import datetime as dt
import io
import json

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from pipeline.ingest.extract import (
    ExtractError,
    ExtractMember,
    bbox_from_geometry,
    build_defaults_only,
    build_item,
    build_raster_auto,
    build_sidecar,
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
        metadata={"strategy": "raster_auto"}, s3_client=s3, bucket="bucket",
        asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None


async def test_build_item_defaults_only_reads_nothing():
    members = [_member("scene.bin")]
    s3 = _FakeS3({})  # no objects — defaults_only must not read
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "defaults_only", "defaults": {"datetime": "2021-01-01T00:00:00Z"}},
        s3_client=s3, bucket="bucket", asset_href_base="/api/assets",
    )
    assert item["geometry"] is None


async def test_build_item_dispatches_sidecar_reads_sidecar_bytes():
    members = [
        _member("scene.tif"),
        ExtractMember("products/scene.xml", "scene.xml", "assets/col/scene/scene.xml", None),
    ]
    s3 = _FakeS3({("bucket", "assets/col/scene/scene.xml"): _XML})
    item = await build_item(
        collection_id="col", item_id="scene", members=members,
        metadata={"strategy": "sidecar", "sidecar": {"pattern": "{basename}.xml"}},
        s3_client=s3, bucket="bucket", asset_href_base="/api/assets",
    )
    assert item["properties"]["datetime"] == "2023-05-05T10:00:00Z"
