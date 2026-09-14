"""RasterLocation + open_raster (M3-C): a raster is opened where it lives."""

from __future__ import annotations

import pytest

from pipeline.ingest.raster_io import RasterLocation, open_raster
from test_ingest_extract import _geotiff_bytes  # the existing in-memory GeoTIFF fixture


def test_repr_never_shows_the_session():
    loc = RasterLocation(uri="/vsis3/b/k", session=object(), options={"AWS_HTTPS": "NO"})
    assert repr(loc) == "RasterLocation('/vsis3/b/k')"


def test_open_raster_opens_bytes_and_a_location_to_the_same_dataset(tmp_path):
    data = _geotiff_bytes()
    path = tmp_path / "scene.tif"
    path.write_bytes(data)
    with open_raster(data) as from_bytes, open_raster(RasterLocation(uri=str(path))) as from_path:
        assert from_bytes.bounds == from_path.bounds
        assert from_bytes.crs == from_path.crs
        assert from_bytes.count == from_path.count


def test_open_raster_applies_gdal_options_inside_the_env(tmp_path):
    import rasterio

    path = tmp_path / "scene.tif"
    path.write_bytes(_geotiff_bytes())
    loc = RasterLocation(uri=str(path), options={"GDAL_CACHEMAX": "7"})
    with open_raster(loc):
        # rasterio routes GDAL_CACHEMAX through GDALSetCacheMax64 (unlike
        # every other config key) and requires/stores it as an int.
        assert rasterio.env.getenv().get("GDAL_CACHEMAX") == 7


def test_open_raster_rejects_unknown_sources():
    with pytest.raises(TypeError), open_raster(123):  # type: ignore[arg-type]
        pass
