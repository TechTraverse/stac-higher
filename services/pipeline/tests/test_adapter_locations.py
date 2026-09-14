"""S3Adapter hands EXTRACT a /vsis3 location and FETCH a copy source (M3-C)."""

from __future__ import annotations

import io

import pytest

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.adapters.s3 import S3Adapter
from pipeline.ingest.raster_io import RasterLocation

ALLOW = frozenset({"minio"})
CREDS = {"access_key_id": "AK", "secret_access_key": "SK"}


def _adapter(**cfg) -> S3Adapter:
    return S3Adapter({"bucket": "src", **cfg}, CREDS, allow_hosts=ALLOW)


def test_gdal_location_for_a_custom_http_endpoint_is_path_style_plaintext(monkeypatch):
    adapter = _adapter(endpoint="http://minio:9000", force_path_style=True)
    monkeypatch.setattr(adapter, "_pinned_endpoint", lambda: "http://10.0.0.5:9000")
    loc = adapter.gdal_location("scenes/a.tif", options={"GDAL_CACHEMAX": "64"})
    assert isinstance(loc, RasterLocation)
    assert loc.uri == "/vsis3/src/scenes/a.tif"
    assert loc.options["AWS_HTTPS"] == "NO"
    assert loc.options["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert loc.options["GDAL_CACHEMAX"] == "64"
    # the session dials the PINNED host:port, scheme stripped — rasterio's AWSSession form
    session = loc.session
    from rasterio.session import AWSSession

    assert isinstance(session, AWSSession)
    assert session.credentials["aws_access_key_id"] == "AK"
    assert session.credentials["aws_secret_access_key"] == "SK"
    assert session.endpoint_url == "10.0.0.5:9000"
    assert "SK" not in repr(loc)


def test_gdal_location_on_real_aws_keeps_https_and_virtual_hosting():
    adapter = _adapter(region="us-east-1")
    loc = adapter.gdal_location("scenes/a.tif")
    assert loc.uri == "/vsis3/src/scenes/a.tif"
    assert "AWS_HTTPS" not in loc.options
    assert "AWS_VIRTUAL_HOSTING" not in loc.options


def test_gdal_location_for_an_anonymous_bucket_is_unsigned():
    adapter = S3Adapter({"bucket": "noaa-goes19", "anonymous": True}, {}, allow_hosts=ALLOW)
    loc = adapter.gdal_location("ABI/a.nc")
    assert loc.session.unsigned is True


def test_copy_source_and_endpoint():
    adapter = _adapter(endpoint="http://minio:9000")
    assert adapter.copy_source("scenes/a.tif") == ("src", "scenes/a.tif")
    assert adapter.endpoint == "http://minio:9000"
    assert _adapter().endpoint is None


def test_base_adapter_defaults_keep_sftp_ftp_on_the_buffered_path():
    class Buffered(StorageAdapter):
        protocol = "sftp"

        async def test(self):  # pragma: no cover
            return {"ok": True}

        async def list(self, prefix=""):  # pragma: no cover
            return []

        async def get(self, path):
            return b"bytes-" + path.encode()

        async def put(self, path, data):  # pragma: no cover
            pass

        async def delete(self, path):  # pragma: no cover
            pass

        async def move(self, src, dst):  # pragma: no cover
            pass

    adapter = Buffered()
    assert adapter.endpoint is None
    assert adapter.copy_source("x") is None
    with pytest.raises(NotImplementedError):
        adapter.gdal_location("x")


async def test_base_open_is_the_buffered_get():
    class Buffered(StorageAdapter):
        protocol = "ftp"

        async def test(self):  # pragma: no cover
            return {"ok": True}

        async def list(self, prefix=""):  # pragma: no cover
            return []

        async def get(self, path):
            return b"payload"

        async def put(self, path, data):  # pragma: no cover
            pass

        async def delete(self, path):  # pragma: no cover
            pass

        async def move(self, src, dst):  # pragma: no cover
            pass

    stream = await Buffered().open("x")
    assert isinstance(stream, io.BytesIO) and stream.read() == b"payload"
