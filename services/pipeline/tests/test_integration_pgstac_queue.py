"""pgstac session GUCs + queue drain against a real pgstac (M3-A).

Auto-skips unless DATABASE_URL is set. Proves what the unit tests cannot:
that the GUCs survive a pool checkout (SET is transactional — the configure
hook must commit), that a writer upsert lands its partition-stats statement in
`pgstac.query_queue` instead of running it inline, and that the drain CALL runs
it and records it in `query_queue_history` — including refreshing the
collection's advertised extent, which only the DRAIN session's GUC pairing
makes happen (see the module docstring on `pipeline.stac.query_queue`).

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_pgstac_queue.py

Shares the database with whatever the stack is doing (the standing GOES demo
enqueues too), so the assertions are "at least" and "no longer present", never
exact counts. The collection/item ids are namespaced (`m3a-*`) so this test
never reads or deletes rows it did not create; the collection fixture removes
everything it made, on success and on failure alike.
"""

from __future__ import annotations

import json
import os

import psycopg
import pytest

from pipeline.stac.pgstac_writer import PgPgstacWriter, close_writer_pools, writer_pool
from pipeline.stac.query_queue import PgPgstacQueueRepo

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

COLLECTION = "m3a-itest"

#: The world bbox `pgstac.create_collection` is declared with below — the
#: value the extent must NOT yet show once the item is written but before the
#: queue drains, and must no longer show once it has.
WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]
ITEM_BBOX = [0.0, 0.0, 1.0, 1.0]


def _item(item_id: str, dtstr: str) -> dict:
    return {
        "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
        "id": item_id, "collection": COLLECTION,
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "bbox": ITEM_BBOX,
        "properties": {"datetime": dtstr}, "assets": {}, "links": [],
    }


async def _collection_bbox(conn) -> list[float] | None:
    cur = await conn.execute(
        "SELECT content->'extent'->'spatial'->'bbox'->0 FROM pgstac.collections WHERE id = %s",
        (COLLECTION,),
    )
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return [float(v) for v in row[0]]


@pytest.fixture
async def collection():
    coll = {
        "type": "Collection", "stac_version": "1.0.0", "id": COLLECTION,
        "description": "m3a itest", "license": "proprietary",
        "extent": {"spatial": {"bbox": [WORLD_BBOX]},
                   "temporal": {"interval": [[None, None]]}}, "links": [],
    }
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute("SELECT pgstac.create_collection(%s::jsonb)", (json.dumps(coll),))
    try:
        yield COLLECTION
    finally:
        # Runs on success AND on a failed assertion (pytest fixture teardown
        # still executes) — this is the only STAC collection/items this test
        # creates, addressed by its own namespaced id, so deleting it can
        # never touch the standing demo's or anyone else's rows.
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute("SELECT pgstac.delete_collection(%s)", (COLLECTION,))
        close_writer_pools()


def test_pooled_connection_carries_both_gucs():
    pool = writer_pool(DATABASE_URL)
    try:
        with pool.connection() as conn:
            use_queue = conn.execute("SELECT pgstac.get_setting('use_queue')").fetchone()[0]
            extent = conn.execute(
                "SELECT pgstac.get_setting_bool('update_collection_extent')"
            ).fetchone()[0]
        assert use_queue == "true"
        assert extent is True
        # And the TABLE is untouched — the setting is session-scoped by design.
        with psycopg.connect(DATABASE_URL) as plain:
            table = plain.execute(
                "SELECT value FROM pgstac.pgstac_settings WHERE name = 'use_queue'"
            ).fetchone()
        assert table is None or table[0] == "false"
    finally:
        close_writer_pools()


async def test_upsert_queues_partition_stats_and_the_drain_runs_them(collection):
    writer = PgPgstacWriter(DATABASE_URL)
    repo = PgPgstacQueueRepo(DATABASE_URL)

    # Start from a drained queue so the assertion below is about OUR write.
    await repo.drain()

    await writer.upsert_items([_item("m3a-1", "2026-09-07T00:00:00Z")])

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT query FROM pgstac.query_queue WHERE query ILIKE %s",
            (f"%{COLLECTION}%",),
        )
        queued = [r[0] for r in await cur.fetchall()]
    assert queued, "the upsert should have QUEUED update_partition_stats, not run it inline"
    assert all("update_partition_stats" in q for q in queued)

    # Prove the "not yet updated" precondition (the controller's ruling): the
    # collection's extent must still be the world bbox it was created with —
    # the refresh is one of the QUEUED statements above, not yet executed.
    # Without this check, the post-drain assertion below could pass
    # vacuously (e.g. if create_collection itself had already set the item's
    # bbox, or some other path updated it outside the queue).
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        bbox_before_drain = await _collection_bbox(conn)
    assert bbox_before_drain == WORLD_BBOX, (
        "extent must NOT have updated yet — it only updates when the queued "
        "statement runs, on drain; got "
        f"{bbox_before_drain!r}"
    )

    before = await repo.sample()
    assert before.depth >= 1 and before.oldest_age_seconds is not None

    outcome = await repo.drain()
    assert outcome.executed >= 1
    assert outcome.errors == 0

    after = await repo.sample()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pgstac.query_queue WHERE query ILIKE %s",
            (f"%{COLLECTION}%",),
        )
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT count(*) FROM pgstac.query_queue_history"
            " WHERE query ILIKE %s AND error IS NULL",
            (f"%{COLLECTION}%",),
        )
        assert (await cur.fetchone())[0] >= 1
        # The queued statement also refreshed the collection's extent (§4.4):
        # it is no longer the declared world bbox — it now reflects the one
        # item this test wrote, which the pre-drain check above proves was
        # NOT already the case, so this cannot pass vacuously.
        bbox_after_drain = await _collection_bbox(conn)
    assert bbox_after_drain == ITEM_BBOX
    assert after.depth <= before.depth

    pruned = await repo.prune_history(0)
    assert pruned >= 1
