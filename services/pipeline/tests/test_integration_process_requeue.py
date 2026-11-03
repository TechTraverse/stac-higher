"""Requeue into an occupied coalescing slot (#67) — auto-skips unless DATABASE_URL is set.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_process_requeue.py

Migrations 025/027 allow ONE `queued` run per (process, source) and per
(process, association). A `running` run that goes back to `queued` — the
runner's could-not-start requeue (`finish_run(status="queued")`) or the stall
sweep (`reset_stalled_runs`) — used to violate that index whenever a newer
trigger had queued a run meanwhile: the statement aborted, and the run stayed
`running` with its ingest-ledger rows `extracting`, forever.

Now the returning run FOLDS into the queued one: its items are appended, its
`extracting` ledger rows are re-pointed at the survivor, and its own row is
deleted. The queued run survives because an ITEMIZE that just folded files
into it is about to point their ledger rows at its id.

Safe against the live stack: the process and the association are disabled,
and every run carries a far-future `rate_deferred_until`, so the running
pipeline's tick never claims one.
"""

import datetime as dt
import json
import os
import uuid

import psycopg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

FAR_FUTURE = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
async def slot():
    """A disabled extractor process + revision + ingest association."""
    process_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
    connection_id, association_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO stac_higher.processes (id, name, group_id, created_by, enabled, kind)"
            " VALUES (%s, %s, 'itest', 'itest', false, 'extractor')",
            (process_id, f"itest-requeue-{process_id[:8]}"),
        )
        await conn.execute(
            "INSERT INTO stac_higher.process_revisions (id, process_id, created_by)"
            " VALUES (%s, %s, 'itest')",
            (revision_id, process_id),
        )
        await conn.execute(
            "INSERT INTO stac_higher.connections (id, name, protocol, group_id, created_by)"
            " VALUES (%s, %s, 's3', 'itest', 'itest')",
            (connection_id, f"itest-requeue-{connection_id[:8]}"),
        )
        await conn.execute(
            "INSERT INTO stac_higher.collection_connections"
            " (id, collection_id, connection_id, direction, created_by, enabled)"
            " VALUES (%s, 'itest-requeue', %s, 'ingest', 'itest', false)",
            (association_id, connection_id),
        )
    try:
        yield {"process_id": process_id, "revision_id": revision_id,
               "association_id": association_id}
    finally:
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(
                "DELETE FROM stac_higher.process_runs WHERE process_id = %s", (process_id,)
            )
            await conn.execute(
                "DELETE FROM stac_higher.ingest_files WHERE association_id = %s",
                (association_id,),
            )
            await conn.execute(
                "DELETE FROM stac_higher.collection_connections WHERE id = %s", (association_id,)
            )
            await conn.execute(
                "DELETE FROM stac_higher.connections WHERE id = %s", (connection_id,)
            )
            await conn.execute(
                "DELETE FROM stac_higher.process_revisions WHERE id = %s", (revision_id,)
            )
            await conn.execute("DELETE FROM stac_higher.processes WHERE id = %s", (process_id,))


async def _run(slot, *, status, items, started_ago=None, created_ago=0):
    now = dt.datetime.now(dt.UTC)
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "INSERT INTO stac_higher.process_runs"
            " (process_id, revision_id, association_id, status, input_items,"
            "  rate_deferred_until, started_at, created_at, attempts)"
            " VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) RETURNING id",
            (
                slot["process_id"], slot["revision_id"], slot["association_id"], status,
                json.dumps(items), FAR_FUTURE,
                now - dt.timedelta(seconds=started_ago) if started_ago is not None else None,
                now - dt.timedelta(seconds=created_ago),
                1 if status == "running" else 0,
            ),
        )
        return str((await cur.fetchone())[0])


async def _ledger_row(slot, run_id, path):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "INSERT INTO stac_higher.ingest_files"
            " (association_id, source_path, status, extract_run_id)"
            " VALUES (%s, %s, 'extracting', %s) RETURNING id",
            (slot["association_id"], path, run_id),
        )
        return str((await cur.fetchone())[0])


async def _runs(slot):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT id::text, status, input_items, error FROM stac_higher.process_runs"
            " WHERE process_id = %s ORDER BY created_at",
            (slot["process_id"],),
        )
        return await cur.fetchall()


async def _owner(entry_id):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT extract_run_id::text, status FROM stac_higher.ingest_files WHERE id = %s",
            (entry_id,),
        )
        return await cur.fetchone()


async def _sweep():
    # The sweep is global by design: on a shared database it also recovers
    # any other stranded run, exactly as the live pipeline's own sweep does
    # every few minutes. So assertions look at this slot only.
    return await _repo().reset_stalled_runs(
        dt.datetime.now(dt.UTC) - dt.timedelta(seconds=60), 1000
    )


def _repo():
    from pipeline.process.repo import PgProcessRepo

    return PgProcessRepo(DATABASE_URL)


async def test_could_not_start_requeue_folds_into_the_queued_run(slot):
    running = await _run(slot, status="running", items=[{"item_id": "a"}],
                         started_ago=5, created_ago=60)
    queued = await _run(slot, status="queued", items=[{"item_id": "b"}], created_ago=30)
    entry = await _ledger_row(slot, running, "a.nc")

    await _repo().finish_run(
        running, status="queued", error="No such image", log_ref=None,
        next_attempt_at=dt.datetime.now(dt.UTC),
    )

    rows = await _runs(slot)
    assert [(r[0], r[1]) for r in rows] == [(queued, "queued")]
    assert rows[0][2] == [{"item_id": "b"}, {"item_id": "a"}]
    assert await _owner(entry) == (queued, "extracting")


async def test_could_not_start_requeue_into_an_empty_slot_just_requeues(slot):
    running = await _run(slot, status="running", items=[{"item_id": "a"}], started_ago=5)

    await _repo().finish_run(
        running, status="queued", error="No such image", log_ref=None,
        next_attempt_at=dt.datetime.now(dt.UTC),
    )

    assert await _runs(slot) == [(running, "queued", [{"item_id": "a"}], "No such image")]


async def test_stall_sweep_recovers_every_run_of_one_slot(slot):
    # The original #67 state: several runs of ONE association stranded
    # `running`. The old single UPDATE aborted on the second, so none was
    # ever recovered.
    first = await _run(slot, status="running", items=[{"item_id": "a"}],
                       started_ago=120, created_ago=300)
    second = await _run(slot, status="running", items=[{"item_id": "b"}],
                        started_ago=110, created_ago=200)
    third = await _run(slot, status="running", items=[{"item_id": "c"}],
                       started_ago=100, created_ago=100)
    entries = [await _ledger_row(slot, run, f"{run}.nc") for run in (first, second, third)]

    recovered = await _sweep()

    assert recovered >= 3
    rows = await _runs(slot)
    assert [(r[0], r[1]) for r in rows] == [(first, "queued")]
    assert sorted(i["item_id"] for i in rows[0][2]) == ["a", "b", "c"]
    for entry in entries:
        assert await _owner(entry) == (first, "extracting")


async def test_stall_sweep_folds_into_a_run_queued_meanwhile(slot):
    stalled = await _run(slot, status="running", items=[{"item_id": "a"}],
                         started_ago=120, created_ago=300)
    queued = await _run(slot, status="queued", items=[{"item_id": "b"}], created_ago=30)

    recovered = await _sweep()

    assert recovered >= 1
    rows = await _runs(slot)
    assert [(r[0], r[1]) for r in rows] == [(queued, "queued")]
    assert rows[0][2] == [{"item_id": "b"}, {"item_id": "a"}]
    assert stalled not in {r[0] for r in rows}


async def test_stall_sweep_leaves_a_fresh_running_run_alone(slot):
    fresh = await _run(slot, status="running", items=[{"item_id": "a"}], started_ago=5)

    await _sweep()

    assert [(r[0], r[1]) for r in await _runs(slot)] == [(fresh, "running")]
