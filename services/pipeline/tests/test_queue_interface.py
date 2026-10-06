"""Queue interface contract, exercised through the in-memory backend."""

import pytest

from pipeline.queue.interface import Enqueued, QueueConnectionError, QueueError
from pipeline.queue.memory import InMemoryQueue


@pytest.fixture
def queue() -> InMemoryQueue:
    return InMemoryQueue()


async def test_register_and_run_task(queue: InMemoryQueue):
    seen: list[dict] = []

    async def handler(**payload):
        seen.append(payload)

    queue.register_task(handler, name="jobs.test")
    job_id = await queue.enqueue("jobs.test", {"path": "/a", "size": 1})
    assert job_id == Enqueued(job_id="1")

    ran = await queue.run_pending()
    assert ran == 1
    assert seen == [{"path": "/a", "size": 1}]
    assert queue.jobs[0].status == "done"


async def test_sync_handler_supported(queue: InMemoryQueue):
    seen = []
    queue.register_task(lambda **kw: seen.append(kw), name="jobs.sync")
    await queue.enqueue("jobs.sync", {"n": 1})
    await queue.run_pending()
    assert seen == [{"n": 1}]


async def test_enqueue_without_payload(queue: InMemoryQueue):
    seen = []
    queue.register_task(lambda **kw: seen.append(kw), name="jobs.bare")
    await queue.enqueue("jobs.bare")
    await queue.run_pending()
    assert seen == [{}]


async def test_enqueue_batch(queue: InMemoryQueue):
    seen = []
    queue.register_task(lambda **kw: seen.append(kw), name="jobs.batch")
    ids = await queue.enqueue_batch("jobs.batch", [{"i": 0}, {"i": 1}, {"i": 2}])
    assert len(ids) == len(set(ids)) == 3

    await queue.run_pending()
    assert seen == [{"i": 0}, {"i": 1}, {"i": 2}]


async def test_enqueue_batch_empty(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.batch")
    assert await queue.enqueue_batch("jobs.batch", []) == []


async def test_enqueue_unknown_task_fails(queue: InMemoryQueue):
    with pytest.raises(QueueError):
        await queue.enqueue("jobs.nope", {})


async def test_duplicate_registration_fails(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.dup")
    with pytest.raises(QueueError):
        queue.register_task(lambda **kw: None, name="jobs.dup")
    with pytest.raises(QueueError):
        queue.register_periodic(lambda timestamp: None, name="jobs.dup", cron="* * * * *")


async def test_periodic_registration_and_tick(queue: InMemoryQueue):
    ticks: list[int] = []

    async def periodic(timestamp: int):
        ticks.append(timestamp)

    queue.register_periodic(periodic, name="jobs.tick", cron="*/5 * * * *")
    assert queue.periodic["jobs.tick"].cron == "*/5 * * * *"

    await queue.run_periodic("jobs.tick", timestamp=1_700_000_000)
    assert ticks == [1_700_000_000]


async def test_failed_job_marked_failed(queue: InMemoryQueue):
    def boom(**kw):
        raise RuntimeError("boom")

    queue.register_task(boom, name="jobs.boom")
    await queue.enqueue("jobs.boom")
    with pytest.raises(RuntimeError):
        await queue.run_pending()
    assert queue.jobs[0].status == "failed"


async def test_check_connection(queue: InMemoryQueue):
    await queue.check_connection()  # connected by default
    queue.connected = False
    with pytest.raises(QueueConnectionError):
        await queue.check_connection()


async def test_setup_idempotent(queue: InMemoryQueue):
    await queue.setup()
    await queue.setup()
    assert queue.is_set_up


async def test_run_worker_accepts_and_ignores_concurrency_kwargs(queue: InMemoryQueue):
    # Fix round 1 (Important #2): the ABC's run_worker now matches
    # ProcrastinateQueue's real signature; InMemoryQueue accepts the same
    # keywords (it has no worker pools to size) so callers need not
    # special-case the backend.
    seen = []
    queue.register_task(lambda **kw: seen.append(kw), name="jobs.run")
    await queue.enqueue("jobs.run")
    await queue.run_worker(concurrency=12, bytes_concurrency=4)
    assert seen == [{}]


async def test_enqueue_records_locks(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    result = await queue.enqueue("jobs.locked", {"n": 1}, lock="L", queueing_lock="Q")
    assert result == Enqueued(job_id="1", coalesced=False)
    assert (queue.jobs[0].lock, queue.jobs[0].queueing_lock) == ("L", "Q")


async def test_queueing_lock_coalesces_a_waiting_job(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    first = await queue.enqueue("jobs.locked", {"n": 1}, queueing_lock="Q")
    second = await queue.enqueue("jobs.locked", {"n": 2}, queueing_lock="Q")
    other = await queue.enqueue("jobs.locked", {"n": 3}, queueing_lock="R")
    assert first.coalesced is False
    assert second == Enqueued(job_id=None, coalesced=True)
    assert other.coalesced is False
    assert [j.payload for j in queue.jobs] == [{"n": 1}, {"n": 3}]


async def test_queueing_lock_frees_once_the_job_has_run(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    await queue.enqueue("jobs.locked", {}, queueing_lock="Q")
    await queue.run_pending()
    again = await queue.enqueue("jobs.locked", {}, queueing_lock="Q")
    assert again.coalesced is False


async def test_a_running_job_can_enqueue_its_successor(queue: InMemoryQueue):
    # Z-4's self re-enqueue (spec §6.2): a running job does not hold its own
    # queueing_lock, exactly like Procrastinate's status='todo' unique index.
    results: list[Enqueued] = []

    async def handler(n: int) -> None:
        if n == 1:
            results.append(
                await queue.enqueue("jobs.self", {"n": 2}, lock="L", queueing_lock="L")
            )

    queue.register_task(handler, name="jobs.self")
    await queue.enqueue("jobs.self", {"n": 1}, lock="L", queueing_lock="L")
    await queue.run_pending()
    assert results == [Enqueued(job_id="2")]
    assert [j.status for j in queue.jobs] == ["done", "done"]


async def test_same_lock_jobs_run_one_at_a_time(queue: InMemoryQueue):
    order: list[str] = []

    async def handler(name: str) -> None:
        order.append(f"start {name}")
        if name == "a":
            # Re-entrant drive while "a" holds lock L: "b" (same lock) must
            # wait, "c" (another lock) may run.
            await queue.run_pending()
        order.append(f"end {name}")

    queue.register_task(handler, name="jobs.lock")
    await queue.enqueue("jobs.lock", {"name": "a"}, lock="L")
    await queue.enqueue("jobs.lock", {"name": "b"}, lock="L")
    await queue.enqueue("jobs.lock", {"name": "c"}, lock="M")
    await queue.run_pending()
    assert order == ["start a", "start c", "end c", "end a", "start b", "end b"]
