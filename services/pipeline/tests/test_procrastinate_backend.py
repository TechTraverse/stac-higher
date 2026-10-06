"""Procrastinate backend: registration wiring, no database required.

Constructing the backend and registering tasks must not open connections —
these tests would hang or error otherwise.
"""

import asyncio

import pytest

from pipeline.jobs import heartbeat
from pipeline.queue.interface import Enqueued, RetrySpec
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
    # Fix round 4: the drain must end before Docker's stop_grace_period (30 s
    # in compose) or SIGKILL skips the abort-with-retry and the pool cleanup.
    from pipeline.queue.procrastinate_backend import SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS

    assert 0 < SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS < 30
    assert by_name["default"]["shutdown_graceful_timeout"] == SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS
    assert by_name["bytes"]["shutdown_graceful_timeout"] == SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS
    # Z-3 final review: a booting worker prunes worker rows silent longer than
    # this, which makes their running jobs look stalled to retry_stalled at
    # once — it must be the same horizon retry_stalled checks, not 30 s.
    from pipeline.queue.procrastinate_backend import STALLED_WORKER_SECONDS

    assert by_name["default"]["stalled_worker_timeout"] == STALLED_WORKER_SECONDS
    assert by_name["bytes"]["stalled_worker_timeout"] == STALLED_WORKER_SECONDS


async def test_run_worker_stop_signal_cancels_both_workers(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 1 (Important #1): a single SIGTERM/SIGINT must stop BOTH
    workers gracefully. `run_worker` owns one signal handler for both, and
    cancelling each worker's `run_worker_async` task is Procrastinate's
    documented graceful-stop path (`Worker.run` catches the cancellation,
    calls `stop()`, and re-raises once the graceful drain is done).

    Fix round 2 (Important #1): the two fakes drain at DIFFERENT speeds
    (`bytes` sleeps 0.05s after catching the cancellation, `default` does
    not) — `run_worker` must not return until BOTH drains have completed, not
    just the first one (a plain `asyncio.gather` with the default
    `return_exceptions=False` would return as soon as `default` finished,
    leaving `bytes`'s drain unobserved)."""
    import os
    import signal

    started = {"default": asyncio.Event(), "bytes": asyncio.Event()}
    drained = {"default": asyncio.Event(), "bytes": asyncio.Event()}
    cancelled: list[str] = []

    async def fake_run_worker_async(*, name, **kwargs):
        started[name].set()
        try:
            await asyncio.Event().wait()  # block forever, like the real worker
        except asyncio.CancelledError:
            if name == "bytes":
                await asyncio.sleep(0.05)  # the slower drain
            cancelled.append(name)
            drained[name].set()
            raise

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    task = asyncio.create_task(queue.run_worker(concurrency=12, bytes_concurrency=4))
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in started.values())), timeout=1
    )

    # Fix round 2 (Minor #3): confirm our handler actually replaced the
    # default disposition before sending a real signal — a future regression
    # should fail this assertion, not kill pytest with an unhandled SIGTERM
    # (exit 143).
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL

    os.kill(os.getpid(), signal.SIGTERM)

    # `default` drains immediately; `bytes` is still mid-sleep — the overall
    # task must still be running.
    await asyncio.wait_for(drained["default"].wait(), timeout=1)
    assert not task.done()
    assert not drained["bytes"].is_set()

    await asyncio.wait_for(task, timeout=1)

    assert drained["bytes"].is_set()
    assert set(cancelled) == {"default", "bytes"}


async def test_run_worker_cancels_the_sibling_when_one_worker_crashes(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 2 (Important #1, error path): a worker ending on its own
    (without a stop request) is a crash, not a graceful stop — its sibling
    must be cancelled too instead of being left running unattended, and the
    original failure must still propagate."""
    cancelled: list[str] = []

    async def fake_run_worker_async(*, name, **kwargs):
        if name == "default":
            raise RuntimeError("boom")
        try:
            await asyncio.Event().wait()  # block forever, like the real worker
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.wait_for(
            queue.run_worker(concurrency=12, bytes_concurrency=4), timeout=1
        )

    assert cancelled == ["bytes"]


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


def _fake_workers(queue: ProcrastinateQueue, monkeypatch, *, drain_seconds: float):
    """Two fake workers that block until cancelled, then 'drain' for
    `drain_seconds` before re-raising — the shape of `Worker.run`."""
    state = {"running": set(), "drained": set(), "interrupted": set()}

    async def fake_run_worker_async(*, name, **kwargs):
        state["running"].add(name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await asyncio.sleep(drain_seconds)
            except asyncio.CancelledError:
                state["interrupted"].add(name)
                raise
            state["drained"].add(name)
            raise
        finally:
            state["running"].discard(name)

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    return state


async def test_run_worker_outer_cancellation_drains_both_workers(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 3: cancelling `run_worker` itself (main.run's gather
    propagating a sibling's failure, asyncio.run's teardown) must not leave
    the two worker tasks running — `asyncio.wait` does not cancel its
    futures, so the `finally` has to."""
    state = _fake_workers(queue, monkeypatch, drain_seconds=0.02)
    task = asyncio.create_task(queue.run_worker(concurrency=12, bytes_concurrency=4))
    await asyncio.sleep(0.01)
    assert state["running"] == {"default", "bytes"}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert state["running"] == set()
    assert state["drained"] == {"default", "bytes"}


async def test_run_worker_second_stop_signal_does_not_abort_the_drain(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 3: a repeat SIGTERM while both workers are draining must be
    a no-op — re-cancelling a task that is in `Worker.run`'s `await loop_task`
    would abort the graceful drain and orphan the loop task."""
    import os
    import signal

    state = _fake_workers(queue, monkeypatch, drain_seconds=0.1)
    task = asyncio.create_task(queue.run_worker(concurrency=12, bytes_concurrency=4))
    await asyncio.sleep(0.01)
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0.03)  # mid-drain
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=1)

    assert state["interrupted"] == set()
    assert state["drained"] == {"default", "bytes"}


async def test_run_worker_rejects_a_split_with_no_default_slots(queue: ProcrastinateQueue):
    # Fix round 3: only Settings.from_env validates the split; a direct caller
    # handing over equal numbers would otherwise get a Semaphore(0) default
    # worker that listens and never runs a job.
    with pytest.raises(ValueError, match="default queue"):
        await queue.run_worker(concurrency=4, bytes_concurrency=4)


async def test_run_worker_outer_cancellation_does_not_recancel_a_draining_worker(
    queue: ProcrastinateQueue, monkeypatch
):
    """Fix round 4: on the crash path the sibling is already cancelled and
    draining when an outer cancellation arrives; the `finally` must not
    cancel it a second time — that would abort its drain."""
    state = {"drained": set(), "interrupted": set()}

    async def fake_run_worker_async(*, name, **kwargs):
        if name == "default":
            raise RuntimeError("boom")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                state["interrupted"].add(name)
                raise
            state["drained"].add(name)
            raise

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    task = asyncio.create_task(queue.run_worker(concurrency=12, bytes_concurrency=4))
    await asyncio.sleep(0.03)  # default crashed; bytes is mid-drain
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert state["interrupted"] == set()
    assert state["drained"] == {"bytes"}


class _FakeDeferrer:
    def __init__(self, outcome):
        self.outcome = outcome
        self.payloads: list[dict] = []

    async def defer_async(self, **payload):
        self.payloads.append(payload)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _wire(queue: ProcrastinateQueue, monkeypatch, outcome):
    """Register jobs.locked and capture what enqueue hands Procrastinate."""

    async def handler(**kw):
        pass

    async def fake_open():
        pass

    queue.register_task(handler, name="jobs.locked")
    task = queue.app.tasks["jobs.locked"]
    deferrer = _FakeDeferrer(outcome)
    configured: list[dict] = []

    def fake_configure(**options):
        configured.append(options)
        return deferrer

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(task, "configure", fake_configure)
    monkeypatch.setattr(task, "defer_async", deferrer.defer_async)
    return deferrer, configured


async def test_enqueue_passes_both_locks_to_configure(queue, monkeypatch):
    deferrer, configured = _wire(queue, monkeypatch, 42)
    result = await queue.enqueue(
        "jobs.locked", {"cube_sink_id": "s"}, lock="cube:s", queueing_lock="cube:s"
    )
    assert result == Enqueued(job_id="42")
    assert configured == [{"lock": "cube:s", "queueing_lock": "cube:s"}]
    assert deferrer.payloads == [{"cube_sink_id": "s"}]


async def test_enqueue_passes_only_the_options_given(queue, monkeypatch):
    _deferrer, configured = _wire(queue, monkeypatch, 7)
    await queue.enqueue("jobs.locked", {}, lock="cube:s")
    assert configured == [{"lock": "cube:s"}]


async def test_enqueue_without_locks_skips_configure(queue, monkeypatch):
    deferrer, configured = _wire(queue, monkeypatch, 7)
    result = await queue.enqueue("jobs.locked", {"n": 1})
    assert result == Enqueued(job_id="7")
    assert configured == []
    assert deferrer.payloads == [{"n": 1}]


async def test_already_enqueued_comes_back_coalesced(queue, monkeypatch):
    from procrastinate.exceptions import AlreadyEnqueued

    _wire(queue, monkeypatch, AlreadyEnqueued("cube:s"))
    result = await queue.enqueue(
        "jobs.locked", {"cube_sink_id": "s"}, lock="cube:s", queueing_lock="cube:s"
    )
    assert result == Enqueued(job_id=None, coalesced=True)


class _FakeJobManager:
    """Stands in for app.job_manager: records calls, collides on demand."""

    def __init__(self, stalled, collide=(), other_violation=False, gone=()):
        self.stalled = stalled
        self.collide = set(collide)
        self.other_violation = other_violation
        #: jobs that left 'doing' between the select and the retry
        self.gone = set(gone)
        self.calls: list[tuple] = []

    async def get_stalled_jobs(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self.stalled

    async def retry_job(self, job):
        from procrastinate.exceptions import ConnectorException, UniqueViolation
        from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT

        self.calls.append(("retry", job.id))
        if job.id in self.gone:
            raise ConnectorException(
                f"Job was not found or has an invalid status to retry (job id: {job.id})"
            )
        if self.other_violation:
            raise UniqueViolation(constraint_name="some_other_idx", queueing_lock=None)
        if job.id in self.collide:
            raise UniqueViolation(
                constraint_name=QUEUEING_LOCK_CONSTRAINT, queueing_lock=job.queueing_lock
            )

    async def finish_job(self, job, status, delete_job):
        self.calls.append(("finish", job.id, status, delete_job))


def _stalled_job(job_id: int, attempts: int = 0):
    from procrastinate.jobs import Job

    return Job(
        id=job_id,
        queue="default",
        lock="cube:s",
        queueing_lock="cube:s",
        task_name="pipeline.cube_append",
        attempts=attempts,
    )


def _wire_manager(queue: ProcrastinateQueue, monkeypatch, manager: _FakeJobManager) -> None:
    async def fake_open():
        pass

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(queue.app, "job_manager", manager)


async def test_retry_stalled_requeues_and_closes_covered_jobs(queue, monkeypatch):
    from procrastinate.jobs import Status

    from pipeline.queue.procrastinate_backend import STALLED_WORKER_SECONDS

    manager = _FakeJobManager([_stalled_job(1), _stalled_job(2)], collide={2})
    _wire_manager(queue, monkeypatch, manager)
    assert await queue.retry_stalled("pipeline.cube_append") == 2
    get_kwargs = {
        "task_name": "pipeline.cube_append",
        "seconds_since_heartbeat": STALLED_WORKER_SECONDS,
    }
    assert manager.calls == [
        ("get", get_kwargs),
        ("retry", 1),
        ("retry", 2),
        ("finish", 2, Status.FAILED, False),
    ]


async def test_retry_stalled_reraises_any_other_unique_violation(queue, monkeypatch):
    from procrastinate.exceptions import UniqueViolation

    _wire_manager(queue, monkeypatch, _FakeJobManager([_stalled_job(1)], other_violation=True))
    with pytest.raises(UniqueViolation):
        await queue.retry_stalled("pipeline.cube_append")


async def test_retry_stalled_with_nothing_stalled_is_a_no_op(queue, monkeypatch):
    manager = _FakeJobManager([])
    _wire_manager(queue, monkeypatch, manager)
    assert await queue.retry_stalled("pipeline.cube_append") == 0
    assert [c[0] for c in manager.calls] == ["get"]


def _recovery_outcomes(caplog) -> dict[int, str]:
    return {
        record.job_id: record.outcome
        for record in caplog.records
        if record.getMessage() == "stalled job recovered"
    }


async def test_retry_stalled_gives_up_on_a_job_at_the_attempt_cap(queue, monkeypatch, caplog):
    """Z-3 final review: Procrastinate's retry_job bumps ``attempts`` on every
    recovery, so a job that kills its worker each run reaches the cap and is
    closed failed instead of being requeued again. (This bounds one job's
    requeues; crash-loop protection for re-enqueued work is the ledger's.)"""
    import logging

    from procrastinate.jobs import Status

    from pipeline.queue.procrastinate_backend import MAX_STALLED_ATTEMPTS

    manager = _FakeJobManager(
        [
            _stalled_job(1, attempts=MAX_STALLED_ATTEMPTS),
            _stalled_job(2, attempts=MAX_STALLED_ATTEMPTS - 1),
        ]
    )
    _wire_manager(queue, monkeypatch, manager)
    with caplog.at_level(logging.WARNING, logger="pipeline.queue.procrastinate_backend"):
        assert await queue.retry_stalled("pipeline.cube_append") == 2
    assert manager.calls[1:] == [
        ("finish", 1, Status.FAILED, False),
        ("retry", 2),
    ]
    assert _recovery_outcomes(caplog) == {1: "gave_up", 2: "requeued"}


async def test_retry_stalled_skips_a_job_it_cannot_recover(queue, monkeypatch, caplog):
    """Z-3 final review: a job that left 'doing' between the select and the
    retry must not abort the loop (nor the kick's stale-sink enqueue)."""
    import logging

    manager = _FakeJobManager([_stalled_job(1), _stalled_job(2)], gone={1})
    _wire_manager(queue, monkeypatch, manager)
    with caplog.at_level(logging.WARNING, logger="pipeline.queue.procrastinate_backend"):
        assert await queue.retry_stalled("pipeline.cube_append") == 1
    assert manager.calls[1:] == [("retry", 1), ("retry", 2)]
    assert _recovery_outcomes(caplog) == {2: "requeued"}
    # A skip is not a recovery: its own message, with the exception attached,
    # so a DB outage mid-loop doesn't read as N "recovered" warnings.
    [skip] = [
        r for r in caplog.records if r.getMessage() == "stalled job not recovered; skipped"
    ]
    assert (skip.job_id, skip.outcome) == (1, "skipped")
    assert skip.exc_info is not None


async def test_retry_stalled_skips_a_job_it_cannot_close(queue, monkeypatch):
    """The collision path's finish_job can fail the same way (the job left
    'doing' meanwhile): skipped, and the loop goes on."""
    from procrastinate.exceptions import ConnectorException

    class _FinishFails(_FakeJobManager):
        async def finish_job(self, job, status, delete_job):
            await super().finish_job(job, status, delete_job)
            raise ConnectorException('Job was not found or not in "doing" or "todo" status')

    manager = _FinishFails([_stalled_job(1), _stalled_job(2)], collide={1})
    _wire_manager(queue, monkeypatch, manager)
    assert await queue.retry_stalled("pipeline.cube_append") == 1
    assert [c[:2] for c in manager.calls[1:]] == [("retry", 1), ("finish", 1), ("retry", 2)]
