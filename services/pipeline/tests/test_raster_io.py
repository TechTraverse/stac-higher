"""RasterLocation + open_raster (M3-C): a raster is opened where it lives."""

from __future__ import annotations

import pytest

from pipeline.ingest.raster_io import VSICURL_TUNING, RasterLocation, env_kwargs_for, open_raster
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
    loc = RasterLocation(
        uri=str(path), options={"AWS_HTTPS": "NO", "GDAL_CACHEMAX": "7"}
    )
    with open_raster(loc):
        env = rasterio.env.getenv()
        # rasterio routes GDAL_CACHEMAX through GDALSetCacheMax64 (unlike
        # every other config key), which requires an int and takes bytes,
        # while the option (like Settings.gdal_cachemax_mb) is in MB.
        assert env.get("GDAL_CACHEMAX") == 7 * 1024 * 1024
        # Every other key passes through the coercion untouched.
        assert env.get("AWS_HTTPS") == "NO"


def test_open_raster_rejects_unknown_sources():
    with pytest.raises(TypeError), open_raster(123):  # type: ignore[arg-type]
        pass


def test_env_kwargs_for_applies_vsicurl_tuning_to_vsi_uris():
    loc = RasterLocation(uri="/vsis3/b/k.tif", options={"GDAL_CACHEMAX": "7"})

    result = env_kwargs_for(loc)

    for key, value in VSICURL_TUNING.items():
        assert result[key] == value
    assert result["GDAL_CACHEMAX"] == 7 * 1024 * 1024


def test_env_kwargs_for_lets_location_options_override_the_tuning():
    loc = RasterLocation(uri="/vsis3/b/k.tif", options={"CPL_VSIL_CURL_CHUNK_SIZE": "65536"})

    result = env_kwargs_for(loc)

    assert result["CPL_VSIL_CURL_CHUNK_SIZE"] == "65536"
    for key, value in VSICURL_TUNING.items():
        if key != "CPL_VSIL_CURL_CHUNK_SIZE":
            assert result[key] == value


def test_env_kwargs_for_leaves_local_paths_untuned(tmp_path):
    loc = RasterLocation(uri=str(tmp_path / "x.tif"))

    result = env_kwargs_for(loc)

    for key in VSICURL_TUNING:
        assert key not in result


def test_env_combination_matches_production_s3_reads():
    # The exact rasterio.Env(session=..., AWS_HTTPS=..., AWS_VIRTUAL_HOSTING=...,
    # GDAL_CACHEMAX=..., plus the I-129 vsicurl tuning) combination `open_raster`
    # enters for a platform /vsis3 read (M3-C final review finding 3). No
    # network: an Env is entered over env_kwargs_for()'s output directly,
    # nothing is opened.
    import rasterio

    from pipeline.storage.platform import PlatformS3Access, raster_location

    access = PlatformS3Access(
        endpoint_url="http://minio:9000",
        region="us-east-1",
        access_key="AK",
        secret_key="SK",
        force_path_style=True,
    )
    loc = raster_location(access, "bucket", "key.tif", gdal_cachemax_mb=64)

    with rasterio.Env(**env_kwargs_for(loc)):
        env = rasterio.env.getenv()
        assert env.get("AWS_S3_ENDPOINT") == "minio:9000"
        assert env.get("AWS_HTTPS") == "NO"
        assert env.get("AWS_VIRTUAL_HOSTING") == "FALSE"
        assert env.get("GDAL_CACHEMAX") == 67108864
        assert env.get("GDAL_DISABLE_READDIR_ON_OPEN") == "EMPTY_DIR"
        assert env.get("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES") == "YES"
        assert env.get("VSI_CACHE") == "TRUE"
        assert env.get("VSI_CACHE_SIZE") == "16777216"
        assert env.get("CPL_VSIL_CURL_CHUNK_SIZE") == "1048576"
