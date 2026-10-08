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


async def test_load_sink_reads_the_app_version_and_the_prefixes(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink_id = await make_sink(_source())
    await conn.execute(
        "UPDATE stac_higher.cube_sinks SET source_prefixes = ARRAY['s3://noaa-goes19/']"
        " WHERE id = %s",
        (sink_id,),
    )
    cur = await conn.execute(
        "SELECT updated_at::text FROM stac_higher.cube_sinks WHERE id = %s", (sink_id,)
    )
    version = (await cur.fetchone())[0]
    repo = PgCubeRepo(DATABASE_URL)
    sink = await repo.load_sink(sink_id)
    assert sink is not None
    assert sink.version == version
    assert sink.source_prefixes == ("s3://noaa-goes19/",)
    assert (sink.config, sink.last_snapshot_id, sink.enabled) == ({}, None, True)
    assert await repo.load_sink(str(uuid.uuid4())) is None


async def test_take_pending_orders_by_item_datetime_and_bumps_attempts(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    _, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [
            LedgerEntry(sink, "late", T0 + dt.timedelta(minutes=10)),
            LedgerEntry(sink, "early", T0),
            LedgerEntry(sink, "mid", T0 + dt.timedelta(minutes=5)),
            LedgerEntry(sink, "done", T0, status="skipped", reason="no_datetime"),
        ]
    )
    taken = await repo.take_pending(sink, 2)
    assert [(r.item_id, r.attempts) for r in taken] == [("early", 1), ("mid", 1)]
    again = await repo.take_pending(sink, 50)
    assert [(r.item_id, r.attempts) for r in again] == [("early", 2), ("mid", 2), ("late", 1)]


async def test_release_rows_undoes_the_take_on_pending_rows_only(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    ids = {r.item_id: r.id for r in await repo.take_pending(sink, 50)}
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    assert await repo.release_rows(sink, list(ids.values())) == 1
    assert [(r[0], r[2], r[4]) for r in await _rows(conn, sink)] == [
        ("a", "pending", 0),
        ("b", "appended", 1),
    ]


async def test_finish_rows_changes_pending_rows_only(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo, RowOutcome

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    ids = {r.item_id: r.id for r in await repo.take_pending(sink, 50)}
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    changed = await repo.finish_rows(
        sink,
        [
            RowOutcome(ids["a"], "appended", "duplicate", "SNAP"),
            RowOutcome(ids["b"], "skipped", "late"),  # a second writer: no effect
        ],
    )
    assert changed == 1
    cur = await conn.execute(
        "SELECT item_id, status, reason, snapshot_id FROM stac_higher.cube_appends"
        " WHERE cube_sink_id = %s ORDER BY item_id",
        (sink,),
    )
    assert await cur.fetchall() == [
        ("a", "appended", "duplicate", "SNAP"),
        ("b", "appended", None, None),
    ]
    assert not await repo.has_pending(sink)


async def test_record_commit_first_commit_is_conditional_and_never_writes_updated_at(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    loaded = await repo.load_sink(sink)
    kw = {"appended_at": T0, "source_prefixes": ["s3://b/"]}
    await repo.record_error(sink, "boom")
    # An app write moved the version: the first commit loses (#90).
    assert not await repo.record_commit(
        sink, snapshot_id="S0", first_commit_version="2000-01-01 00:00:00+00", **kw
    )
    assert await repo.record_commit(
        sink, snapshot_id="S1", first_commit_version=loaded.version, **kw
    )
    # Only one first commit: a second run that also read NULL loses.
    assert not await repo.record_commit(
        sink, snapshot_id="S2", first_commit_version=loaded.version, **kw
    )
    assert await repo.record_commit(sink, snapshot_id="S3", first_commit_version=None, **kw)
    after = await repo.load_sink(sink)
    assert (after.last_snapshot_id, after.source_prefixes) == ("S3", ("s3://b/",))
    assert after.version == loaded.version  # the pipeline never writes updated_at
    cur = await conn.execute(
        "SELECT last_error, last_appended_at FROM stac_higher.cube_sinks WHERE id = %s",
        (sink,),
    )
    assert await cur.fetchone() == (None, T0)


async def test_record_error_keeps_the_app_version(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    before = (await repo.load_sink(sink)).version
    await repo.record_error(sink, "invalid config: unsupported parser 'grib'")
    cur = await conn.execute(
        "SELECT last_error FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    assert (await cur.fetchone())[0] == "invalid config: unsupported parser 'grib'"
    assert (await repo.load_sink(sink)).version == before
