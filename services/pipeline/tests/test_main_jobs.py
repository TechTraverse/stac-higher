"""build_queue wires the heartbeat, both connection bridge jobs, and cleanup."""

import asyncio

import pytest

from pipeline.config import Settings
from pipeline.jobs.dispatch import JOB_DISPATCH_POLL
from pipeline.jobs.drain import JOB_NAME as DRAIN_JOB
from pipeline.jobs.gc import COLLECT_JOB_NAME, RETENTION_JOB_NAME
from pipeline.jobs.health_sweep import JOB_NAME as SWEEP_JOB
from pipeline.jobs.heartbeat import JOB_NAME as HEARTBEAT_JOB
from pipeline.jobs.history import JOB_NAME as HISTORY_JOB
from pipeline.jobs.ingest import JOB_DISCOVER, JOB_FETCH, JOB_GROUP, JOB_ITEMIZE, JOB_POLL
from pipeline.jobs.monitor import JOB_NAME as MONITOR_JOB
from pipeline.jobs.notify import SWEEP_JOB_NAME as NOTIFY_SWEEP_JOB
from pipeline.jobs.pgstac_drain import JOB_NAME as PGSTAC_DRAIN_JOB
from pipeline.jobs.process import (
    JOB_CRON,
    JOB_REAP,
    JOB_RUN_NOW,
    JOB_RUN_TICK,
    JOB_SWEEP,
)
from pipeline.jobs.staging_cleanup import JOB_NAME as CLEANUP_JOB
from pipeline.main import build_queue
from pipeline.notify.fanout import WEBHOOK_JOB_NAME


def test_build_queue_registers_all_periodic_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {HEARTBEAT_JOB, DRAIN_JOB, SWEEP_JOB, CLEANUP_JOB} <= registered
    assert {JOB_POLL, JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE} <= registered
    assert JOB_DISPATCH_POLL in registered
    assert {MONITOR_JOB, NOTIFY_SWEEP_JOB, WEBHOOK_JOB_NAME} <= registered
    assert {RETENTION_JOB_NAME, COLLECT_JOB_NAME, HISTORY_JOB} <= registered
    # The reaper is its own leg, not folded into the DB sweep (M3-W-1).
    assert {JOB_RUN_TICK, JOB_CRON, JOB_SWEEP, JOB_REAP} <= registered
    # G-3: the immediate-run job, so a trigger does not wait for the tick.
    assert JOB_RUN_NOW in registered
    # M3-A: the pgstac query-queue drain (a sampler when pg_cron owns the drain).
    assert PGSTAC_DRAIN_JOB in registered


async def test_run_closes_both_pools_before_the_queue(monkeypatch):
    """M3-B: pools are process-wide, so `run()` owns their shutdown.

    Both of them close BEFORE `queue.aclose()`, because Procrastinate's own
    pool is the last thing released and nothing after that point may still
    want a connection. M3-A's SYNC writer pool and M3-B's ASYNC repo pool are
    separate objects and both must be released — this asserts the order rather
    than just the calls, because "closed, eventually" is not the contract.
    """
    import pipeline.main as main_module

    order: list[str] = []

    class ExplodingQueue:
        name = "stub"

        async def setup(self) -> None:
            raise RuntimeError("stop here")

        async def aclose(self) -> None:
            order.append("queue")

    async def fake_close_pools() -> None:
        order.append("async-pools")

    def fake_close_writer_pools() -> None:
        order.append("writer-pools")

    monkeypatch.setattr(main_module, "build_queue", lambda settings: ExplodingQueue())
    monkeypatch.setattr(main_module, "close_pools", fake_close_pools)
    monkeypatch.setattr(main_module, "close_writer_pools", fake_close_writer_pools)

    with pytest.raises(RuntimeError, match="stop here"):
        await main_module.run(Settings.from_env(env={}))

    assert order == ["async-pools", "writer-pools", "queue"]


async def test_run_isolates_a_failing_close_so_the_rest_still_run(monkeypatch):
    """A raising cleanup step must not skip the remaining closes, and must
    not shadow the original exception from the `try` above (fix round 1:
    the bare `finally` had no exception isolation)."""
    import pipeline.main as main_module

    order: list[str] = []

    class ExplodingQueue:
        name = "stub"

        async def setup(self) -> None:
            raise RuntimeError("stop here")

        async def aclose(self) -> None:
            order.append("queue")

    async def fake_close_pools() -> None:
        order.append("async-pools")
        raise ValueError("boom")

    def fake_close_writer_pools() -> None:
        order.append("writer-pools")

    monkeypatch.setattr(main_module, "build_queue", lambda settings: ExplodingQueue())
    monkeypatch.setattr(main_module, "close_pools", fake_close_pools)
    monkeypatch.setattr(main_module, "close_writer_pools", fake_close_writer_pools)

    with pytest.raises(RuntimeError, match="stop here"):
        await main_module.run(Settings.from_env(env={}))

    assert "writer-pools" in order
    assert "queue" in order


def test_build_queue_puts_only_the_byte_holding_jobs_on_the_bytes_queue():
    from pipeline.jobs.dispatch import JOB_DELIVER
    from pipeline.queue.interface import QUEUE_BYTES, QUEUE_DEFAULT

    queue = build_queue(Settings.from_env(env={}))
    on_bytes = {name for name, task in queue.app.tasks.items() if task.queue == QUEUE_BYTES}
    assert on_bytes == {JOB_FETCH, JOB_ITEMIZE, JOB_DELIVER}
    assert queue.app.tasks[HEARTBEAT_JOB].queue == QUEUE_DEFAULT
    assert queue.app.tasks[JOB_RUN_NOW].queue == QUEUE_DEFAULT


def test_blocking_executor_is_sized_to_the_concurrency():
    """Every blocking call is `asyncio.to_thread`; the loop's default executor
    (min(32, cpus + 4) threads) would be a hidden ceiling below 12 on a small
    container, so main sizes it to the slots plus the overlapping ticks."""
    import os

    from pipeline.main import blocking_executor

    stdlib_default = min(32, (os.cpu_count() or 1) + 4)
    executor = blocking_executor(Settings.from_env(env={"WORKER_CONCURRENCY": "12"}))
    try:
        # Fix round 3: never BELOW the stdlib default — on a >= 13-CPU host the
        # stdlib number is the larger one and would otherwise be lowered.
        assert executor._max_workers == max(16, stdlib_default)
    finally:
        executor.shutdown(wait=False)
    executor = blocking_executor(Settings.from_env(env={"WORKER_CONCURRENCY": "40"}))
    try:
        assert executor._max_workers == 44
    finally:
        executor.shutdown(wait=False)
class _FakeServer:
    """uvicorn.Server's shape as `run_until_first_exit` uses it: `serve()`
    runs until `should_exit` is set."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.should_exit = False
        self.fail = fail

    async def serve(self) -> None:
        if self.fail is not None:
            raise self.fail
        while not self.should_exit:
            await asyncio.sleep(0.005)


def _blocking_until_cancelled(flag: asyncio.Event):
    async def coro() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            flag.set()
            raise

    return coro()


async def test_run_until_first_exit_stops_everything_when_the_worker_returns():
    """A stop signal reaches the worker only (its asyncio handler displaces
    uvicorn's); the worker's clean return must take the server and the
    listener down instead of leaving the gather waiting for a SIGKILL."""
    from pipeline.main import run_until_first_exit

    server = _FakeServer()
    listener_cancelled = asyncio.Event()

    async def worker() -> None:
        await asyncio.sleep(0.01)

    await asyncio.wait_for(
        run_until_first_exit(server, worker(), _blocking_until_cancelled(listener_cancelled)),
        timeout=1,
    )
    assert server.should_exit
    assert listener_cancelled.is_set()


async def test_run_until_first_exit_reraises_a_crash_after_stopping_the_rest():
    from pipeline.main import run_until_first_exit

    server = _FakeServer()
    listener_cancelled = asyncio.Event()

    async def worker() -> None:
        raise RuntimeError("worker boom")

    with pytest.raises(RuntimeError, match="worker boom"):
        await asyncio.wait_for(
            run_until_first_exit(
                server, worker(), _blocking_until_cancelled(listener_cancelled)
            ),
            timeout=1,
        )
    assert server.should_exit
    assert listener_cancelled.is_set()


async def test_run_until_first_exit_stops_the_workers_when_the_server_dies():
    from pipeline.main import run_until_first_exit

    server = _FakeServer(fail=RuntimeError("port in use"))
    worker_cancelled = asyncio.Event()
    listener_cancelled = asyncio.Event()

    with pytest.raises(RuntimeError, match="port in use"):
        await asyncio.wait_for(
            run_until_first_exit(
                server,
                _blocking_until_cancelled(worker_cancelled),
                _blocking_until_cancelled(listener_cancelled),
            ),
            timeout=1,
        )
    assert worker_cancelled.is_set()
    assert listener_cancelled.is_set()
