"""Canonical asset key builder + platform put_object primitive."""

from __future__ import annotations

import pytest

from pipeline.storage import platform
from pipeline.storage.keys import InvalidKeySegment, asset_href, canonical_asset_key


def test_canonical_asset_key_layout():
    assert (
        canonical_asset_key("sentinel-2", "S2A_2026", "scene.tif")
        == "assets/sentinel-2/S2A_2026/scene.tif"
    )


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b"])
def test_canonical_asset_key_rejects_traversal(bad):
    with pytest.raises(InvalidKeySegment):
        canonical_asset_key(bad, "item", "f.tif")
    with pytest.raises(InvalidKeySegment):
        canonical_asset_key("coll", bad, "f.tif")
    with pytest.raises(InvalidKeySegment):
        canonical_asset_key("coll", "item", bad)


class _CaptureS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_put_object_passes_bytes_and_key():
    client = _CaptureS3()
    platform.put_object(client, "bucket", "assets/c/i/f.tif", b"xyz")
    assert client.calls == [{"Bucket": "bucket", "Key": "assets/c/i/f.tif", "Body": b"xyz"}]


def test_put_object_sets_content_type_when_given():
    client = _CaptureS3()
    platform.put_object(client, "b", "k", b"1", content_type="image/tiff")
    assert client.calls[0]["ContentType"] == "image/tiff"


def test_asset_href_is_root_relative_and_encoded():
    assert (
        asset_href("col lection", "item/id", "a b.tif")
        == "/api/assets/col%20lection/item%2Fid/a%20b.tif"
    )


def test_asset_href_respects_custom_base():
    assert asset_href("c", "i", "f.tif", base="/assets") == "/assets/c/i/f.tif"
