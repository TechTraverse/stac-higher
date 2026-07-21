from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.postingest import apply_post_ingest


class _FakeAdapter:
    protocol = "sftp"

    def __init__(self, files):
        self.files = dict(files)
        self.deleted = []
        self.puts = {}

    async def get(self, path):
        return self.files[path]

    async def put(self, path, data):
        self.puts[path] = data

    async def delete(self, path):
        self.deleted.append(path)
        self.files.pop(path, None)


def _cfg(post_ingest, source_path="/out"):
    return parse_ingest_config(
        {"source_path": source_path, "post_ingest": post_ingest}
    )


async def test_leave_is_noop():
    a = _FakeAdapter({"/out/scene.tif": b"x"})
    await apply_post_ingest(a, _cfg("leave"), source_paths=["scene.tif"])
    assert a.deleted == [] and a.puts == {}


async def test_delete_removes_source():
    a = _FakeAdapter({"/out/scene.tif": b"x"})
    await apply_post_ingest(a, _cfg("delete"), source_paths=["scene.tif"])
    assert a.deleted == ["/out/scene.tif"]


async def test_move_copies_then_deletes():
    a = _FakeAdapter({"/out/scene.tif": b"x"})
    await apply_post_ingest(a, _cfg("move:/done"), source_paths=["scene.tif"])
    assert a.puts == {"/done/scene.tif": b"x"}
    assert a.deleted == ["/out/scene.tif"]


async def test_errors_are_swallowed():
    class _Boom(_FakeAdapter):
        async def delete(self, path):
            raise OSError("gone")

    a = _Boom({"/out/scene.tif": b"x"})
    # must not raise
    await apply_post_ingest(a, _cfg("delete"), source_paths=["scene.tif"])


async def test_reference_mode_skips_destructive_post_ingest():
    a = _FakeAdapter({"/out/scene.tif": b"x"})
    config = parse_ingest_config({
        "source_path": "/out", "storage_mode": "reference", "post_ingest": "delete",
    })
    await apply_post_ingest(a, config, source_paths=["scene.tif"])
    # Nothing deleted — the referenced bytes ARE the asset.
    assert a.files == {"/out/scene.tif": b"x"}
