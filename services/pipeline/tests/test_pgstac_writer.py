import pypgstac.db
import pypgstac.load
import pytest

from pipeline.stac import pgstac_writer as writer_mod
from pipeline.stac.pgstac_writer import CollectionMissing, PgPgstacWriter, PgstacWriter


def test_pgpgstac_writer_is_a_writer():
    assert issubclass(PgPgstacWriter, PgstacWriter)


async def test_upsert_translates_collection_missing(monkeypatch):
    writer = PgPgstacWriter(dsn="postgresql://ignored")

    def _boom(items):
        raise Exception("Collection foo is not present in the database")

    monkeypatch.setattr(writer, "_upsert_sync", _boom)
    with pytest.raises(CollectionMissing):
        await writer.upsert_items([{"id": "x", "collection": "foo"}])


async def test_upsert_reraises_other_errors(monkeypatch):
    writer = PgPgstacWriter(dsn="postgresql://ignored")

    def _boom(items):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(writer, "_upsert_sync", _boom)
    with pytest.raises(RuntimeError):
        await writer.upsert_items([{"id": "x", "collection": "foo"}])


class _FakePool:
    closed = False

    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.closed = True


def test_writer_pool_is_shared_per_dsn_and_configured(monkeypatch):
    created: list[dict] = []

    def _fake_connection_pool(conninfo, **kwargs):
        created.append({"conninfo": conninfo, **kwargs})
        return _FakePool()

    monkeypatch.setattr(writer_mod, "ConnectionPool", _fake_connection_pool)
    writer_mod.close_writer_pools()

    a = writer_mod.writer_pool("postgresql://one")
    b = writer_mod.writer_pool("postgresql://one")
    c = writer_mod.writer_pool("postgresql://two")

    assert a is b and a is not c
    assert len(created) == 2
    # The hook that sets both GUCs is the pool's configure callback (spec §4.2);
    # a bounded pool caps concurrent upserts once M3-D raises concurrency.
    assert created[0]["configure"] is writer_mod.configure_pgstac_session
    assert created[0]["max_size"] == writer_mod.WRITER_POOL_MAX
    assert created[0]["open"] is True

    writer_mod.close_writer_pools()
    assert a.close_calls == 1 and c.close_calls == 1
    # A closed pool is replaced, not reused.
    d = writer_mod.writer_pool("postgresql://one")
    assert d is not a
    writer_mod.close_writer_pools()


def test_upsert_opens_pgstac_with_the_pool_and_use_queue(monkeypatch):
    sentinel_pool = _FakePool()
    monkeypatch.setattr(writer_mod, "writer_pool", lambda dsn: sentinel_pool)

    opened: list[dict] = []
    loaded: list[tuple] = []

    class _FakeDB:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeLoader:
        def __init__(self, db):
            self.db = db

        def load_items(self, items, insert_mode):
            loaded.append((list(items), insert_mode))

    monkeypatch.setattr(pypgstac.db, "PgstacDB", _FakeDB)
    monkeypatch.setattr(pypgstac.load, "Loader", _FakeLoader)

    writer = PgPgstacWriter(dsn="postgresql://ignored")
    writer._upsert_sync([{"id": "x", "collection": "c"}])

    # pypgstac only issues its own SET when it checks a connection out of a
    # POOL — a handed-in `connection` skips it — so the pool is what it gets.
    assert opened == [{"pool": sentinel_pool, "use_queue": True}]
    assert loaded == [([{"id": "x", "collection": "c"}], pypgstac.load.Methods.upsert)]
