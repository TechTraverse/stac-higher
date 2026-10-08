"""Z-3 cube jobs: the per-sink lock, the cube_kick backstop, the stub."""

import datetime as dt

import pytest

from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from pipeline.config import Settings
from pipeline.cubes.repo import LedgerEntry
from pipeline.jobs.cubes import (
    JOB_CUBE_APPEND,
    JOB_CUBE_KICK,
    KICK_CRON,
    KICK_STALE_SECONDS,
    REASON_NOT_IMPLEMENTED,
    cube_append_enqueuer,
    cube_lock,
    enqueue_cube_append,
    register,
)
from pipeline.queue.interface import QUEUE_DEFAULT, Enqueued
from pipeline.queue.memory import InMemoryQueue

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


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
def queue(repo: FakeCubeRepo) -> InMemoryQueue:
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)
    return q


def test_registration(queue: InMemoryQueue):
    assert queue.queues[JOB_CUBE_APPEND] == QUEUE_DEFAULT
    assert queue.periodic[JOB_CUBE_KICK].cron == KICK_CRON == "*/5 * * * *"
    assert KICK_STALE_SECONDS == 120


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
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    stranded = await enqueue_cube_append(queue, "s1")
    queue.strand(stranded.job_id)  # its worker was SIGKILLed: holds cube:s1
    await enqueue_cube_append(queue, "s1")  # a later wake: waiting, blocked
    await queue.run_pending()
    assert repo.rows("s1")[0].status == "pending"  # wedged

    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    await queue.run_pending()
    assert [j.status for j in queue.jobs] == ["failed", "done"]
    assert repo.rows("s1")[0].status == "failed"  # the stub ran: unwedged


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


async def test_stub_marks_pending_rows_failed_not_implemented(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends(
        [
            LedgerEntry("s1", "a", T0),
            LedgerEntry("s1", "b", T0, status="skipped", reason="no_datetime"),
            LedgerEntry("s2", "c", T0),
        ]
    )
    await enqueue_cube_append(queue, "s1")
    await queue.run_pending()
    assert [(r.item_id, r.status, r.reason, r.attempts) for r in repo.rows("s1")] == [
        ("a", "failed", REASON_NOT_IMPLEMENTED, 1),
        ("b", "skipped", "no_datetime", 0),
    ]
    assert repo.rows("s2")[0].status == "pending"  # another sink's rows untouched
