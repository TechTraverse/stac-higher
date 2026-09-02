"""Both upstream readers rewrite canonical hrefs after `main` is imported.

Needs titiler.pgstac (heavy); skipped when it is absent so the pure tests
still run anywhere. Locally: `uv run --extra dev --extra titiler pytest`.
"""

import importlib

import pytest

pytest.importorskip("titiler.pgstac")


@pytest.fixture(scope="module")
def patched(monkeypatch_module):
    monkeypatch_module.setenv("PLATFORM_ASSET_BUCKET", "stac-higher")
    monkeypatch_module.setenv("ASSET_HREF_BASE", "/api/assets")
    # Importing main patches the classes and imports the upstream app, which
    # needs no database at import time.
    return importlib.import_module("stac_higher_titiler.main")


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_simple_reader_maps_canonical_href(patched):
    from titiler.pgstac.reader import SimpleSTACReader

    item = {
        "id": "i1",
        "collection": "c",
        "bbox": [-10, -10, 10, 10],
        "assets": {"visual": {"href": "/api/assets/c/i1/visual.tif", "type": "image/tiff"}},
    }
    with SimpleSTACReader(item) as src:
        assert src._get_asset_info("visual")["url"] == "s3://stac-higher/assets/c/i1/visual.tif"


def test_simple_reader_leaves_absolute_href_alone(patched):
    from titiler.pgstac.reader import SimpleSTACReader

    href = "https://noaa-goes19.s3.amazonaws.com/x.nc"
    item = {"id": "i1", "collection": "c", "bbox": [0, 0, 1, 1], "assets": {"d": {"href": href}}}
    with SimpleSTACReader(item) as src:
        assert src._get_asset_info("d")["url"] == href


def test_pgstac_item_reader_maps_canonical_href(patched):
    import pystac
    from titiler.pgstac.reader import PgSTACReader

    item = pystac.Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "i1",
            "collection": "c",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "bbox": [0, 0, 1, 1],
            "properties": {"datetime": "2026-01-01T00:00:00Z"},
            "links": [],
            "assets": {"visual": {"href": "/api/assets/c/i1/visual.tif", "type": "image/tiff"}},
        }
    )
    with PgSTACReader(item) as src:
        assert src._get_asset_info("visual")["url"] == "s3://stac-higher/assets/c/i1/visual.tif"


def test_missing_bucket_env_fails_at_import(monkeypatch):
    monkeypatch.delenv("PLATFORM_ASSET_BUCKET", raising=False)
    import sys

    sys.modules.pop("stac_higher_titiler.main", None)
    with pytest.raises(RuntimeError, match="PLATFORM_ASSET_BUCKET"):
        importlib.import_module("stac_higher_titiler.main")
