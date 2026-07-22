import json

from pipeline.delivery.payload import (
    checksum_payload,
    completion_payload,
    item_json_payload,
)


def test_item_json_payload_named_by_item_id_and_verbatim():
    item = {
        "id": "scene",
        "collection": "col",
        "assets": {"a": {"href": "/api/assets/col/scene/a.tif"}},
    }
    name, body = item_json_payload(item)
    assert name == "scene.json"
    assert json.loads(body) == item
    assert body.endswith(b"\n")


def test_checksum_payload_coreutils_format():
    name, body = checksum_payload("a.tif", "sha256", "deadbeef")
    assert name == "a.tif.sha256"
    # `sha256sum -c`-compatible: two spaces between digest and filename.
    assert body == b"deadbeef  a.tif\n"


def test_completion_payload_lists_assets_sorted_by_key():
    delivered = {
        "b": {"fingerprint": "sha256:2", "size": 2, "filename": "b.tif"},
        "a": {"fingerprint": "sha256:1", "size": 1, "filename": "a.tif"},
    }
    name, body = completion_payload("scene", delivered)
    assert name == "scene.done"
    manifest = json.loads(body)
    assert manifest["item_id"] == "scene"
    assert [e["key"] for e in manifest["assets"]] == ["a", "b"]
    assert manifest["assets"][0] == {
        "key": "a", "filename": "a.tif", "fingerprint": "sha256:1", "size": 1,
    }


def test_completion_payload_missing_fields_serialize_as_null():
    _name, body = completion_payload("scene", {"a": {"fingerprint": "sha256:1"}})
    manifest = json.loads(body)
    assert manifest["assets"] == [
        {"key": "a", "filename": None, "fingerprint": "sha256:1", "size": None}
    ]
