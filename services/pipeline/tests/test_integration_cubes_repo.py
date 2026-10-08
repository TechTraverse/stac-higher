"""PgCubeRepo against a migrated database -- auto-skips unless DATABASE_URL
is set AND migration 032 has been applied.

    DATABASE_URL=postgresql://... uv run pytest tests/test_integration_cubes_repo.py

Every test creates its own sinks (uuid-suffixed collection ids) and deletes
them in teardown (the ledger cascades). The one whole-table read,
``sinks_with_stale_pending``, is asserted only for the sinks the test owns.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


@pytest.fixture
async def db():
    """(raw connection, make_sink) with every sink it made deleted after."""
    import psycopg

    from pipeline.db.pool import close_pools

    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
    cur = await conn.execute("SELECT to_regclass('stac_higher.cube_appends')")
    if (await cur.fetchone())[0] is None:
        await conn.close()
        pytest.skip("migration 032 not applied")
    made: list[str] = []

    async def make_sink(source: str, *, enabled: bool = True) -> str:
        cur = await conn.execute(
            "INSERT INTO stac_higher.cube_sinks"
            " (source_collection_id, cube_collection_id, enabled, config, created_by)"
            " VALUES (%s, %s, %s, '{}'::jsonb, 'itest') RETURNING id::text",
            (source, f"z3-cube-{uuid.uuid4().hex[:8]}", enabled),
        )
        sink_id = (await cur.fetchone())[0]
        made.append(sink_id)
        return sink_id

    yield conn, make_sink
    await conn.execute(
        "DELETE FROM stac_higher.cube_sinks WHERE id = ANY(%s::uuid[])", (made,)
    )
    await conn.close()
    await close_pools()


def _source() -> str:
    return f"z3-src-{uuid.uuid4().hex[:8]}"


async def _rows(conn, sink_id: str) -> list[tuple]:
    cur = await conn.execute(
        "SELECT item_id, item_datetime, status, reason, attempts"
        " FROM stac_higher.cube_appends WHERE cube_sink_id = %s ORDER BY item_id",
        (sink_id,),
    )
    return await cur.fetchall()


async def test_enabled_sinks_for_source_lists_enabled_only(db):
    from pipeline.cubes.repo import PgCubeRepo

    _, make_sink = db
    source = _source()
    on = await make_sink(source)
    await make_sink(source, enabled=False)
    await make_sink(_source())
    sinks = await PgCubeRepo(DATABASE_URL).enabled_sinks_for_source(source)
    assert [s.id for s in sinks] == [on]


async def test_record_appends_is_idempotent(db):
    from pipeline.cubes.repo import REASON_NO_DATETIME, LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    entries = [
        LedgerEntry(sink, "a", T0),
        LedgerEntry(sink, "a", T0),  # same key twice in one statement
        LedgerEntry(sink, "b", T0, status="skipped", reason=REASON_NO_DATETIME),
    ]
    assert await repo.record_appends(entries) == 2
    assert await repo.record_appends([LedgerEntry(sink, "a", T0)]) == 0  # a replayed PUT
    assert await _rows(conn, sink) == [
        ("a", T0, "pending", None, 0),
        ("b", T0, "skipped", REASON_NO_DATETIME, 0),
    ]


async def test_record_appends_skips_a_sink_deleted_or_disabled_after_the_lookup(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    disabled = await make_sink(_source(), enabled=False)
    gone = str(uuid.uuid4())  # never existed: an FK error would stall the outbox
    inserted = await PgCubeRepo(DATABASE_URL).record_appends(
        [LedgerEntry(disabled, "a", T0), LedgerEntry(gone, "a", T0)]
    )
    assert inserted == 0
    assert await _rows(conn, disabled) == []


async def test_sinks_with_stale_pending(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    stale = await make_sink(_source())
    fresh = await make_sink(_source())
    done = await make_sink(_source())
    off = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [LedgerEntry(s, "a", T0) for s in (stale, fresh, done, off)]
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET created_at = now() - interval '5 minutes'"
        " WHERE cube_sink_id = ANY(%s::uuid[])",
        ([stale, done, off],),
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended' WHERE cube_sink_id = %s",
        (done,),
    )
    await conn.execute(
        "UPDATE stac_higher.cube_sinks SET enabled = false WHERE id = %s", (off,)
    )
    found = set(await repo.sinks_with_stale_pending(120))
    assert stale in found
    assert not found & {fresh, done, off}


async def test_fail_pending_touches_pending_rows_only_and_never_the_sink(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    cur = await conn.execute(
        "SELECT updated_at FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    version = (await cur.fetchone())[0]
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    assert await repo.fail_pending(sink, "not_implemented") == 1
    assert await _rows(conn, sink) == [
        ("a", T0, "failed", "not_implemented", 1),
        ("b", T0, "appended", None, 0),
    ]
    # #98/#99: updated_at is the app's version; the pipeline never writes it.
    cur = await conn.execute(
        "SELECT updated_at FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    assert (await cur.fetchone())[0] == version
