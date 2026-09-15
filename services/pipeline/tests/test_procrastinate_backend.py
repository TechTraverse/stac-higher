"""Procrastinate backend: registration wiring, no database required.

Constructing the backend and registering tasks must not open connections —
these tests would hang or error otherwise.
"""

import asyncio

import pytest

from pipeline.jobs import heartbeat
from pipeline.queue.interface import RetrySpec
from pipeline.queue.procrastinate_backend import ProcrastinateQueue

DSN = "postgresql://username:password@localhost:5433/postgis"


@pytest.fixture
def queue() -> ProcrastinateQueue:
    return ProcrastinateQueue(DSN, schema="procrastinate_test")


def test_construction_opens_no_connection(queue: ProcrastinateQueue):
    assert queue.name == "procrastinate"
    assert queue.schema == "procrastinate_test"


def test_rejects_unsafe_schema_name():
    with pytest.raises(ValueError):
        ProcrastinateQueue(DSN, schema="bad-schema; DROP TABLE x")


def test_register_task_lands_in_procrastinate_registry(queue: ProcrastinateQueue):
    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.example")
    assert "jobs.example" in queue.app.tasks


def test_register_task_with_retry_maps_to_retry_strategy(queue: ProcrastinateQueue):
    # I-55: a RetrySpec must reach Procrastinate as a real retry strategy —
    # without one, a handler exception fails the job after a single attempt.
    async def handler(**kw):
        pass

    queue.register_task(
        handler, name="jobs.retrying", retry=RetrySpec(max_attempts=4, wait_seconds=60)
    )
    strategy = queue.app.tasks["jobs.retrying"].retry_strategy
    assert strategy is not None
    assert strategy.max_attempts == 4
    assert strategy.wait == 60


def test_register_periodic_lands_in_registry_with_queueing_lock(
    queue: ProcrastinateQueue,
):
    async def tick(timestamp: int):
        pass

    queue.register_periodic(tick, name="jobs.tick", cron="* * * * *")
    task = queue.app.tasks["jobs.tick"]
    assert task.queueing_lock == "jobs.tick"
    # the periodic registry holds our task
    registered = {
        periodic_task.task.name
        for periodic_task in queue.app.periodic_registry.periodic_tasks.values()
    }
    assert "jobs.tick" in registered


def test_heartbeat_registers_through_interface(queue: ProcrastinateQueue):
    heartbeat.register(queue, state=heartbeat.HeartbeatState())
    assert heartbeat.JOB_NAME in queue.app.tasks


async def test_enqueue_batch_empty_is_noop(queue: ProcrastinateQueue):
    # must not touch the (nonexistent) database
    assert await queue.enqueue_batch("jobs.whatever", []) == []


def test_register_task_lands_on_the_named_queue(queue: ProcrastinateQueue):
    from pipeline.queue.interface import QUEUE_BYTES, QUEUE_DEFAULT

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.cheap")
    queue.register_task(handler, name="jobs.heavy", queue=QUEUE_BYTES)
    assert queue.app.tasks["jobs.cheap"].queue == QUEUE_DEFAULT == "default"
    assert queue.app.tasks["jobs.heavy"].queue == QUEUE_BYTES == "bytes"


async def test_run_worker_starts_one_worker_per_queue(queue: ProcrastinateQueue, monkeypatch):
    """M3-D: two Procrastinate workers in one process — the bytes queue's
    concurrency bounds memory, the default queue's is the rest."""
    calls: list[dict] = []

    async def fake_run_worker_async(**kwargs):
        calls.append(kwargs)

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    await queue.run_worker(concurrency=12, bytes_concurrency=4)

    by_name = {c["name"]: c for c in calls}
    assert set(by_name) == {"default", "bytes"}
    assert by_name["default"]["queues"] == ["default"]
    assert by_name["default"]["concurrency"] == 8
    assert by_name["bytes"]["queues"] == ["bytes"]
    assert by_name["bytes"]["concurrency"] == 4
    # Fix round 1 (Important #1): each worker's own signal handling must stay
    # off, or the second worker to install one steals SIGTERM/SIGINT from the
    # first (asyncio.add_signal_handler replaces, it does not layer).
    assert by_name["default"]["install_signal_handlers"] is False
    assert by_name["bytes"]["install_signal_handlers"] is False


async def test_run_worker_stop_signal_cancels_both_workers(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 1 (Important #1): a single SIGTERM/SIGINT must stop BOTH
    workers gracefully. `run_worker` owns one signal handler for both, and
    cancelling each worker's `run_worker_async` task is Procrastinate's
    documented graceful-stop path (`Worker.run` catches the cancellation,
    calls `stop()`, and re-raises once the graceful drain is done)."""
    import os
    import signal

    started = {"default": asyncio.Event(), "bytes": asyncio.Event()}
    cancelled: list[str] = []

    async def fake_run_worker_async(*, name, **kwargs):
        started[name].set()
        try:
            await asyncio.Event().wait()  # block forever, like the real worker
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    task = asyncio.create_task(queue.run_worker(concurrency=12, bytes_concurrency=4))
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in started.values())), timeout=1
    )

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=1)

    assert set(cancelled) == {"default", "bytes"}


def test_connector_pool_sized_to_worker_concurrency_by_default(queue: ProcrastinateQueue):
    # Fix round 1 (Important #3): unsized, psycopg_pool defaults to max_size=4
    # — 12 slots' fetch/finish/heartbeat round trips would share 4 connections
    # and PoolTimeout under load. Defaults here track DEFAULT_WORKER_CONCURRENCY
    # (12) + 4, matching DB_POOL_MAX's own sizing rule.
    pool_args = queue.app.connector._pool_args
    assert pool_args["min_size"] == 2
    assert pool_args["max_size"] == 16


def test_connector_pool_size_is_configurable():
    queue = ProcrastinateQueue(
        DSN, schema="procrastinate_test", pool_min_size=3, pool_max_size=20
    )
    pool_args = queue.app.connector._pool_args
    assert pool_args["min_size"] == 3
    assert pool_args["max_size"] == 20
