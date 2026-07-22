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
