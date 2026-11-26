"""In-process fake executor for handler tests (spec §14).

The real :class:`~pipeline.process.docker_executor.DockerExecutor` is
exercised in the M5-G rehearsal and by a DB/socket-gated test; everything
that merely needs "a run happened" uses this — the ``queue/memory.py``
precedent.

It records the specs it was given, which is what lets tests assert the ADR
0013 invariants directly: that the environment contains ONLY what was
intended, that limits were passed through, and that ``reap`` ran even on the
failure path.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from pipeline.process.executor import (
    Executor,
    ExitStatus,
    LaunchedRun,
    RunHandle,
    RunSpec,
    RunState,
)


def exited(
    exit_code: int = 0,
    *,
    timed_out: bool = False,
    error: str | None = None,
    finished_at: dt.datetime | None = None,
) -> RunState:
    """A scripted ``exited`` state."""
    return RunState(
        "exited", exit=ExitStatus(exit_code, timed_out=timed_out, error=error),
        finished_at=finished_at,
    )


@dataclass
class MemoryExecutor(Executor):
    """Scripted states, full recording, no containers."""

    name: str = "memory"
    submitted_phase: str = "starting"
    #: Per run id, the states ``status()`` returns in order; the last repeats.
    states: dict[str, list[RunState]] = field(default_factory=dict)
    #: The script for a run without its own entry in ``states``.
    default_states: list[RunState] = field(default_factory=lambda: [exited(0)])
    log_output: bytes = b""
    #: When set, submit / status / cancel raise it — the backend-down paths.
    submit_error: Exception | None = None
    status_error: Exception | None = None
    cancel_error: Exception | None = None
    submitted: list[RunSpec] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    reaped: list[str] = field(default_factory=list)
    status_calls: list[str] = field(default_factory=list)
    _reads: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def handle_for(run_id: str) -> RunHandle:
        return RunHandle(id=f"mem-{run_id}", backend="memory")

    def _run_id(self, handle: RunHandle) -> str:
        return handle.id.removeprefix("mem-")

    def submit(self, spec: RunSpec) -> RunHandle:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(spec)
        return RunHandle(id=f"mem-{spec.run_id}", backend=self.name)

    def status(self, handle: RunHandle) -> RunState:
        self.status_calls.append(handle.id)
        if self.status_error is not None:
            raise self.status_error
        run_id = self._run_id(handle)
        script = self.states.get(run_id, self.default_states)
        index = min(self._reads.get(run_id, 0), len(script) - 1)
        self._reads[run_id] = self._reads.get(run_id, 0) + 1
        return script[index]

    def cancel(self, handle: RunHandle) -> None:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(handle.id)

    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        return self.log_output[:max_bytes]

    def reap(self, handle: RunHandle) -> None:
        self.reaped.append(handle.id)

    def list_launched(self) -> list[LaunchedRun]:
        # Submitted-minus-reaped is the fake's honest analogue of "resources
        # the backend still holds", which is what the reaper reconciles.
        return [
            LaunchedRun(
                handle=RunHandle(id=f"mem-{spec.run_id}", backend=self.name),
                run_id=spec.run_id,
                kind=spec.kind,
            )
            for spec in self.submitted
            if f"mem-{spec.run_id}" not in self.reaped
        ]
