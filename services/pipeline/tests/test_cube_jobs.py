"""Cube jobs: the per-sink lock, the cube_kick backstop, the cube_append handler."""

import datetime as dt

import pytest

import pipeline.jobs.cubes as cubes_jobs
from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from pipeline.config import Settings
from pipeline.cubes.append import AppendDeps, AppendReport
from pipeline.cubes.repo import LedgerEntry
from pipeline.cubes.resolve import PgSourceResolver
from pipeline.jobs.cubes import (
    CUBE_APPEND_RETRY,
    JOB_CUBE_APPEND,
    JOB_CUBE_KICK,
    KICK_CRON,
    KICK_STALE_SECONDS,
    cube_append_enqueuer,
    cube_lock,
    enqueue_cube_append,
    production_append_deps,
    register,
)
from pipeline.queue.interface import QUEUE_DEFAULT, Enqueued, RetrySpec
from pipeline.queue.memory import InMemoryQueue

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)
DEPS = object()  # what deps_factory hands the (patched) run_cube_append
KEY_B64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture
def repo() -> FakeCubeRepo:
    return FakeCubeRepo(
        sinks=[
            FakeSink("s1", "src", "cube1"),
            FakeSink("s2", "src", "cube2"),
            FakeSink("off", "src", "cube3", enabled=False),
        ]
    )


@pytest.fixture
def ran(monkeypatch) -> list[str]:
    """Sink ids the handler ran run_cube_append for (the append is Task 9's)."""
    calls: list[str] = []

    async def fake_run(cube_sink_id: str, deps) -> AppendReport:
        assert deps is DEPS
        calls.append(cube_sink_id)
        return AppendReport()

    monkeypatch.setattr(cubes_jobs, "run_cube_append", fake_run)
    return calls


@pytest.fixture
def queue(repo: FakeCubeRepo, ran: list[str]) -> InMemoryQueue:
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo, deps_factory=lambda: DEPS)
    return q


def test_registration(queue: InMemoryQueue):
    assert queue.queues[JOB_CUBE_APPEND] == QUEUE_DEFAULT
    assert queue.periodic[JOB_CUBE_KICK].cron == KICK_CRON == "*/5 * * * *"
    assert KICK_STALE_SECONDS == 120
    assert queue.retry_specs[JOB_CUBE_APPEND] == CUBE_APPEND_RETRY
    assert RetrySpec(max_attempts=3, wait_seconds=30) == CUBE_APPEND_RETRY


def test_cube_lock_is_per_sink():
    assert cube_lock("abc") == "cube:abc"


async def test_enqueue_cube_append_takes_both_locks(queue: InMemoryQueue):
    result = await enqueue_cube_append(queue, "s1")
    assert result == Enqueued(job_id="1")
    job = queue.jobs[0]
    assert (job.name, job.payload) == (JOB_CUBE_APPEND, {"cube_sink_id": "s1"})
    assert job.lock == job.queueing_lock == "cube:s1"


async def test_a_second_enqueue_for_a_waiting_sink_coalesces(queue: InMemoryQueue):
    await enqueue_cube_append(queue, "s1")
    again = await enqueue_cube_append(queue, "s1")
    other = await enqueue_cube_append(queue, "s2")
    assert again.coalesced is True
    assert other.coalesced is False
    assert len(queue.jobs) == 2


async def test_enqueuer_enqueues_each_sink(queue: InMemoryQueue):
    await cube_append_enqueuer(queue)(["s1", "s2", "s1"])
    assert [j.payload["cube_sink_id"] for j in queue.jobs] == ["s1", "s2"]


async def test_kick_re_enqueues_only_stale_pending_sinks(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends(
        [
            LedgerEntry("s1", "old", T0),
            LedgerEntry("s2", "new", T0),
        ]
    )
    # record_appends refuses a disabled sink (like the real JOIN): plant it.
    repo.ledger[("off", "old")] = FakeLedgerRow("off", "old", T0, "pending", None)
    repo.backdate("s1", "old", KICK_STALE_SECONDS + 1)
    repo.backdate("off", "old", KICK_STALE_SECONDS + 1)  # disabled: never kicked

    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)

    assert [(j.name, j.payload) for j in queue.jobs] == [
        (JOB_CUBE_APPEND, {"cube_sink_id": "s1"})
    ]


async def test_kick_skips_terminal_rows(queue: InMemoryQueue, repo: FakeCubeRepo):
    await repo.record_appends([LedgerEntry("s1", "a", T0, status="skipped", reason="late")])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert queue.jobs == []


async def test_kick_against_a_waiting_job_coalesces(queue: InMemoryQueue, repo: FakeCubeRepo):
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    await enqueue_cube_append(queue, "s1")
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert len(queue.jobs) == 1


async def test_kick_recovers_a_stranded_append_a_waiting_job_covers(
    queue: InMemoryQueue, repo: FakeCubeRepo, ran: list[str]
):
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    stranded = await enqueue_cube_append(queue, "s1")
    queue.strand(stranded.job_id)  # its worker was SIGKILLed: holds cube:s1
    await enqueue_cube_append(queue, "s1")  # a later wake: waiting, blocked
    await queue.run_pending()
    assert ran == []  # wedged

    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    await queue.run_pending()
    assert [j.status for j in queue.jobs] == ["failed", "done"]
    assert ran == ["s1"]  # unwedged


async def test_kick_requeues_a_stranded_append_with_nothing_waiting(queue: InMemoryQueue):
    stranded = await enqueue_cube_append(queue, "s1")
    queue.strand(stranded.job_id)
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert [j.status for j in queue.jobs] == ["pending"]


async def test_kick_still_kicks_stale_sinks_when_stalled_recovery_fails(
    queue: InMemoryQueue, repo: FakeCubeRepo, monkeypatch, caplog
):
    # A raise from retry_stalled (e.g. the stalled-jobs query hits a
    # ConnectorException) must not disable the §5.3 backstop.
    import logging

    async def boom(job_name: str) -> int:
        raise RuntimeError("queue database unreachable")

    monkeypatch.setattr(queue, "retry_stalled", boom)
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)

    with caplog.at_level(logging.ERROR, logger="pipeline.jobs.cubes"):
        await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)

    assert [(j.name, j.payload) for j in queue.jobs] == [
        (JOB_CUBE_APPEND, {"cube_sink_id": "s1"})
    ]
    assert [r.getMessage() for r in caplog.records] == [
        "cube_kick: stalled-job recovery failed"
    ]


async def test_cube_append_runs_the_append_for_its_sink(queue: InMemoryQueue, ran: list[str]):
    await enqueue_cube_append(queue, "s1")
    await queue.run_pending()
    assert ran == ["s1"]
    assert queue.jobs[0].status == "done"


async def test_cube_append_without_a_master_key_leaves_rows_waiting(repo: FakeCubeRepo):
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)  # production deps, no key
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    await enqueue_cube_append(q, "s1")
    await q.run_pending()
    assert q.jobs[0].status == "done"
    assert repo.rows("s1")[0].status == "pending"


async def test_production_deps_wire_the_real_seams(repo: FakeCubeRepo):
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)
    settings = Settings.from_env(
        env={"CREDENTIALS_MASTER_KEY": KEY_B64, "EGRESS_ALLOW_HOSTS": "minio"}
    )
    deps = production_append_deps(settings, q, repo)
    assert isinstance(deps, AppendDeps)
    assert isinstance(deps.resolver, PgSourceResolver)
    sink = await repo.load_sink("s1")
    assert "prefix: assets/cube1/_cube" in repr(deps.storage_for(sink))
    await deps.enqueue_next("s1")
    assert q.jobs[0].lock == q.jobs[0].queueing_lock == "cube:s1"
