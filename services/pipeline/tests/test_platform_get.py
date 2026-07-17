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
