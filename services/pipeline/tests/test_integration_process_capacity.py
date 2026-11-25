"""K-3 in-claim capacity admission against Postgres — auto-skips unless DATABASE_URL is set.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_process_capacity.py

Needs migration 029 applied (the app applies it on its first API request, or
`npm run migrate` from app/). Pins what the fake cannot:

- **The invariant:** at no committed instant are more than ``capacity`` runs
  ADMITTED on a profile (``status = 'running'`` with ``executor_backend``
  set, extractor runs exempt), whatever the workers' clocks say — admission
  is decided under a per-profile advisory lock in the claiming transaction.
- A run whose profile is full is not claimed: it stays ``queued`` as
  ``pending_capacity`` with ``next_attempt_at`` +15 s, and in the tick it
  does not consume the batch ``LIMIT`` or block other profiles.
- ``claim_due_runs`` honours ``next_attempt_at`` and breaks ``created_at``
  ties by id; the could-not-start requeue clears the admission and folds a
  non-test run into the queued run holding its slot (#67).

Profile ids are unique per test (``itest-cap-…``), so no live run is ever
counted. The process is disabled. The tick tests claim GLOBALLY, so they skip
on a database that holds any other due run (a shared dev stack); run them on a
throwaway database.
"""

import asyncio
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest

from pipeline.process.ledger import CAPACITY_RETRY_SECONDS
from pipeline.process.repo import PgProcessRepo

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


async def _exec(sql, params=()):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall() if cur.description else None


@pytest.fixture
async def slot():
    """A disabled process with one revision per profile, and a source."""
    process_id = str(uuid.uuid4())
    tag = process_id[:8]
    profiles = {"one": f"itest-cap-one-{tag}", "two": f"itest-cap-two-{tag}",
                "free": f"itest-cap-free-{tag}"}
    revisions = {key: str(uuid.uuid4()) for key in profiles}
    source_id = str(uuid.uuid4())
    connection_id, association_id = str(uuid.uuid4()), str(uuid.uuid4())
    await _exec(
        "INSERT INTO stac_higher.processes (id, name, group_id, created_by, enabled)"
        " VALUES (%s, %s, 'itest', 'itest', false)",
        (process_id, f"itest-capacity-{tag}"),
    )
    for key, profile in profiles.items():
        runtime = {"kind": "inline_python",
                   "hardware": {"profile": profile, "cpu": 1, "gpu_count": 0}}
        await _exec(
            "INSERT INTO stac_higher.process_revisions (id, process_id, runtime, created_by)"
            " VALUES (%s, %s, %s::jsonb, 'itest')",
            (revisions[key], process_id, json.dumps(runtime)),
        )
    await _exec(
        "INSERT INTO stac_higher.process_sources (id, process_id, collection_id)"
        " VALUES (%s, %s, %s)",
        (source_id, process_id, f"itest-capacity-{tag}"),
    )
    await _exec(
        "INSERT INTO stac_higher.connections (id, name, protocol, group_id, created_by)"
        " VALUES (%s, %s, 's3', 'itest', 'itest')",
        (connection_id, f"itest-capacity-{tag}"),
    )
    await _exec(
        "INSERT INTO stac_higher.collection_connections"
        " (id, collection_id, connection_id, direction, created_by, enabled)"
        " VALUES (%s, %s, %s, 'ingest', 'itest', false)",
        (association_id, f"itest-capacity-{tag}", connection_id),
    )
    try:
        yield {
            "process_id": process_id, "profiles": profiles, "revisions": revisions,
            "source_id": source_id, "association_id": association_id,
            "capacities": {profiles["one"]: 1, profiles["two"]: 2, profiles["free"]: None},
        }
    finally:
        await _exec("DELETE FROM stac_higher.process_runs WHERE process_id = %s", (process_id,))
        await _exec("DELETE FROM stac_higher.process_sources WHERE id = %s", (source_id,))
        await _exec(
            "DELETE FROM stac_higher.collection_connections WHERE id = %s", (association_id,)
        )
        await _exec("DELETE FROM stac_higher.connections WHERE id = %s", (connection_id,))
        await _exec(
            "DELETE FROM stac_higher.process_revisions WHERE process_id = %s", (process_id,)
        )
        await _exec("DELETE FROM stac_higher.processes WHERE id = %s", (process_id,))


async def _run(slot, profile="one", *, status="queued", created_ago=0, extractor=False,
               is_test=True, source=False, admitted=False, next_attempt_in=None):
    now = dt.datetime.now(dt.UTC)
    rows = await _exec(
        "INSERT INTO stac_higher.process_runs"
        " (process_id, revision_id, source_id, status, input_items, started_at,"
        "  created_at, finished_at, attempts, is_test, executor_backend, next_attempt_at,"
        "  association_id)"
        " VALUES (%s, %s, %s, %s, '[{\"item_id\": \"i\"}]'::jsonb, %s, %s, %s, %s, %s, %s,"
        "         %s, NULL) RETURNING id",
        (
            slot["process_id"], slot["revisions"][profile],
            slot["source_id"] if source else None, status,
            now if status == "running" else None,
            now - dt.timedelta(seconds=created_ago),
            now - dt.timedelta(seconds=1) if status == "failed" else None,
            1 if status in ("running", "failed") else 0,
            is_test,
            "docker" if admitted else None,
            now + dt.timedelta(seconds=next_attempt_in) if next_attempt_in is not None else None,
        ),
    )
    run_id = str(rows[0][0])
    if extractor:
        await _exec(
            "UPDATE stac_higher.process_runs SET association_id = %s WHERE id = %s",
            (slot["association_id"], run_id),
        )
    return run_id


async def _row(run_id):
    rows = await _exec(
        "SELECT status, phase, phase_detail, next_attempt_at, executor_backend, attempts"
        "  FROM stac_higher.process_runs WHERE id = %s",
        (run_id,),
    )
    return rows[0] if rows else None


async def _admitted(profile):
    rows = await _exec(
        "SELECT count(*) FROM stac_higher.process_runs r"
        "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
        " WHERE r.status = 'running' AND r.executor_backend IS NOT NULL"
        "   AND r.association_id IS NULL"
        "   AND rev.runtime->'hardware'->>'profile' = %s",
        (profile,),
    )
    return rows[0][0]


async def _require_quiet_database(slot):
    """The tick claims globally; on a shared database it would take live work."""
    rows = await _exec(
        "SELECT count(*) FROM stac_higher.process_runs"
        " WHERE process_id <> %s AND status IN ('queued', 'failed')",
        (slot["process_id"],),
    )
    if rows[0][0]:
        pytest.skip("the database holds other queued/failed runs; use a throwaway database")


# ---------------------------------------------------------------------------
# claim_run (the immediate path)
# ---------------------------------------------------------------------------


async def test_a_full_profile_is_not_claimed_and_waits_for_capacity(slot):
    repo = PgProcessRepo(DATABASE_URL)
    profile, caps = slot["profiles"]["one"], slot["capacities"]
    first, second = await _run(slot), await _run(slot)
    now = dt.datetime.now(dt.UTC)

    claimed = await repo.claim_run(first, now, capacities=caps)
    assert claimed is not None and claimed.id == first
    status, phase, _, _, backend, attempts = await _row(first)
    assert (status, phase, backend, attempts) == ("running", "starting", "docker", 1)

    assert await repo.claim_run(second, now, capacities=caps) is None
    status, phase, detail, next_attempt_at, backend, attempts = await _row(second)
    assert (status, phase, backend, attempts) == ("queued", "pending_capacity", None, 0)
    assert detail == f"profile {profile!r} is at its capacity of 1 on this host"
    assert next_attempt_at == now + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)

    # Before next_attempt_at it is not claimable even with room; after, it is.
    await repo.finish_run(first, status="succeeded", error=None, log_ref=None,
                          next_attempt_at=None)
    assert await repo.claim_run(second, now + dt.timedelta(seconds=1), capacities=caps) is None
    later = now + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS + 1)
    assert await repo.claim_run(second, later, capacities=caps) is not None
    assert (await _row(second))[1:3] == ("starting", None)


async def test_extractor_runs_are_exempt_and_do_not_count(slot):
    repo = PgProcessRepo(DATABASE_URL)
    caps, profile = slot["capacities"], slot["profiles"]["one"]
    now = dt.datetime.now(dt.UTC)
    # One queued extractor run per association (027's index): claim each
    # before queueing the next.
    extract_a = await _run(slot, extractor=True)
    assert await repo.claim_run(extract_a, now, capacities=caps) is not None
    extract_b = await _run(slot, extractor=True)
    assert await repo.claim_run(extract_b, now, capacities=caps) is not None
    assert await _admitted(profile) == 0  # extractors never count
    transform = await _run(slot)
    assert await repo.claim_run(transform, now, capacities=caps) is not None


async def test_two_concurrent_claims_never_admit_past_capacity(slot):
    """The invariant through real transactions: twenty rounds of two runs
    racing for a one-slot profile, the second worker's clock 50 ms BEHIND the
    first (the over-admission the review found with started_at ordering)."""
    caps, profile = slot["capacities"], slot["profiles"]["one"]
    for _ in range(20):
        a, b = await _run(slot), await _run(slot)
        now = dt.datetime.now(dt.UTC)
        results = await asyncio.gather(
            PgProcessRepo(DATABASE_URL).claim_run(a, now, capacities=caps),
            PgProcessRepo(DATABASE_URL).claim_run(b, now - dt.timedelta(milliseconds=50),
                                                  capacities=caps),
        )
        assert sum(r is not None for r in results) == 1
        assert await _admitted(profile) == 1
        await _exec("DELETE FROM stac_higher.process_runs WHERE id IN (%s, %s)", (a, b))


async def test_unbounded_and_unknown_profiles_always_admit(slot):
    repo = PgProcessRepo(DATABASE_URL)
    now = dt.datetime.now(dt.UTC)
    for _ in range(3):
        run = await _run(slot, "free")
        assert await repo.claim_run(run, now, capacities=slot["capacities"]) is not None
    # A profile the deployment does not define is admitted; run_one kills it.
    run = await _run(slot, "two")
    assert await repo.claim_run(run, now, capacities={}) is not None


# ---------------------------------------------------------------------------
# claim_due_runs (the tick)
# ---------------------------------------------------------------------------


async def test_the_tick_skips_a_full_profile_without_spending_its_batch(slot):
    """Twelve old runs on a one-slot profile must not starve newer due work
    on other profiles: the tick admits one of them, marks the rest waiting,
    and still claims the newer runs and a due failed retry."""
    await _require_quiet_database(slot)
    repo = PgProcessRepo(DATABASE_URL)
    caps = slot["capacities"]
    busy = [await _run(slot, "one", created_ago=600 - i) for i in range(12)]
    newer_two = [await _run(slot, "two", created_ago=10) for _ in range(2)]
    newer_free = await _run(slot, "free", created_ago=5)
    retry = await _run(slot, "free", status="failed", created_ago=1)

    now = dt.datetime.now(dt.UTC)
    claimed = {r.id for r in await repo.claim_due_runs(now, 10, capacities=caps)}

    assert claimed == {busy[0], *newer_two, newer_free, retry}
    assert await _admitted(slot["profiles"]["one"]) == 1
    for waiting in busy[1:]:
        status, phase, _, next_attempt_at, backend, _ = await _row(waiting)
        assert (status, phase, backend) == ("queued", "pending_capacity", None)
        assert next_attempt_at == now + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
    # A second tick inside the 15 s claims nothing on the full profile.
    assert await repo.claim_due_runs(now + dt.timedelta(seconds=1), 10, capacities=caps) == []


async def test_the_tick_honours_next_attempt_at_and_breaks_ties_by_id(slot):
    await _require_quiet_database(slot)
    repo = PgProcessRepo(DATABASE_URL)
    caps = slot["capacities"]
    later = await _run(slot, "free", next_attempt_in=3600)
    now = dt.datetime.now(dt.UTC)
    assert await repo.claim_due_runs(now, 10, capacities=caps) == []
    assert [r.id for r in await repo.claim_due_runs(
        now + dt.timedelta(hours=2), 10, capacities=caps)] == [later]

    # Same created_at: the lower id is claimed first.
    stamp = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    ties = [await _run(slot, "free") for _ in range(3)]
    await _exec(
        "UPDATE stac_higher.process_runs SET created_at = %s WHERE id = ANY(%s::uuid[])",
        (stamp, ties),
    )
    got = [r.id for r in await repo.claim_due_runs(dt.datetime.now(dt.UTC), 2, capacities=caps)]
    assert got == sorted(ties)[:2]


# ---------------------------------------------------------------------------
# the requeue paths clear the admission
# ---------------------------------------------------------------------------


async def test_a_could_not_start_requeue_frees_the_slot(slot):
    repo = PgProcessRepo(DATABASE_URL)
    caps, profile = slot["capacities"], slot["profiles"]["one"]
    now = dt.datetime.now(dt.UTC)
    run = await _run(slot)
    assert await repo.claim_run(run, now, capacities=caps) is not None
    await repo.finish_run(run, status="queued", error="daemon down", log_ref=None,
                          next_attempt_at=now)
    status, phase, _, _, backend, _ = await _row(run)
    assert (status, phase, backend) == ("queued", None, None)
    assert await _admitted(profile) == 0


async def test_a_non_test_requeue_folds_into_the_queued_run_holding_its_slot(slot):
    """#67 with a transform run: while A ran, a newer trigger queued B for the
    same source; A's could-not-start requeue folds into B and frees A's slot."""
    repo = PgProcessRepo(DATABASE_URL)
    caps, profile = slot["capacities"], slot["profiles"]["one"]
    now = dt.datetime.now(dt.UTC)
    first = await _run(slot, is_test=False, source=True, created_ago=60)
    assert await repo.claim_run(first, now, capacities=caps) is not None
    holder = await _run(slot, is_test=False, source=True)

    await repo.finish_run(first, status="queued", error="daemon down", log_ref=None,
                          next_attempt_at=now)
    assert await _row(first) is None  # folded away
    rows = await _exec(
        "SELECT jsonb_array_length(input_items), status FROM stac_higher.process_runs"
        " WHERE id = %s",
        (holder,),
    )
    assert rows[0] == (2, "queued")
    assert await _admitted(profile) == 0
