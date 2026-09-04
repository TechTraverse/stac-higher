"""The GOES worked example's scripts (G-7, spec §9), tested as plain modules.

The runtime entrypoint exec()s each script with __name__ == "__main__"; here
they are exec'd with another name so their functions can be called on
synthetic data. No network, no container, no netCDF file: the footprint is
checked on a GeoTIFF in the geostationary CRS, which is the property that
made I-101 — a projection the built-in path cannot footprint.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from pipeline.demo.goes import OUTPUT_COLLECTION_TOKEN, extractor_code, geocolor_code

GEOS_CRS = (
    "+proj=geos +lon_0=-75 +h=35786023 +x_0=0 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs +sweep=x"
)


def _load(source: str) -> dict:
    ns: dict = {"__name__": "test_demo_goes"}
    exec(compile(source, "<goes>", "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def extractor():
    return _load(extractor_code())


@pytest.fixture(scope="module")
def geocolor():
    return _load(geocolor_code("goes-geocolor"))


def test_scripts_compile_and_never_open_vsi_paths():
    for source in (extractor_code(), geocolor_code("x")):
        compile(source, "<goes>", "exec")
        # Spec §15: the runtime image's netCDF driver cannot read /vsi paths.
        assert "/vsis3" not in source and "/vsicurl" not in source
    assert OUTPUT_COLLECTION_TOKEN not in geocolor_code("goes-geocolor")
    assert '"goes-geocolor"' in geocolor_code("goes-geocolor")


def test_scan_time_parses_the_filename_token(extractor):
    when = extractor["scan_time"](
        "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
    )
    assert when == dt.datetime(2026, 9, 3, 4, 1, 17, 200_000, tzinfo=dt.UTC)
    with pytest.raises(ValueError):
        extractor["scan_time"]("scene.nc")


def test_footprint_reprojects_a_geostationary_dataset(extractor):
    # A 10x10 tile of 2 km pixels near the sub-satellite point: every corner
    # is on-disk, so the polygon is closed, finite, and around lon -75.
    transform = from_origin(-10_000.0, 10_000.0, 2_000.0, 2_000.0)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((1, 10, 10), dtype="uint16"))
        with mem.open() as ds:
            geometry, bbox = extractor["footprint"](ds)
    ring = geometry["coordinates"][0]
    assert geometry["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) > 20
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in ring)
    assert bbox[0] < -75 < bbox[2] and bbox[1] < 0 < bbox[3]
    assert abs(bbox[0] - bbox[2]) < 1 and abs(bbox[1] - bbox[3]) < 1


def test_compose_daylight_night_and_fill(geocolor):
    # pixel 0: bright day cloud; pixel 1: clear night land (warm); pixel 2:
    # night cold cloud top; pixel 3: fill (off-disk).
    c01 = np.array([[0.8, 0.0, 0.0, np.nan]])
    c02 = np.array([[0.9, 0.0, 0.0, np.nan]])
    c03 = np.array([[0.7, 0.0, 0.0, np.nan]])
    c13 = np.array([[220.0, 300.0, 200.0, np.nan]])
    rgb, mask = geocolor["compose"](c01, c02, c03, c13)
    assert rgb.shape == (3, 1, 4) and rgb.dtype == np.uint8 and mask.dtype == np.uint8
    assert rgb[0, 0, 0] > 200                       # day: red from C02 after gamma
    assert 0 < rgb[0, 0, 1] < 40                    # warm night surface: faint IR
    assert rgb[0, 0, 2] > rgb[0, 0, 1]              # cold cloud brighter than warm land at night
    assert (rgb[:, 0, 3] == 0).all() and mask[0, 3] == 0   # fill: black + masked
    assert (mask[0, :3] == 255).all()


def test_read_band_applies_scale_offset_and_fill(geocolor, tmp_path):
    # A scaled uint16 GeoTIFF stands in for a CMI subdataset.
    transform = from_origin(0, 0, 1, 1)
    path = tmp_path / "band.tif"
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=2, height=1, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform, nodata=65535) as ds:
            ds.write(np.array([[[1000, 65535]]], dtype="uint16"))
            ds.scales = (0.001,)
            ds.offsets = (0.0,)
        path.write_bytes(mem.read())
    values, crs, tr = geocolor["read_band"](str(path), None)
    assert values[0, 0] == pytest.approx(1.0) and np.isnan(values[0, 1])
    assert crs is not None and tr == transform


def test_build_output_item_reuses_the_source_footprint(geocolor, tmp_path):
    # The G-7 live gate's failure (2026-09-03): rio-stac reprojects the
    # raster's bounds itself, and on the real MCMIPC grid GDAL refuses the
    # whole call ("Full reprojection failed") instead of dropping the off-limb
    # corners. Same fixture as the extractor's off-disk test: a full-disk
    # extent whose CORNERS are past the limb.
    transform = from_origin(-5.0e6, 5.0e6, 1.0e6, 1.0e6)
    path = tmp_path / "scene-geocolor.tif"
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=3, dtype="uint8",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((3, 10, 10), dtype="uint8"))
        path.write_bytes(mem.read())

    # The failure mode itself, so this test fails loudly if GDAL ever changes.
    bounds_box = {
        "type": "Polygon",
        "coordinates": [[[-5.0e6, -5.0e6], [5.0e6, -5.0e6], [5.0e6, 5.0e6],
                         [-5.0e6, 5.0e6], [-5.0e6, -5.0e6]]],
    }
    with pytest.raises(Exception):  # noqa: B017 — a CPLE_* error, not a subclass we import
        transform_geom(GEOS_CRS, "EPSG:4326", bounds_box)

    geometry = {
        "type": "Polygon",
        "coordinates": [[[-100.0, 20.0], [-60.0, 20.0], [-60.0, 50.0],
                         [-100.0, 50.0], [-100.0, 20.0]]],
    }
    source_item = {
        "id": "scene",
        "geometry": geometry,
        "bbox": [-100.0, 20.0, -60.0, 50.0],
        "properties": {"platform": "goes-19", "instruments": ["abi"]},
    }
    item = geocolor["build_output_item"](
        str(path),
        out_id="scene-geocolor",
        source_item=source_item,
        when=dt.datetime(2026, 9, 3, 4, 1, 17, tzinfo=dt.UTC),
        visual_filename="scene-geocolor.tif",
    )
    assert item["geometry"] == geometry
    assert item["bbox"] == source_item["bbox"]
    assert item["collection"] == "goes-geocolor"
    assert item["assets"]["visual"]["href"] == "scene-geocolor.tif"
    assert item["links"] == []
    # rio-stac 0.12 describes a CRS with no EPSG code as WKT2.
    assert "proj:wkt2" in item["properties"]
    assert item["properties"]["goes:derived_from"] == "scene"


def test_footprint_drops_off_disk_vertices(extractor):
    # A full-disk extent: the bounding box's CORNERS fall off the Earth's
    # limb (radius 7.1e6 m) while its edge midpoints are on it (5.0e6 m,
    # inside the ~5.43e6 m disk). GDAL 3.12 does not hand the off-limb
    # points back as non-finite — it fails the whole reprojection — so
    # footprint() must survive it and keep the on-disk part of the ring.
    transform = from_origin(-5.0e6, 5.0e6, 1.0e6, 1.0e6)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=10, height=10, count=1, dtype="uint16",
                      crs=GEOS_CRS, transform=transform) as ds:
            ds.write(np.zeros((1, 10, 10), dtype="uint16"))
        with mem.open() as ds:
            geometry, bbox = extractor["footprint"](ds)
    ring = geometry["coordinates"][0]
    assert 3 < len(ring) < 101  # some vertices dropped, some kept
    assert ring[0] == ring[-1]
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in ring)
    assert -180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90


# --------------------------------------------------------------------------- #
# the seeder's config builders (G-7 task 2): the rows `goes-seed` writes have
# to parse with the SAME readers the pipeline uses at runtime, or the loop is
# only discovered to be misconfigured on a live stack.
# --------------------------------------------------------------------------- #


def test_goes_ingest_config_round_trips_both_readers():
    from pipeline.demo.goes.seed import EXTRACTOR_ID, ingest_config
    from pipeline.ingest.config import parse_ingest_config
    from pipeline.ingest.extract import parse_metadata

    cfg = ingest_config(include=("**/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172*.nc",))
    parsed = parse_ingest_config(cfg)
    assert parsed.storage_mode == "reference"
    assert parsed.path_template == "{Y}/{j}/{H}/" and parsed.window_begin == "-1h"
    assert parsed.max_files_per_poll == 2
    assert parsed.include == ("**/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172*.nc",)
    assert parse_metadata(parsed.metadata).extractor_process_id == EXTRACTOR_ID


def test_goes_connection_config_is_anonymous():
    from pipeline.connections.adapters.s3 import parse_s3_config
    from pipeline.demo.goes.seed import nodd_connection_config

    cfg = nodd_connection_config()
    assert cfg["anonymous"] is True and cfg["bucket"] == "noaa-goes19"
    parsed = parse_s3_config(cfg)  # the adapter accepts it without credentials
    assert parsed.anonymous is True and parsed.endpoint is None


def test_goes_deliver_config_round_trips_the_delivery_reader():
    from pipeline.delivery.config import parse_delivery_config
    from pipeline.demo.goes.seed import deliver_config

    parsed = parse_delivery_config(deliver_config())
    assert parsed.path_template == "goes/{item_id}/{filename}"


def test_goes_collection_documents_cover_source_and_output():
    from pipeline.demo.goes.seed import OUTPUT_COLLECTION, SOURCE_COLLECTION, collection_documents

    docs = collection_documents()
    assert [doc["id"] for doc in docs] == [SOURCE_COLLECTION, OUTPUT_COLLECTION]
    for doc in docs:
        assert doc["type"] == "Collection" and doc["description"]
        assert doc["extent"]["spatial"]["bbox"][0][0] < doc["extent"]["spatial"]["bbox"][0][2]


# --------------------------------------------------------------------------- #
# the competing-association guard: `goes-seed` must refuse BEFORE it replaces
# two collection documents, and `goes-teardown` must refuse to delete a
# collection somebody else's association still points at.
#
# The helper's SQL is Pg-only, so the guard is exercised at the CLI level with
# the helper itself stubbed out — what is under test is the refusal ORDER, not
# the query.
# --------------------------------------------------------------------------- #


class _FakeConn:
    """Just enough psycopg to run the seeders' `with connect(...) as conn`."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePaginator:
    def paginate(self, **kwargs):
        return []


class _FakeS3:
    def get_paginator(self, name):
        return _FakePaginator()


def _goes_args(**overrides):
    import argparse as _argparse

    defaults = {
        "database_url": "postgresql://u:p@localhost:5433/postgis",
        "stac_url": "http://stac.invalid",
        "s3_endpoint": "http://s3.invalid",
        "titiler_url": "http://tiler.invalid",
        "bucket": "stac-higher",
        "include": [],
        "window": "-1h",
        "max_files": 2,
        "deliver": False,
        "internal_s3_endpoint": "http://minio:9000",
        "force": False,
    }
    defaults.update(overrides)
    return _argparse.Namespace(**defaults)


@pytest.fixture
def goes_seed_module(monkeypatch):
    from pipeline.demo.goes import seed as module

    conn = _FakeConn()
    # Every seed needs the master key (the anonymous envelope is sealed with it).
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", _SEED_KEY_B64)
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(module, "check_migrations", lambda *a, **k: None)
    monkeypatch.setattr(module, "say", lambda *a, **k: None)
    module._test_conn = conn  # type: ignore[attr-defined]
    return module


def test_goes_seed_refuses_before_it_writes_any_collection(goes_seed_module, monkeypatch):
    module = goes_seed_module
    written: list[tuple] = []
    monkeypatch.setattr(module, "put_collection", lambda *a, **k: written.append(a))
    monkeypatch.setattr(
        module,
        "competing_ingest_associations",
        lambda *a, **k: [("assoc-1", module.SOURCE_COLLECTION, "someone-elses-conn")],
    )

    with pytest.raises(SystemExit) as excinfo:
        module.seed(_goes_args())

    assert "assoc-1" in str(excinfo.value)
    assert written == []  # the collection documents were NOT replaced


def test_goes_teardown_refuses_while_another_association_targets_the_collections(
    goes_seed_module, monkeypatch
):
    module = goes_seed_module
    deleted: list[str] = []
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: deleted.append("process"))
    monkeypatch.setattr(
        module, "request", lambda *a, **k: deleted.append("collection") or (204, "")
    )
    monkeypatch.setattr(module, "s3_client", lambda *a, **k: _FakeS3())
    monkeypatch.setattr(
        module,
        "competing_ingest_associations",
        lambda *a, **k: [("assoc-1", module.SOURCE_COLLECTION, "someone-elses-conn")],
    )

    with pytest.raises(SystemExit) as excinfo:
        module.teardown(_goes_args(force=False))

    assert "--force" in str(excinfo.value)
    assert deleted == []
    assert module._test_conn.statements == []  # not one DELETE was issued


def test_goes_teardown_proceeds_with_force(goes_seed_module, monkeypatch):
    module = goes_seed_module
    deleted: list[str] = []
    monkeypatch.setattr(module, "remove_process", lambda *a, **k: deleted.append("process"))
    monkeypatch.setattr(
        module, "request", lambda *a, **k: deleted.append("collection") or (204, "")
    )
    monkeypatch.setattr(module, "s3_client", lambda *a, **k: _FakeS3())
    monkeypatch.setattr(
        module,
        "competing_ingest_associations",
        lambda *a, **k: [("assoc-1", module.SOURCE_COLLECTION, "someone-elses-conn")],
    )

    assert module.teardown(_goes_args(force=True)) == 0
    assert deleted.count("process") == 2 and deleted.count("collection") == 2
    assert module._test_conn.statements  # the DELETEs did run


def test_goes_teardown_subparser_takes_force(monkeypatch):
    """The CLI has to be able to SAY --force, or the escape hatch is unreachable."""
    from pipeline.demo import __main__ as demo_main

    seen: list[bool] = []
    monkeypatch.setattr(
        demo_main.goes, "teardown", lambda args: seen.append(args.force) or 0
    )
    assert demo_main.main(["goes-teardown", "--force"]) == 0
    assert demo_main.main(["goes-teardown"]) == 0
    assert seen == [True, False]


_SEED_KEY_B64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def _stub_seed_writes(module, monkeypatch, connections: list[dict]):
    """Silence every write in `seed` except the connection upsert, which is
    captured so the test can inspect what the NODD connection was stored with."""
    monkeypatch.setattr(module, "competing_ingest_associations", lambda *a, **k: [])
    monkeypatch.setattr(module, "put_collection", lambda *a, **k: None)
    monkeypatch.setattr(module, "enable_serving", lambda *a, **k: None)
    monkeypatch.setattr(module, "install_process", lambda *a, **k: None)
    monkeypatch.setattr(module, "upsert_association", lambda *a, **k: "assoc")
    monkeypatch.setattr(
        module,
        "upsert_connection",
        lambda *a, **k: connections.append(k) or "conn",
    )


def test_goes_seed_stores_an_empty_envelope_for_the_anonymous_connection(
    goes_seed_module, monkeypatch
):
    """The app stores an ENCRYPTED `{}` for an anonymous s3 connection because
    the pipeline's build_adapter treats a NULL credentials column as a
    configuration error. The seed must write the same shape, or discover fails
    on every poll with "connection has no stored credentials"."""
    import json

    from pipeline.connections.envelope import decrypt, load_master_key

    module = goes_seed_module
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", _SEED_KEY_B64)
    connections: list[dict] = []
    _stub_seed_writes(module, monkeypatch, connections)

    assert module.seed(_goes_args()) == 0

    nodd = next(c for c in connections if c["name"] == module.CONNECTION_NAME)
    assert nodd["credentials"] is not None
    key = load_master_key({"CREDENTIALS_MASTER_KEY": _SEED_KEY_B64})
    assert json.loads(decrypt(nodd["credentials"], key)) == {}


def test_goes_seed_requires_the_master_key_before_any_write(goes_seed_module, monkeypatch):
    """The key is needed for the ingest leg too (the anonymous envelope), not
    just for `--deliver`, so its absence must stop the seed before the catalog
    is touched."""
    from pipeline.connections.envelope import CredentialKeyError

    module = goes_seed_module
    monkeypatch.delenv("CREDENTIALS_MASTER_KEY", raising=False)
    written: list[tuple] = []
    monkeypatch.setattr(module, "competing_ingest_associations", lambda *a, **k: [])
    monkeypatch.setattr(module, "put_collection", lambda *a, **k: written.append(a))

    with pytest.raises(CredentialKeyError):
        module.seed(_goes_args())

    assert written == []
