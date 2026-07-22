import pytest

from pipeline.connections.adapters.base import StorageAdapter

pytestmark = pytest.mark.asyncio


class _RecordingAdapter(StorageAdapter):
    """Concrete adapter that records put/move calls (exercises the base
    put_atomic default)."""

    protocol = "rec"

    def __init__(self):
        self.calls: list[tuple] = []

    async def test(self): ...
    async def list(self, prefix=""):
        return []
    async def get(self, path):
        return b""
    async def put(self, path, data):
        self.calls.append(("put", path, data))
    async def delete(self, path): ...
    async def move(self, src, dst):
        self.calls.append(("move", src, dst))


async def test_base_put_atomic_writes_part_then_moves():
    a = _RecordingAdapter()
    await a.put_atomic("dir/file.tif", b"xyz")
    assert a.calls == [
        ("put", "dir/file.tif.part", b"xyz"),
        ("move", "dir/file.tif.part", "dir/file.tif"),
    ]


async def test_s3_put_atomic_is_direct_put(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter({"bucket": "b"}, {"access_key_id": "k", "secret_access_key": "s"})
    puts: list[tuple] = []

    async def _fake_put(path, data):
        puts.append((path, data))

    monkeypatch.setattr(a, "put", _fake_put)
    await a.put_atomic("k/scene.tif", b"abc")
    # S3 objects appear atomically on PUT — no .part dance.
    assert puts == [("k/scene.tif", b"abc")]


async def test_s3_copy_object_from_issues_server_side_copy(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter({"bucket": "dest"}, {"access_key_id": "k", "secret_access_key": "s"})
    calls: list[dict] = []

    class _Client:
        def copy_object(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(a, "_pinned_endpoint", lambda: None)
    monkeypatch.setattr(a, "_make_client", lambda endpoint_url: _Client())
    await a.copy_object_from("platform", "assets/col/scene/a.tif", "col/a.tif")
    assert calls == [
        {
            "Bucket": "dest",
            "Key": "col/a.tif",
            "CopySource": {"Bucket": "platform", "Key": "assets/col/scene/a.tif"},
        }
    ]
