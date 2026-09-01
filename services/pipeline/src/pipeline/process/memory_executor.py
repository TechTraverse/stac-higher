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

from dataclasses import dataclass, field

from pipeline.process.executor import (
    Executor,
    ExitStatus,
    LaunchedRun,
    RunHandle,
    RunSpec,
)


@dataclass
class MemoryExecutor(Executor):
    """Scripted results, full recording, no containers."""

    name: str = "memory"
    #: Result per launch, in order; the last one repeats once exhausted.
    results: list[ExitStatus] = field(default_factory=lambda: [ExitStatus(0)])
    log_output: bytes = b""
    launched: list[RunSpec] = field(default_factory=list)
    reaped: list[str] = field(default_factory=list)
    #: When set, launch raises it — for the backend-unavailable path.
    launch_error: Exception | None = None
    _waits: int = 0

    def launch(self, spec: RunSpec) -> RunHandle:
        if self.launch_error is not None:
            raise self.launch_error
        self.launched.append(spec)
        return RunHandle(id=f"mem-{spec.run_id}", backend=self.name)

    def wait(self, handle: RunHandle, timeout_seconds: int) -> ExitStatus:
        index = min(self._waits, len(self.results) - 1)
        self._waits += 1
        return self.results[index]

    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        return self.log_output[:max_bytes]

    def reap(self, handle: RunHandle) -> None:
        self.reaped.append(handle.id)

    def list_launched(self) -> list[LaunchedRun]:
        # Launched-minus-reaped is the fake's honest analogue of "resources
        # the backend still holds", which is what the reaper reconciles.
        return [
            LaunchedRun(handle=RunHandle(id=f"mem-{spec.run_id}", backend=self.name),
                        run_id=spec.run_id)
            for spec in self.launched
            if f"mem-{spec.run_id}" not in self.reaped
        ]
