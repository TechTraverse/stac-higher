"""pypgstac upsert integration — auto-skips unless DATABASE_URL is set.

Requires the compose stack (pgstac at :5433) with a test collection present.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_itemize.py

The collection fixture uses `pgstac.create_collection(...)` /
`pgstac.delete_collection(...)` — both confirmed present on the running
pgstac (0.9.x) during the live verification run.

NOTE (live-run finding, ISSUE I-27): pgstac's `items` table enforces a
NOT NULL `geometry` column, so an item MUST carry a geometry to be
upsertable — even though the STAC spec and stac-pydantic both permit
`geometry: null`. This test therefore uses a real Polygon (the `raster_auto`
path always derives one). Items from the `defaults_only` strategy (and a
`sidecar` with no parsed geometry) produce `geometry: null` and cannot be
catalogued in pgstac as-is; see ISSUE I-27 for the open product decision.
"""

import json
import os

import psycopg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

COLLECTION = "b4-itest"


def _item(item_id, dtstr):
    # pgstac requires a non-null geometry (ISSUE I-27); use a small real Polygon.
    return {
        "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
        "id": item_id, "collection": COLLECTION,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        "bbox": [0, 0, 1, 1],
        "properties": {"datetime": dtstr}, "assets": {}, "links": [],
    }


@pytest.fixture
async def collection():
    # Insert a minimal collection via pgstac's create_collection, clean up after.
    coll = {
        "type": "Collection", "stac_version": "1.0.0", "id": COLLECTION,
        "description": "b4 itest", "license": "proprietary",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]]},
                   "temporal": {"interval": [[None, None]]}}, "links": [],
    }
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute("SELECT pgstac.create_collection(%s::jsonb)", (json.dumps(coll),))
        # Resolve the partition name WHILE the collection exists — teardown
        # needs it after `delete_collection` has taken the row (and the key)
        # with it. pgstac names queued statements after the PARTITION
        # (`_items_<key>`, a serial), never after the collection id.
        cur = await conn.execute("SELECT key FROM pgstac.collections WHERE id = %s", (COLLECTION,))
        partition = f"_items_{(await cur.fetchone())[0]}"
    try:
        yield COLLECTION
    finally:
        # M3-A made this cleanup load-bearing: the writer now runs with
        # `pgstac.use_queue` ON, so each upsert above QUEUES a
        # `update_partition_stats('<partition>')` instead of running it
        # inline. Dropping the collection without clearing them leaves
        # statements naming a partition that no longer exists; the next
        # drain tick (`pipeline.pgstac_queue_drain`, every minute) executes
        # each one, gets `relation "<partition>" does not exist`, and writes
        # an error row to `query_queue_history` — permanent noise on a shared
        # database, and an error-rate signal an operator would chase. The
        # queue self-heals (pgstac removes the row either way), so this is
        # hygiene, not correctness. Quoted pattern so `_items_3` cannot match
        # `_items_34`. Same shape as tests/test_integration_pgstac_queue.py.
        pattern = f"%'{partition}'%"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute("DELETE FROM pgstac.query_queue WHERE query ILIKE %s", (pattern,))
            await conn.execute(
                "DELETE FROM pgstac.query_queue_history WHERE query ILIKE %s", (pattern,)
            )
            await conn.execute("SELECT pgstac.delete_collection(%s)", (COLLECTION,))


async def test_pgstac_schema_version_matches_pinned_pypgstac():
    # I-54 drift guard: the pgstac image only installs its schema on a FRESH
    # volume (initdb), so bumping the image tag never migrates a persisted
    # volume. The compose `pgstac-migrate` one-shot closes that gap; this
    # asserts the running schema matches the pinned migrator, so the guard
    # tracks every future pin bump (0.9.11 was merely the I-54 instance —
    # the release that fixed `get_tstz_constraint`'s fractional-second regex).
    from pypgstac.version import __version__ as pinned

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute("SELECT pgstac.get_version()")
        row = await cur.fetchone()
    assert row is not None
    schema = tuple(int(p) for p in row[0].split(".")[:3])
    assert schema == tuple(int(p) for p in pinned.split(".")[:3]), (
        f"pgstac schema {row[0]} != pinned pypgstac {pinned} — schema drift; "
        "run `docker compose up pgstac-migrate` (and keep the database image "
        "pin in lockstep with the pipeline's pypgstac pin)"
    )


async def test_second_microsecond_load_widens_partition_constraint(collection):
    # I-54 regression: after the first load, pgstac tightens the partition
    # CHECK constraint to the loaded data's min/max — including fractional
    # seconds. 0.9.10's `get_tstz_constraint` regex couldn't re-parse a
    # fractional-second constraint, so the loader skipped widening and the
    # second single-item load died with CheckViolation. Two sequential
    # single-item loads with distinct microsecond datetimes reproduce the
    # exact M1-rehearsal shape.
    from pipeline.stac.pgstac_writer import PgPgstacWriter

    writer = PgPgstacWriter(DATABASE_URL)
    await writer.upsert_items([_item("usec-1", "2024-03-01T10:00:00.123456Z")])
    await writer.upsert_items([_item("usec-2", "2024-03-02T11:30:00.654321Z")])

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pgstac.items"
            " WHERE collection = %s AND id LIKE 'usec-%%'", (COLLECTION,))
        row = await cur.fetchone()
    assert row is not None and row[0] == 2


async def test_upsert_then_query_and_update(collection):
    from pipeline.stac.pgstac_writer import PgPgstacWriter

    writer = PgPgstacWriter(DATABASE_URL)
    await writer.upsert_items([_item("scene-1", "2021-01-01T00:00:00Z")])

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT content->'properties'->>'datetime' FROM pgstac.items"
            " WHERE id = 'scene-1' AND collection = %s", (COLLECTION,))
        row = await cur.fetchone()
    assert row is not None and row[0].startswith("2021-01-01")

    # Upsert same id with a new datetime → update in place.
    await writer.upsert_items([_item("scene-1", "2022-02-02T00:00:00Z")])
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT content->'properties'->>'datetime' FROM pgstac.items WHERE id = 'scene-1'")
        row = await cur.fetchone()
    assert row is not None and row[0].startswith("2022-02-02")
