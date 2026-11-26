"""K-4 executor contract (spec §5.1): submit/status/cancel, optional watch,
``wait`` gone, and the blocking ``run_to_exit`` helper image scans use."""

from __future__ import annotations

import pytest

from pipeline.process.executor import (
    RESYNC,
    Executor,
    ExecutorUnavailable,
    ExitStatus,
    RunHandle,
    RunState,
    run_to_exit,
)
from pipeline.process.memory_executor import MemoryExecutor, exited


def test_wait_is_gone_from_the_interface():
    assert not hasattr(Executor, "wait")
    assert not hasattr(Executor, "launch")
    assert Executor.__abstractmethods__ == {
        "submit", "status", "cancel", "logs", "reap", "list_launched"
    }


class _Minimal(Executor):
    name = "minimal"

    def submit(self, spec): ...
    def status(self, handle): ...
    def cancel(self, handle): ...
    def logs(self, handle, max_bytes): ...
    def reap(self, handle): ...
    def list_launched(self): ...


def test_watch_is_optional():
    minimal = _Minimal()
    assert minimal.supports_watch is False
    with pytest.raises(NotImplementedError):
        minimal.watch()
    assert Executor.submitted_phase == "pending_capacity"
    assert RESYNC.run_id is None


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


H = MemoryExecutor.handle_for("r1")


def test_run_to_exit_returns_the_exit():
    ex = MemoryExecutor(states={"r1": [RunState("starting"), RunState("running"), exited(3)]})
    clock = _Clock()
    assert run_to_exit(ex, H, 60, clock=clock, sleep=clock.sleep) == ExitStatus(3)
    assert len(ex.status_calls) == 3 and ex.cancelled == []


def test_run_to_exit_cancels_on_its_own_deadline():
    ex = MemoryExecutor(states={"r1": [RunState("running")]})
    clock = _Clock()
    status = run_to_exit(ex, H, 10, poll_seconds=2, clock=clock, sleep=clock.sleep)
    assert status == ExitStatus(137, timed_out=True)
    assert ex.cancelled == [H.id]


def test_run_to_exit_reports_lost():
    ex = MemoryExecutor(states={"r1": [RunState("lost", detail="job deleted")]})
    clock = _Clock()
    status = run_to_exit(ex, H, 10, clock=clock, sleep=clock.sleep)
    assert status.exit_code == -1 and status.error == "job deleted"


def test_run_to_exit_rides_out_an_unavailable_backend():
    ex = MemoryExecutor(status_error=ExecutorUnavailable("down"))
    clock = _Clock()
    status = run_to_exit(ex, RunHandle(id="mem-r1"), 6, poll_seconds=2, clock=clock,
                         sleep=clock.sleep)
    assert status.timed_out is True
    assert len(ex.status_calls) >= 3
