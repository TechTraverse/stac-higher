"""K-3 capacity statements against Postgres — auto-skips unless DATABASE_URL is set.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_process_capacity.py

Needs migration 029 applied (the app applies it on its first API request).
Pins what the fake cannot: the profile join through process_revisions, the
"claimed ahead of" ordering, the release's attempt restore and phase, the
claim predicate on next_attempt_at, and phase cleared on finish.

Safe against the live stack: the process is disabled, its revision names a
profile id no deployment has (so no live run is counted), and every run
carries a far-future `rate_deferred_until`, so the pipeline's tick never
claims one.
"""

import datetime as dt
import json
import os
import uuid

import psycopg
import pytest

from pipeline.process.repo import PgProcessRepo

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

FAR_FUTURE = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
async def profile_slot():
    process_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
    profile = f"itest-cap-{process_id[:8]}"
    runtime = {"kind": "inline_python", "hardware": {"profile": profile, "cpu": 1, "gpu_count": 0}}
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO stac_higher.processes (id, name, group_id, created_by, enabled)"
            " VALUES (%s, %s, 'itest', 'itest', false)",
            (process_id, f"itest-capacity-{process_id[:8]}"),
        )
        await conn.execute(
            "INSERT INTO stac_higher.process_revisions (id, process_id, runtime, created_by)"
            " VALUES (%s, %s, %s::jsonb, 'itest')",
            (revision_id, process_id, json.dumps(runtime)),
        )
    try:
        yield {"process_id": process_id, "revision_id": revision_id, "profile": profile}
    finally:
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(
                "DELETE FROM stac_higher.process_runs WHERE process_id = %s", (process_id,)
            )
            await conn.execute(
                "DELETE FROM stac_higher.process_revisions WHERE id = %s", (revision_id,)
            )
            await conn.execute("DELETE FROM stac_higher.processes WHERE id = %s", (process_id,))


async def _run(slot, *, status, started_ago=None):
    now = dt.datetime.now(dt.UTC)
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "INSERT INTO stac_higher.process_runs"
            " (process_id, revision_id, status, input_items, rate_deferred_until,"
            "  started_at, attempts, is_test)"
            " VALUES (%s, %s, %s, '[]'::jsonb, %s, %s, %s, true) RETURNING id",
            (
                slot["process_id"], slot["revision_id"], status, FAR_FUTURE,
                now - dt.timedelta(seconds=started_ago) if started_ago is not None else None,
                1 if status == "running" else 0,
            ),
        )
        return str((await cur.fetchone())[0])


async def _row(run_id):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT status, attempts, started_at, phase, phase_detail, next_attempt_at"
            "  FROM stac_higher.process_runs WHERE id = %s",
            (run_id,),
        )
        return await cur.fetchone()


async def test_counts_only_running_runs_on_the_profile_claimed_ahead(profile_slot):
    repo = PgProcessRepo(DATABASE_URL)
    first = await _run(profile_slot, status="running", started_ago=20)
    second = await _run(profile_slot, status="running", started_ago=10)
    third = await _run(profile_slot, status="running", started_ago=0)
    await _run(profile_slot, status="queued")  # never counted

    profile = profile_slot["profile"]
    assert await repo.count_running_on_profile(profile, ahead_of_run_id=first) == 0
    assert await repo.count_running_on_profile(profile, ahead_of_run_id=second) == 1
    assert await repo.count_running_on_profile(profile, ahead_of_run_id=third) == 2
    assert await repo.count_running_on_profile("no-such-profile", ahead_of_run_id=third) == 0


async def test_release_restores_the_attempt_and_claims_honour_next_attempt_at(profile_slot):
    repo = PgProcessRepo(DATABASE_URL)
    run_id = await _run(profile_slot, status="running", started_ago=0)
    retry_at = FAR_FUTURE + dt.timedelta(hours=1)

    await repo.release_for_capacity(run_id, next_attempt_at=retry_at, detail="full")
    status, attempts, started_at, phase, detail, next_attempt_at = await _row(run_id)
    assert (status, attempts, started_at) == ("queued", 0, None)
    assert (phase, detail, next_attempt_at) == ("pending_capacity", "full", retry_at)

    # Past the rate deferral but before next_attempt_at: not claimable.
    assert await repo.claim_run(run_id, FAR_FUTURE + dt.timedelta(minutes=1)) is None
    claimed = await repo.claim_run(run_id, retry_at + dt.timedelta(seconds=1))
    assert claimed is not None and claimed.attempts == 1
    _, _, _, phase, detail, next_attempt_at = await _row(run_id)
    assert (phase, detail, next_attempt_at) == ("starting", None, None)

    await repo.finish_run(
        run_id, status="succeeded", error=None, log_ref=None, next_attempt_at=None
    )
    status, _, _, phase, detail, _ = await _row(run_id)
    assert (status, phase, detail) == ("succeeded", None, None)
