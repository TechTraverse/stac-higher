"""The executor interface process runs depend on (ADR 0013, spec §4).

Same shape as the queue interface (ROADMAP §1 "Topology"): business logic
sees ONE seam, deployments pick a backend. Slice 1 ships
:class:`~pipeline.process.docker_executor.DockerExecutor` for compose/dev;
ECS/Fargate RunTask is the Phase 8 cloud candidate (paper-only until then —
ADR 0013's P9-A appendix).

**This is a security-critical seam** (ADR 0013 invariants), and the interface
is shaped to make the invariants enforceable rather than merely intended:

- ``RunSpec.env`` is the COMPLETE environment of the run. There is no
  inherit-from-worker path, so "the run must never see the DB URL or the
  platform's object-store keys" is a property of the type, not a habit.
- Limits ride on the spec and are applied by the backend at create time, so a
  run cannot opt out of them.
- ``wait`` takes the timeout and is required to KILL on expiry: a wedged run
  fails itself, it does not degrade the worker.
- ``reap`` is always called by the caller, and backends must not rely on
  container auto-removal to do it (ADR 0013: "AutoRemove not trusted").
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


class ExecutorError(Exception):
    """Base class for executor failures."""


class ExecutorUnavailable(ExecutorError):
    """The backend is unreachable or misconfigured — a run cannot start.

    Distinct from a run that started and failed: this is infrastructure, so
    the caller retries the JOB rather than marking the run's attempt spent.
    """


class RunTimeout(ExecutorError):
    """The run exceeded its wall-clock budget and was killed."""


@dataclass(frozen=True)
class RunSpec:
    """Everything needed to start one run — and nothing implicit."""

    run_id: str
    process_id: str
    image: str
    #: The COMPLETE environment. Whatever is not here does not reach the run:
    #: no DATABASE_URL, no CREDENTIALS_MASTER_KEY, no platform S3 keys
    #: (ADR 0013). Populated by the launch path from the revision's resolved
    #: env plus the run-scoped storage credentials.
    env: dict[str, str] = field(default_factory=dict)
    #: Container-side entrypoint override; empty = the image's own.
    cmd: tuple[str, ...] = ()
    memory_mb: int = 512
    timeout_seconds: int = 900
    #: Docker network name / cloud equivalent. The egress-policy analog at the
    #: executor's network boundary (ADR 0013): runs attach to a dedicated
    #: restricted network, never the platform's own.
    network: str | None = None


@dataclass(frozen=True)
class RunHandle:
    """Backend-opaque identity of a started run."""

    id: str
    #: Free-form backend detail for logging (never trusted as content).
    backend: str = ""


@dataclass(frozen=True)
class ExitStatus:
    exit_code: int
    #: True when the backend killed the run for exceeding its budget. The
    #: exit code alone cannot say this — a killed container reports 137, and
    #: so does any other SIGKILL.
    timed_out: bool = False
    #: Backend-reported failure text, when the run never produced an exit code.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Executor(abc.ABC):
    """Launch, await, read the logs of, and reap one isolated run."""

    #: short identifier surfaced in logs and /health ("docker", "memory")
    name: str

    @abc.abstractmethod
    def launch(self, spec: RunSpec) -> RunHandle:
        """Create and start the run. Raises :class:`ExecutorUnavailable` when
        the backend itself is the problem."""

    @abc.abstractmethod
    def wait(self, handle: RunHandle, timeout_seconds: int) -> ExitStatus:
        """Block until the run exits or the budget expires, KILLING it on
        expiry and returning ``timed_out=True``. Never raises for a run that
        merely failed — a non-zero exit is a result, not an error."""

    @abc.abstractmethod
    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        """The run's combined stdout/stderr, truncated to ``max_bytes``.

        UNTRUSTED CONTENT (ADR 0013): the caller writes this to object storage
        and never interleaves it into the pipeline's own logs.
        """

    @abc.abstractmethod
    def reap(self, handle: RunHandle) -> None:
        """Remove the run's resources. Called in a finally, must be
        idempotent, and must not raise for an already-gone run."""
