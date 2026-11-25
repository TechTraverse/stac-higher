"""K-4 compare-and-set ledger against Postgres — auto-skips unless DATABASE_URL is set.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_process_reconcile.py

Needs migration 031 applied (the app applies it on its first API request, or
`npm run db:migrate` from the repo root). Pins what the fake cannot: the row
lock that makes a verdict exactly-once (`collecting`), the claim-stamp and
handle fences, the stall sweep sparing submitted runs, cancel winning every
requeue, the fold inside a collecting transaction, the app's cancel statement
serialising against a claim, and K-3's admission invariant surviving
submission and collection (addendum A1/A2).

Reuses test_integration_process_capacity's fixture: a disabled process whose
profiles are unique per test, so no live run is ever counted.
"""

import asyncio
import datetime as dt
import os

import psycopg
import pytest

import test_integration_process_capacity as cap
from pipeline.process.repo import PgProcessRepo

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

slot = cap.slot
_exec, _run = cap._exec, cap._run

#: The app's cancel statement (app/src/lib/processes/storage.ts `cancelRun`),
#: in psycopg placeholders. Keep the two in sync: a comment there names this.
APP_CANCEL_SQL = """
WITH target AS (
  SELECT id, status AS prior_status FROM stac_higher.process_runs
   WHERE id = %s AND process_id = %s AND status IN ('queued','failed','running')
   FOR UPDATE
), upd AS (
  UPDATE stac_higher.process_runs r
     SET cancel_requested_at = coalesce(r.cancel_requested_at, now()),
         status = CASE WHEN t.prior_status = 'running' THEN r.status ELSE 'cancelled' END,
         finished_at = CASE WHEN t.prior_status = 'running' THEN r.finished_at ELSE now() END,
         phase = CASE WHEN t.prior_status = 'running' THEN r.phase ELSE NULL END,
         phase_detail = CASE WHEN t.prior_status = 'running' THEN r.phase_detail ELSE NULL END,
         next_attempt_at = CASE WHEN t.prior_status = 'running'
                                THEN r.next_attempt_at ELSE NULL END,
         rate_deferred_until = CASE WHEN t.prior_status = 'running'
                                    THEN r.rate_deferred_until ELSE NULL END,
         error = CASE WHEN t.prior_status = 'running' THEN r.error
                      ELSE 'cancelled by an operator' END
    FROM target t WHERE r.id = t.id
  RETURNING r.id, r.status, t.prior_status
)
SELECT id, status, prior_status, pg_notify('process_run_cancel', '') FROM upd
"""


def _repo() -> PgProcessRepo:
    return PgProcessRepo(DATABASE_URL)


async def _state(run_id):
    rows = await _exec(
        "SELECT status, executor_handle, executor_backend, attempts, error, phase,"
        "       next_attempt_at, cancel_requested_at"
        "  FROM stac_higher.process_runs WHERE id = %s",
        (run_id,),
    )
    return rows[0] if rows else None


async def _submitted(slot, *, handle="h1", source=False, started_ago=0, profile="one"):
    """A running row the executor holds, as a claim + record_submission leave it."""
    run_id = await _run(slot, profile, status="running", source=source, admitted=True)
    await _exec(
        "UPDATE stac_higher.process_runs SET executor_handle = %s, submitted_at = now(),"
        " started_at = now() - make_interval(secs => %s), phase = 'running'"
        " WHERE id = %s",
        (handle, started_ago, run_id),
    )
    return run_id


async def _flow_runs(slot):
    rows = await _exec(
        "SELECT coalesce((flow_stats->>'runs')::int, 0) FROM stac_higher.process_sources"
        " WHERE id = %s",
        (slot["source_id"],),
    )
    return rows[0][0]


async def test_two_concurrent_collectors_write_one_verdict(slot):
    run_id = await _submitted(slot, source=True)
    before = await _flow_runs(slot)
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def first():
        async with _repo().collecting(run_id, handle="h1") as collecting:
            seen.append(collecting)
            entered.set()
            await release.wait()
            await collecting.finish(status="succeeded", error=None, log_ref="k",
                                    next_attempt_at=None, at=dt.datetime.now(dt.UTC))

    async def second():
        await entered.wait()
        async with _repo().collecting(run_id, handle="h1") as collecting:
            seen.append(collecting)
        release.set()

    await asyncio.gather(first(), second())
    assert seen[0] is not None and seen[1] is None
    assert (await _state(run_id))[0] == "succeeded"
    assert await _flow_runs(slot) == before + 1
    async with _repo().collecting(run_id, handle="h1") as again:
        assert again is None


async def test_a_collector_that_raises_rolls_back_and_the_next_one_collects(slot):
    run_id = await _submitted(slot, source=True)
    before = await _flow_runs(slot)
    with pytest.raises(OSError):
        async with _repo().collecting(run_id, handle="h1") as collecting:
            await collecting.finish(status="succeeded", error=None, log_ref="k",
                                    next_attempt_at=None, at=dt.datetime.now(dt.UTC))
            raise OSError("worker died mid-collection")
    assert (await _state(run_id))[:2] == ("running", "h1")
    assert await _flow_runs(slot) == before
    async with _repo().collecting(run_id, handle="h1") as collecting:
        assert await collecting.finish(
            status="succeeded", error=None, log_ref="k", next_attempt_at=None,
            at=dt.datetime.now(dt.UTC),
        ) == "succeeded"
    assert await _flow_runs(slot) == before + 1


async def test_set_phase_after_the_verdict_is_a_noop(slot):
    run_id = await _submitted(slot)
    repo = _repo()
    assert await repo.set_phase(run_id, handle="h1", phase="pending_capacity", detail="q")
    assert not await repo.set_phase(run_id, handle="other", phase="running", detail=None)
    async with repo.collecting(run_id, handle="h1") as collecting:
        await collecting.finish(status="failed", error="exit 1", log_ref=None,
                                next_attempt_at=dt.datetime.now(dt.UTC),
                                at=dt.datetime.now(dt.UTC))
    assert not await repo.set_phase(run_id, handle="h1", phase="running", detail=None)


async def test_stall_sweep_skips_submitted_rows(slot):
    plain = await _run(slot, status="running", admitted=True)
    await _exec(
        "UPDATE stac_higher.process_runs SET started_at = now() - interval '2 hours'"
        " WHERE id = %s",
        (plain,),
    )
    submitted = await _submitted(slot, handle="h2", started_ago=7200)
    await _repo().reset_stalled_runs(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1), 50)
    assert (await _state(plain))[:3] == ("queued", None, None)
    assert (await _state(submitted))[:2] == ("running", "h2")


async def test_a_stale_worker_cannot_finish_a_reclaimed_row(slot):
    repo = _repo()
    run_id = await _run(slot)
    now = dt.datetime.now(dt.UTC)
    first = await repo.claim_run(run_id, now, capacities=slot["capacities"])
    assert first is not None and first.claimed_at == now
    # The row is requeued (a stall sweep, say) and claimed again under it.
    assert await repo.requeue_without_attempt(
        run_id, claimed_at=first.claimed_at, error="stalled", next_attempt_at=now
    ) == "requeued"
    later = now + dt.timedelta(seconds=5)
    second = await repo.claim_run(run_id, later, capacities=slot["capacities"])
    assert second is not None and second.claimed_at == later

    assert await repo.finish_run(
        run_id, status="dead", error="late", log_ref=None, next_attempt_at=None,
        claimed_at=first.claimed_at,
    ) is None
    assert await repo.record_submission(
        run_id, claimed_at=first.claimed_at, handle="stale", backend="docker",
        phase="running", submitted_at=later, credentials_expire_at=None,
    ) is None
    record = await repo.record_submission(
        run_id, claimed_at=second.claimed_at, handle="fresh", backend="docker",
        phase="running", submitted_at=later, credentials_expire_at=later,
    )
    assert record is not None and record.cancel_requested is False
    assert (await _state(run_id))[:2] == ("running", "fresh")


async def test_requeue_without_attempt_restores_the_attempt(slot):
    repo = _repo()
    run_id = await _run(slot)
    now = dt.datetime.now(dt.UTC)
    run = await repo.claim_run(run_id, now, capacities=slot["capacities"])
    retry = now + dt.timedelta(seconds=60)
    await repo.requeue_without_attempt(
        run_id, claimed_at=run.claimed_at, error="daemon down", next_attempt_at=retry
    )
    status, handle, backend, attempts, error, phase, next_attempt_at, _ = await _state(run_id)
    assert (status, handle, backend, attempts, error, phase) == (
        "queued", None, None, 0, "daemon down", None
    )
    assert next_attempt_at == retry


async def test_requeue_with_cancel_requested_lands_cancelled(slot):
    repo = _repo()
    now = dt.datetime.now(dt.UTC)
    unsubmitted = await _run(slot)
    run = await repo.claim_run(unsubmitted, now, capacities=slot["capacities"])
    await _exec(
        "UPDATE stac_higher.process_runs SET cancel_requested_at = now() WHERE id = %s",
        (unsubmitted,),
    )
    assert await repo.requeue_without_attempt(
        unsubmitted, claimed_at=run.claimed_at, error="daemon down", next_attempt_at=now
    ) == "cancelled"
    state = await _state(unsubmitted)
    assert state[0] == "cancelled" and state[4] == "cancelled by an operator; daemon down"

    submitted = await _submitted(slot, handle="h9")
    await _exec(
        "UPDATE stac_higher.process_runs SET cancel_requested_at = now() WHERE id = %s",
        (submitted,),
    )
    async with repo.collecting(submitted, handle="h9") as collecting:
        assert await collecting.requeue(
            error="lost", phase=None, phase_detail=None, next_attempt_at=now
        ) == "cancelled"
    assert (await _state(submitted))[0] == "cancelled"


async def test_a_collector_requeue_folds_into_an_occupied_slot(slot):
    running = await _submitted(slot, source=True, handle="h3")
    await _exec(
        "UPDATE stac_higher.process_runs"
        "   SET is_test = false, input_items = '[{\"item_id\": \"a\"}]' WHERE id = %s",
        (running,),
    )
    queued = await _run(slot, source=True, is_test=False)
    async with _repo().collecting(running, handle="h3") as collecting:
        assert await collecting.requeue(
            error="lost", phase=None, phase_detail=None,
            next_attempt_at=dt.datetime.now(dt.UTC),
        ) == "merged"
    assert await _state(running) is None
    rows = await _exec(
        "SELECT input_items FROM stac_higher.process_runs WHERE id = %s", (queued,)
    )
    assert rows[0][0] == [{"item_id": "i"}, {"item_id": "a"}]


async def test_claim_and_app_cancel_serialise(slot):
    repo = _repo()
    for _ in range(10):
        run_id = await _run(slot, "free")

        async def cancel(run_id=run_id):
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
                cur = await conn.execute(APP_CANCEL_SQL, (run_id, slot["process_id"]))
                row = await cur.fetchone()
                await conn.commit()
                return row

        claimed, cancelled = await asyncio.gather(
            repo.claim_run(run_id, dt.datetime.now(dt.UTC), capacities=slot["capacities"]),
            cancel(),
        )
        status, *_rest, cancel_requested_at = await _state(run_id)
        assert status != "queued"
        assert cancelled is not None and cancel_requested_at is not None
        if claimed is not None:
            assert (status, cancelled[2]) == ("running", "running")
        else:
            assert (status, cancelled[2]) == ("cancelled", "queued")


async def test_admission_holds_through_submission_and_collection(slot):
    """Addendum A1: capacity 1 — submitted counts, collected frees the slot."""
    repo = _repo()
    caps = slot["capacities"]
    first, second = await _run(slot), await _run(slot)
    now = dt.datetime.now(dt.UTC)
    run = await repo.claim_run(first, now, capacities=caps)
    assert await repo.record_submission(
        first, claimed_at=run.claimed_at, handle="h1", backend="docker", phase="running",
        submitted_at=now, credentials_expire_at=None,
    ) is not None
    assert await repo.claim_run(second, now, capacities=caps) is None
    async with repo.collecting(first, handle="h1") as collecting:
        await collecting.finish(status="succeeded", error=None, log_ref=None,
                                next_attempt_at=None, at=now)
    later = now + dt.timedelta(seconds=60)
    assert await repo.claim_run(second, later, capacities=caps) is not None


async def test_admission_race_is_decided_under_the_advisory_lock(slot):
    """Addendum A2, deterministic: both claims block on the profile's lock."""
    profile = slot["profiles"]["one"]
    first, second = await _run(slot), await _run(slot)
    holder = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        await holder.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"process-capacity:{profile}",)
        )
        now = dt.datetime.now(dt.UTC)
        tasks = [
            asyncio.create_task(PgProcessRepo(DATABASE_URL).claim_run(
                run_id, now, capacities=slot["capacities"]))
            for run_id in (first, second)
        ]
        for _ in range(100):
            rows = await _exec(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'"
            )
            if rows[0][0] >= 2:
                break
            await asyncio.sleep(0.05)
        assert rows[0][0] >= 2, "both claims should be waiting on the profile lock"
        assert not any(task.done() for task in tasks)
        await holder.rollback()  # releases the xact lock
        results = await asyncio.gather(*tasks)
    finally:
        await holder.close()
    assert sum(r is not None for r in results) == 1
    assert await cap._admitted(profile) == 1


async def test_an_admission_refuses_to_start_inside_an_open_transaction():
    """Addendum A2: SET TRANSACTION must be the first statement."""
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        await conn.execute("SELECT 1")
        with pytest.raises(RuntimeError):
            await PgProcessRepo._begin_read_committed(conn)
        await conn.rollback()
        await PgProcessRepo._begin_read_committed(conn)
        cur = await conn.execute("SHOW transaction_isolation")
        assert (await cur.fetchone())[0] == "read committed"
