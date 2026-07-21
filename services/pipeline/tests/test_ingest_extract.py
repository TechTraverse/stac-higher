import datetime as dt
import io
import json

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from pipeline.ingest.extract import (
    CanonicalByteSource,
    ExtractError,
    ExtractMember,
    bbox_from_geometry,
    bbox_to_polygon,
    build_defaults_only,
    build_item,
    build_raster_auto,
    build_sidecar,
    geometry_from_raster,
    is_gdal_candidate,
    media_type_for,
    parse_metadata,
    parse_sidecar,
    resolve_datetime,
)
from pipeline.ingest.itemize import validate_item


def _member(name="scene.tif", observed=None):
    return ExtractMember(
        source_path=f"products/{name}",
        filename=name,
        canonical_key=f"assets/col/scene/{name}",
        observed_at=observed,
    )


def test_parse_metadata_defaults():
    cfg = parse_metadata({})
    assert cfg.strategy == "raster_auto"
    assert cfg.sidecar_parser == "generic_xml"
    assert cfg.default_datetime is None


def test_parse_metadata_sidecar_and_defaults():
    cfg = parse_metadata(
        {
            "strategy": "sidecar",
            "sidecar": {"pattern": "{basename}.xml", "parser": "json"},
            "defaults": {"datetime": "file_mtime"},
        }
    )
    assert cfg.strategy == "sidecar"
    assert cfg.sidecar_pattern == "{basename}.xml"
    assert cfg.sidecar_parser == "json"
    assert cfg.default_datetime == "file_mtime"


def test_parse_metadata_default_geometry_absent_is_none():
    cfg = parse_metadata({})
    assert cfg.default_geometry is None


def test_parse_metadata_default_geometry_collection():
    cfg = parse_metadata({"defaults": {"geometry": "collection"}})
    assert cfg.default_geometry == "collection"


def test_parse_metadata_default_geometry_unknown_value_ignored():
    cfg = parse_metadata({"defaults": {"geometry": "nonsense"}})
    assert cfg.default_geometry is None


def test_is_gdal_candidate_raster_and_gridded_true():
    for name in ("scene.tif", "scene.nc", "scene.grib"):
        assert is_gdal_candidate(name) is True


def test_is_gdal_candidate_non_raster_false():
    for name in ("scene.bin", "scene.txt"):
        assert is_gdal_candidate(name) is False


def test_media_type_for_known_and_unknown():
    assert media_type_for("a.tif") == "image/tiff; application=geotiff"
    assert media_type_for("a.TIFF") == "image/tiff; application=geotiff"
    assert media_type_for("a.bin") == "application/octet-stream"


def test_resolve_datetime_prefers_extracted():
    cfg = parse_metadata({})
    got = resolve_datetime(dt.datetime(2020, 1, 1, tzinfo=dt.UTC), cfg, _member())
    assert got == dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


def test_resolve_datetime_literal_default():
    cfg = parse_metadata({"defaults": {"datetime": "2021-06-01T00:00:00Z"}})
    got = resolve_datetime(None, cfg, _member())
    assert got == dt.datetime(2021, 6, 1, tzinfo=dt.UTC)


def test_resolve_datetime_file_mtime_uses_observed():
    cfg = parse_metadata({"defaults": {"datetime": "file_mtime"}})
    observed = dt.datetime(2022, 3, 3, tzinfo=dt.UTC)
    got = resolve_datetime(None, cfg, _member(observed=observed))
    assert got == observed


def test_resolve_datetime_unresolvable_raises():
    cfg = parse_metadata({})
    with pytest.raises(ExtractError):
        resolve_datetime(None, cfg, _member(observed=None))


def test_build_defaults_only_null_geometry_item():
    cfg = parse_metadata(
        {"strategy": "defaults_only", "defaults": {"datetime": "2021-06-01T00:00:00Z"}}
    )
    item = build_defaults_only("col", "scene", [_member()], cfg, "/api/assets")
    assert item["id"] == "scene"
    assert item["collection"] == "col"
    assert item["geometry"] is None
    assert "bbox" not in item
    assert item["properties"]["datetime"] == "2021-06-01T00:00:00Z"
    assert item["assets"]["scene"]["href"] == "/api/assets/col/scene/scene.tif"
    assert item["assets"]["scene"]["type"] == "image/tiff; application=geotiff"


_XML = b"""<?xml version="1.0"?>
<product><acquired>2023-05-05T10:00:00Z</acquired></product>"""

_XXE = b"""<?xml version="1.0"?>
<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<product><acquired>2023-05-05T10:00:00Z</acquired>&x;</product>"""


def test_parse_sidecar_json_datetime():
    out = parse_sidecar(b'{"datetime": "2023-05-05T10:00:00Z"}', "json")
    assert out["datetime"] == dt.datetime(2023, 5, 5, 10, tzinfo=dt.UTC)


def test_parse_sidecar_generic_xml_datetime():
    out = parse_sidecar(_XML, "generic_xml")
    assert out["datetime"] == dt.datetime(2023, 5, 5, 10, tzinfo=dt.UTC)


def test_parse_sidecar_xml_is_xxe_safe():
    # defusedxml forbids DOCTYPE entities → parse is blocked, surfaced as
    # ExtractError. The local file is never read.
    with pytest.raises(ExtractError):
        parse_sidecar(_XXE, "generic_xml")


def test_build_sidecar_uses_extracted_datetime():
    members = [
        _member("scene.tif"),
        ExtractMember("products/scene.xml", "scene.xml", "assets/col/scene/scene.xml", None),
    ]
    cfg = parse_metadata({"strategy": "sidecar", "sidecar": {"pattern": "{basename}.xml"}})
    item = build_sidecar("col", "scene", members, cfg, _XML, "/api/assets")
    assert item["properties"]["datetime"] == "2023-05-05T10:00:00Z"
    assert set(item["assets"]) == {"scene"}  # both members, keyed by stem
    assert item["assets"]["scene"]["roles"] == ["data"]


def test_bbox_from_geometry_polygon():
    geom = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]]],
    }
    assert bbox_from_geometry(geom) == [0, 0, 2, 3]


def test_bbox_from_geometry_point():
    geom = {"type": "Point", "coordinates": [5, 7]}
    assert bbox_from_geometry(geom) == [5, 7, 5, 7]


def test_bbox_from_geometry_geometry_collection_raises():
    geom = {"type": "GeometryCollection", "geometries": []}
    with pytest.raises(ExtractError):
        bbox_from_geometry(geom)


def test_build_sidecar_with_geometry_sets_bbox_and_validates():
    members = [
        ExtractMember("products/scene.json", "scene.json", "assets/col/scene/scene.json", None),
    ]
    cfg = parse_metadata(
        {"strategy": "sidecar", "sidecar": {"pattern": "{basename}.json", "parser": "json"}}
    )
    sidecar_bytes = json.dumps(
        {
            "datetime": "2023-05-05T10:00:00Z",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]]],
            },
        }
    ).encode()
    item = build_sidecar("col", "scene", members, cfg, sidecar_bytes, "/api/assets")
    assert item["bbox"] == [0, 0, 2, 3]
    assert item["properties"]["stac_higher:geometry_source"] == "sidecar"
    validate_item(item)  # must not raise ("bbox is required if geometry is not null")


def test_build_sidecar_null_geometry_still_has_no_bbox():
    members = [
        ExtractMember("products/scene.json", "scene.json", "assets/col/scene/scene.json", None),
    ]
    cfg = parse_metadata(
        {"strategy": "sidecar", "sidecar": {"pattern": "{basename}.json", "parser": "json"}}
    )
    sidecar_bytes = json.dumps({"datetime": "2023-05-05T10:00:00Z"}).encode()
    item = build_sidecar("col", "scene", members, cfg, sidecar_bytes, "/api/assets")
    assert item["geometry"] is None
    assert "bbox" not in item
    validate_item(item)


def _geotiff_bytes():
    arr = np.arange(16, dtype="uint8").reshape(1, 4, 4)
    transform = from_bounds(-1, -1, 1, 1, 4, 4)
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff", height=4, width=4, count=1, dtype="uint8",
            crs="EPSG:4326", transform=transform,
        ) as ds:
            ds.write(arr)
        return mem.read()


def _netcdf_bytes():
    """A single-variable netCDF built via CreateCopy from an in-memory GTiff
    (netCDF is blacklisted for `Create`/write but supports CreateCopy)."""
    import os
    import tempfile

    import rasterio.shutil as rio_shutil

    arr = np.arange(16, dtype="uint8").reshape(1, 4, 4)
    transform = from_bounds(-1, -1, 1, 1, 4, 4)
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff", height=4, width=4, count=1, dtype="uint8",
            crs="EPSG:4326", transform=transform,
        ) as ds:
            ds.write(arr)
        with mem.open() as src, tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "out.nc")
            rio_shutil.copy(src, path, driver="netCDF")
            with open(path, "rb") as f:
                return f.read()


def test_bbox_to_polygon():
    assert bbox_to_polygon([0, 0, 2, 3]) == {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]]],
    }


def test_geometry_from_raster_gtiff_returns_geometry_and_bbox():
    result = geometry_from_raster(_geotiff_bytes())
    assert result is not None
    geometry, bbox = result
    assert geometry["type"] == "Polygon"
    assert bbox == pytest.approx([-1, -1, 1, 1])


def test_geometry_from_raster_netcdf_returns_geometry_and_bbox():
    result = geometry_from_raster(_netcdf_bytes())
    assert result is not None
    geometry, bbox = result
    assert geometry["type"] == "Polygon"
    assert bbox == pytest.approx([-1, -1, 1, 1])


def test_geometry_from_raster_non_raster_bytes_returns_none():
    assert geometry_from_raster(b"not a raster") is None


class _FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def test_build_raster_auto_sets_geometry_and_href():
    members = [_member("scene.tif")]
    cfg = parse_metadata({"strategy": "raster_auto"})
    item = build_raster_auto("col", "scene", members, cfg, _geotiff_bytes(), "/api/assets")
    assert item["id"] == "scene"
    assert item["collection"] == "col"
    assert item["geometry"] is not None and item["geometry"]["type"] == "Polygon"
    assert item["bbox"] is not None
    assert item["assets"]["scene"]["href"] == "/api/assets/col/scene/scene.tif"
    assert "proj:epsg" in item["properties"] or "proj:code" in item["properties"]


def test_build_raster_auto_sidecar_does_not_clobber_primary_asset():
    # A sidecar sharing the primary's stem (scene.xml next to scene.tif) must
    # not overwrite the data asset create_stac_item already added under that
    # key — the data asset wins on stem collision, same as build_assets.
    members = [
        _member("scene.tif"),
        ExtractMember("products/scene.xml", "scene.xml", "assets/col/scene/scene.xml", None),
    ]
    cfg = parse_metadata({"strategy": "raster_auto"})
    item = build_raster_auto("col", "scene", members, cfg, _geotiff_bytes(), "/api/assets")
    assert set(item["assets"]) == {"scene"}
    assert item["assets"]["scene"]["roles"] == ["data"]
    assert item["assets"]["scene"]["href"] == "/api/assets/col/scene/scene.tif"


async def test_build_item_dispatches_raster_auto_reads_from_storage():
    members = [_member("scene.tif")]
    s3 = _FakeS3({("bucket", "assets/col/scene/scene.tif"): _geotiff_bytes()})
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "raster_auto"}, byte_source=CanonicalByteSource(s3, "bucket"),
        asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None


async def test_build_item_defaults_only_non_candidate_reads_nothing_and_raises():
    # scene.bin is not a GDAL-candidate extension: no best-effort read is
    # attempted, and with no collection_fallback opted in, this is now a
    # fail-fast ExtractError (I-27 — pgstac rejects null-geometry items).
    members = [_member("scene.bin")]
    s3 = _FakeS3({})  # no objects — a non-candidate primary must not read
    with pytest.raises(ExtractError):
        await build_item(
            collection_id="col", item_id="scene", members=members,
            metadata={
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z"},
            },
            byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
        )


async def test_build_item_defaults_only_recovers_geometry_from_gdal_candidate():
    # scene.tif IS a GDAL-candidate extension: defaults_only now attempts a
    # best-effort read even though the strategy itself extracts nothing.
    members = [_member("scene.tif")]
    s3 = _FakeS3({("bucket", "assets/col/scene/scene.tif"): _geotiff_bytes()})
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "defaults_only", "defaults": {"datetime": "2021-01-01T00:00:00Z"}},
        byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None
    assert item["bbox"] is not None
    assert item["properties"]["stac_higher:geometry_source"] == "raster"


async def test_build_item_defaults_only_collection_fallback_used():
    members = [_member("scene.bin")]
    s3 = _FakeS3({})
    fallback = {
        "geometry": bbox_to_polygon([10, 20, 30, 40]),
        "bbox": [10, 20, 30, 40],
        "source": "collection_extent",
    }
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "defaults_only", "defaults": {"datetime": "2021-01-01T00:00:00Z"}},
        byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
        collection_fallback=fallback,
    )
    assert item["geometry"] == fallback["geometry"]
    assert item["bbox"] == [10, 20, 30, 40]
    assert item["properties"]["stac_higher:geometry_source"] == "collection_extent"


async def test_build_item_global_fallback_used():
    members = [_member("scene.bin")]
    s3 = _FakeS3({})
    fallback = {
        "geometry": bbox_to_polygon([-180, -90, 180, 90]),
        "bbox": [-180, -90, 180, 90],
        "source": "global_fallback",
    }
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "defaults_only", "defaults": {"datetime": "2021-01-01T00:00:00Z"}},
        byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
        collection_fallback=fallback,
    )
    assert item["properties"]["stac_higher:geometry_source"] == "global_fallback"


async def test_build_item_no_fallback_raises_extract_error():
    members = [_member("scene.bin")]
    s3 = _FakeS3({})
    with pytest.raises(ExtractError):
        await build_item(
            collection_id="col", item_id="scene", members=members,
            metadata={
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z"},
            },
            byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
        )


async def test_build_item_reference_reads_from_adapter():
    from _ingest_fake import FakeAdapter
    from pipeline.ingest.extract import SourceAdapterByteSource

    members = [_member("scene.tif")]
    # canonical_key on the member is "assets/col/scene/scene.tif"; reference
    # mode must NOT use it — it reads the source via the adapter instead.
    # _member()'s source_path already carries the "products/" prefix, so the
    # configured source_path is "" (source_fetch_path is then a no-op passthrough).
    adapter = FakeAdapter(blobs={"products/scene.tif": _geotiff_bytes()})
    byte_source = SourceAdapterByteSource(adapter, "")
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "raster_auto"}, byte_source=byte_source,
        asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None
    assert adapter.get_calls == ["products/scene.tif"]


async def test_build_item_dispatches_sidecar_reads_sidecar_bytes():
    # The generic_xml sidecar parser never yields a geometry, so this exercises
    # the ISSUE I-27 best-effort fallback too: scene.tif (the group's raster
    # member, already in storage from FETCH) recovers a geometry.
    members = [
        _member("scene.tif"),
        ExtractMember("products/scene.xml", "scene.xml", "assets/col/scene/scene.xml", None),
    ]
    s3 = _FakeS3(
        {
            ("bucket", "assets/col/scene/scene.xml"): _XML,
            ("bucket", "assets/col/scene/scene.tif"): _geotiff_bytes(),
        }
    )
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "sidecar", "sidecar": {"pattern": "{basename}.xml"}},
        byte_source=CanonicalByteSource(s3, "bucket"), asset_href_base="/api/assets",
    )
    assert item["properties"]["datetime"] == "2023-05-05T10:00:00Z"
    assert item["geometry"] is not None
    assert item["properties"]["stac_higher:geometry_source"] == "raster"
