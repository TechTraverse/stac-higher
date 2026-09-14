"""Platform get_object read primitive (ingest EXTRACT)."""

from __future__ import annotations

import io

from pipeline.storage.platform import get_object


class _FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def test_get_object_reads_body_bytes():
    client = _FakeS3({("bucket", "assets/c/i/f.tif"): b"RAWBYTES"})
    assert get_object(client, "bucket", "assets/c/i/f.tif") == b"RAWBYTES"


def test_head_object_returns_stripped_etag_and_size():
    from pipeline.storage.platform import head_object

    class _Client:
        def head_object(self, Bucket, Key):  # boto3 kwarg names (not enabled: N803)
            assert (Bucket, Key) == ("bucket", "assets/col/scene/a.tif")
            return {"ETag": '"d41d8cd98f00b204e9800998ecf8427e"', "ContentLength": 9}

    etag, size = head_object(_Client(), "bucket", "assets/col/scene/a.tif")
    assert etag == "d41d8cd98f00b204e9800998ecf8427e"
    assert size == 9


def test_platform_s3_access_pins_the_endpoint_and_raster_location_is_vsis3():
    from pipeline.config import Settings
    from pipeline.storage.platform import platform_s3_access, raster_location

    settings = Settings.from_env(
        env={
            "STAGING_S3_ENDPOINT": "http://minio:9000",
            "STAGING_S3_ACCESS_KEY_ID": "AK",
            "STAGING_S3_SECRET_ACCESS_KEY": "SK",
            "EGRESS_ALLOW_HOSTS": "minio",
        }
    )
    access = platform_s3_access(settings)
    assert access.endpoint_url is not None and access.endpoint_url.endswith(":9000")
    assert access.force_path_style is True
    loc = raster_location(access, "stac-higher", "assets/c/i/scene.tif", gdal_cachemax_mb=64)
    assert loc.uri == "/vsis3/stac-higher/assets/c/i/scene.tif"
    assert loc.options == {"AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE", "GDAL_CACHEMAX": "64"}
    assert loc.session.get_credential_options()["AWS_SECRET_ACCESS_KEY"] == "SK"


def test_gdal_endpoint_strips_the_scheme():
    from pipeline.storage.platform import gdal_endpoint

    assert gdal_endpoint("http://10.0.0.5:9000") == "10.0.0.5:9000"
    assert gdal_endpoint("https://s3.example.com") == "s3.example.com"
    assert gdal_endpoint(None) is None
