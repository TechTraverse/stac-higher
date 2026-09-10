"""Connection-pool integration — auto-skips unless DATABASE_URL is set.

Run against the compose Postgres:

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_pool.py

Proves the two things M3-B claims and unit tests cannot: consecutive checkouts
land on the SAME backend (no fork per statement), and the pgstac session GUCs
from spec §4.2 are live on a pooled connection because the pool's `configure`
hook set them when the connection was created.
"""

import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping DB integration tests"
)


@pytest.fixture(autouse=True)
async def clean_pools():
    from pipeline.db.pool import close_pools

    await close_pools()
    yield
    await close_pools()


async def _backend_pid(pool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_backend_pid()")
        row = await cur.fetchone()
    return int(row[0])


async def test_sequential_checkouts_reuse_one_backend():
    from pipeline.db.pool import get_async_pool

    pool = await get_async_pool(DATABASE_URL)
    pids = {await _backend_pid(pool) for _ in range(5)}
    # min_size is 2, so a checkout may land on either warm connection — but
    # five sequential checkouts must never fork five backends.
    assert len(pids) <= 2, pids


async def test_pooled_connection_carries_the_pgstac_session_gucs():
    from pipeline.db.pgstac_session import PGSTAC_SESSION_SQL
    from pipeline.db.pool import get_async_pool

    assert PGSTAC_SESSION_SQL == (
        "SET pgstac.use_queue TO TRUE",
        "SET pgstac.update_collection_extent TO TRUE",
    )

    pool = await get_async_pool(DATABASE_URL)
    async with pool.connection() as conn:
        cur = await conn.execute("SHOW pgstac.use_queue")
        assert (await cur.fetchone())[0] == "true"
        cur = await conn.execute("SHOW pgstac.update_collection_extent")
        assert (await cur.fetchone())[0] == "true"


async def test_a_repo_call_does_not_open_a_new_session():
    """The end-to-end shape: two real repo calls, zero new backends.

    `pg_stat_database.sessions` is cumulative per database, so its delta over
    two repo calls is exactly the number of connections those calls forked.
    Before M3-B the delta was 2; it must now be 0.
    """
    from pipeline.db.pool import get_async_pool
    from pipeline.ingest.repo import PgIngestRepo

    pool = await get_async_pool(DATABASE_URL)  # warm the pool first

    async def sessions() -> int:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sessions FROM pg_stat_database WHERE datname = current_database()"
            )
            return int((await cur.fetchone())[0])

    repo = PgIngestRepo(DATABASE_URL)
    before = await sessions()
    await repo.list_enabled_ingest_associations()
    await repo.list_enabled_ingest_associations()
    after = await sessions()

    assert after - before == 0, f"{after - before} new backend sessions for two repo calls"


async def test_closing_the_pools_lets_a_later_call_reopen():
    from pipeline.db.pool import close_pools, get_async_pool, pool_stats

    pool = await get_async_pool(DATABASE_URL)
    assert pool_stats()[pool.name]["pool_size"] >= 1

    await close_pools()
    assert pool_stats() == {}

    reopened = await get_async_pool(DATABASE_URL)
    assert reopened is not pool
    assert await _backend_pid(reopened) > 0
