"""M3-B: the process-wide async connection pool registry.

These tests never touch a database — `psycopg_pool.AsyncConnectionPool` is
replaced by a recording fake, so what is under test is the registry's
behaviour: one pool per DSN, opened once, configured with M3-A's pgstac
session hook, sized from Settings, closed and cleared on shutdown.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from pipeline.db import pool as dbpool

DSN = "postgresql://username:password@localhost:5433/postgis"
OTHER_DSN = "postgresql://username:password@localhost:5433/other"


class FakePool:
    """Records what `get_async_pool` asked psycopg_pool for."""

    instances: ClassVar[list[FakePool]] = []

    def __init__(self, conninfo: str, **kwargs) -> None:
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.name = kwargs.get("name", "fake")
        self.min_size = kwargs.get("min_size")
        self.max_size = kwargs.get("max_size")
        self.opens = 0
        self.closes = 0
        FakePool.instances.append(self)

    async def open(self) -> None:
        self.opens += 1

    async def close(self) -> None:
        self.closes += 1

    def get_stats(self) -> dict[str, int]:
        return {
            "pool_min": self.min_size,
            "pool_max": self.max_size,
            "pool_size": self.min_size,
            "pool_available": self.min_size,
            "requests_waiting": 0,
        }


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch):
    """Empty registry + recording pool class for every test in this module."""
    FakePool.instances = []
    monkeypatch.setattr(dbpool, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(dbpool, "_pools", {})
    yield FakePool
    monkeypatch.setattr(dbpool, "_pools", {})


async def test_one_pool_per_dsn_opened_once():
    first = await dbpool.get_async_pool(DSN)
    second = await dbpool.get_async_pool(DSN)
    assert first is second
    assert first.opens == 1
    assert len(FakePool.instances) == 1


async def test_distinct_dsns_get_distinct_pools():
    a = await dbpool.get_async_pool(DSN)
    b = await dbpool.get_async_pool(OTHER_DSN)
    assert a is not b
    assert len(FakePool.instances) == 2


async def test_concurrent_first_use_creates_exactly_one_pool():
    # The lazy-open path is the one place two coroutines can race: without the
    # lock both would build a pool and one would leak, unopened and unclosed.
    pools = await asyncio.gather(*(dbpool.get_async_pool(DSN) for _ in range(8)))
    assert len({id(p) for p in pools}) == 1
    assert len(FakePool.instances) == 1
    assert FakePool.instances[0].opens == 1


async def test_pool_configures_the_pgstac_session_guc_hook():
    """Spec §4.2: the two pgstac session SETs live in the pool's on_connect."""
    from pipeline.db.pgstac_session import configure_pgstac_session_async

    created = await dbpool.get_async_pool(DSN)
    assert created.kwargs["configure"] is configure_pgstac_session_async
    # Lazy open, never psycopg_pool's implicit one.
    assert created.kwargs["open"] is False


async def test_pool_sizes_come_from_settings(monkeypatch):
    monkeypatch.setenv("DB_POOL_MIN", "3")
    monkeypatch.setenv("DB_POOL_MAX", "21")
    created = await dbpool.get_async_pool(DSN)
    assert (created.min_size, created.max_size) == (3, 21)


async def test_pool_sizes_default_when_env_is_absent(monkeypatch):
    monkeypatch.delenv("DB_POOL_MIN", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    created = await dbpool.get_async_pool(DSN)
    assert (created.min_size, created.max_size) == (2, 16)


def test_pool_name_drops_user_and_password():
    # /health serves this string; the DSN carries a password.
    name = dbpool.pool_name(DSN)
    assert name == "localhost:5433/postgis"
    assert "password" not in name
    assert "username" not in name


def test_pool_name_fills_in_omitted_parts():
    assert dbpool.pool_name("postgresql://database/postgis") == "database:5432/postgis"


async def test_pool_stats_is_keyed_by_the_redacted_name():
    await dbpool.get_async_pool(DSN)
    stats = dbpool.pool_stats()
    assert list(stats) == ["localhost:5433/postgis"]
    assert stats["localhost:5433/postgis"]["pool_max"] == 16


def test_pool_stats_is_empty_before_any_pool_exists():
    assert dbpool.pool_stats() == {}


async def test_close_pools_closes_every_pool_and_clears_the_registry():
    await dbpool.get_async_pool(DSN)
    await dbpool.get_async_pool(OTHER_DSN)

    await dbpool.close_pools()

    assert all(p.closes == 1 for p in FakePool.instances)
    assert dbpool.pool_stats() == {}
    # And a later call builds a fresh pool rather than handing back a closed one.
    await dbpool.get_async_pool(DSN)
    assert len(FakePool.instances) == 3


async def test_close_pools_is_a_no_op_when_nothing_was_opened():
    await dbpool.close_pools()  # must not raise — main.run()'s finally always calls it
    assert dbpool.pool_stats() == {}
