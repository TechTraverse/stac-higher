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
- The backend enforces the run's timeout: server-side (a Kubernetes Job's
  ``activeDeadlineSeconds``) or on read (Docker: ``status()`` kills a run past
  ``StartedAt + timeout`` and reports ``timed_out``). A wedged run fails
  itself; it never degrades the worker.
- ``reap`` is always called by the caller, and backends must not rely on
  container auto-removal to do it (ADR 0013: "AutoRemove not trusted").
- ``list_launched`` exists because the caller's ``reap`` is not enough on its
  own: a worker killed between submit and reap never reaches it, so the
  platform needs a way to ENUMERATE what it started and reconcile it against
  the ledger (M3-W-1). Every backend must be able to answer it — a backend
  that cannot list what it launched cannot be operated.

**Submit, then reconcile (K-4, spec §5, ADR 0019).** ``submit`` creates the
run and returns at once; nothing blocks on a run. The pipeline's Reconciler
(``process/reconcile.py``) reads ``status()`` and moves the ledger. The
contract every backend keeps — K-5's pre-flight checklist:

- **``status()`` is the truth; events are wake-ups (R1).** ``watch()`` is
  optional (``supports_watch``); every (re)connect of its stream yields
  :data:`RESYNC` first, the watcher reconnects when the stream raises, and an
  event never carries a state the caller trusts — the Reconciler re-reads
  ``status()`` before every transition.
- ``status()`` raises :class:`ExecutorUnavailable` when the backend cannot be
  asked, and reports ``lost`` ONLY when the backend affirms the run is gone
  (an outage is not an absence — the reaper's rule).
- ``RunState.detail`` is the backend's own words for ``pending_capacity``
  (the UI's tooltip) and for ``lost`` (the run's error), never trusted as
  content.
- ``cancel()`` stops the run and KEEPS it, so ``logs()`` and ``reap()`` still
  work; removal is ``reap``, called only after the verdict commits.
- ``submitted_phase`` is what a just-submitted run is on this backend
  (``pending_capacity`` by default, for backends where a submitted run can sit
  queued). Submissions are recorded per backend (``executor_backend``), and a
  Reconciler skips runs another backend submitted.
- Every call here is blocking; async callers go through ``asyncio.to_thread``.
- A backend refusal at submit that should kill the run (not requeue it) is a
  :class:`HardwareUnavailable` (or subclass): the claim path maps it to dead.
"""

from __future__ import annotations

import abc
import contextlib
import datetime as dt
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import ClassVar

from pipeline.process.hardware import HardwareProfile

#: Kueue's two priority classes (K-5/K-6) — unread until then.
PRIORITIES = ("interactive", "triggered")

#: What a run is (C-2): a process run, judged against ``process_runs``, or an
#: image scan, judged against ``image_scans``. The reaper keeps them apart.
RUN_KINDS = ("process", "image_scan")


class ExecutorError(Exception):
    """Base class for executor failures."""


class ExecutorUnavailable(ExecutorError):
    """The backend is unreachable or misconfigured — a run cannot start.

    Distinct from a run that started and failed: this is infrastructure, so
    the caller retries the JOB rather than marking the run's attempt spent.
    """


class RunTimeout(ExecutorError):
    """The run exceeded its wall-clock budget and was killed."""


class HardwareUnavailable(ExecutorError):
    """The run asks for hardware this backend or host cannot provide (K-3):
    GPUs on a Docker host without an NVIDIA container runtime, or more CPUs
    than the host has. Configuration, not a fault: the run dies naming the
    profile and never requeues. Deliberately NOT an
    :class:`ExecutorUnavailable`, whose branch never spends an attempt."""


class ImagePullFailed(ExecutorError):
    """The daemon refused or could not pull a user image by digest (C-2,
    spec §8.4). A per-run OUTCOME, not an outage: the registry may have
    deleted the manifest or revoked the credential, so the caller spends an
    attempt rather than requeueing forever."""


class ImageMissing(ExecutorError):
    """The daemon has no image by the name the run launches on (#108): the
    create call answered ``404 No such image``. A deployment fault retrying
    will not fix -- the platform image was never built -- so, like
    :class:`ImagePullFailed`, the caller spends an attempt and the run dies
    on budget, which raises the ``process_failed`` alert instead of an
    endless, silent requeue. Deliberately NOT an :class:`ExecutorUnavailable`:
    that branch never spends an attempt."""


@dataclass(frozen=True)
class RegistryAuth:
    """A pull credential for one registry, resolved at launch (spec §8.4) and
    handed only to the daemon. ``password`` never appears in a repr, a log
    line or an error message."""

    username: str
    password: str = field(repr=False)
    server: str = ""


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
    #: Docker ``Cmd`` override: a kind-3 revision's ``command``. Empty = the
    #: image's own. It never overrides ``Entrypoint`` or ``User``.
    cmd: tuple[str, ...] = ()
    memory_mb: int = 512
    timeout_seconds: int = 900
    #: Docker network name / cloud equivalent. The egress-policy analog at the
    #: executor's network boundary (ADR 0013): runs attach to a dedicated
    #: restricted network, never the platform's own.
    network: str | None = None
    # K-1 (process-compute spec §4): the hardware the run asked for, resolved
    # against the deployment's profile set at launch. Defaults keep every
    # existing construction valid. The Docker executor reads them for
    # process runs since K-3 (`NanoCpus`, `DeviceRequests`).
    cpu: float = 1.0
    gpu_count: int = 0
    profile: HardwareProfile | None = None
    #: "interactive" for a UI test run, "triggered" otherwise — Kueue's two
    #: priority classes (K-5/K-6); unread until then.
    priority: str = "triggered"
    #: One of RUN_KINDS. A scan's ``process_id`` carries the IMAGE id.
    kind: str = "process"
    #: C-2 (spec §3.2/§8.4): True for kinds 2-3. The backend then refuses an
    #: image not pinned by digest, pulls it if absent, and forces the
    #: platform's hardening whatever the image says: uid 10001, a /tmp tmpfs.
    user_image: bool = False
    #: Entrypoint override. Only the kind-2 bootstrap sets one; empty = the
    #: image's own.
    entrypoint: tuple[str, ...] = ()
    #: Pull credential for a user image; None = anonymous.
    registry_auth: RegistryAuth | None = None
    #: K-3 ruling: an EXTRACTOR run (G-6, association-triggered) on the
    #: DEFAULT profile is ingest work, so the executor does not cap its CPU
    #: (and the claim does not count it against capacity). An extractor on
    #: any other profile is capped like a process run (`repo.capacity_exempt`).
    cpu_exempt: bool = False


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


@dataclass(frozen=True)
class LaunchedRun:
    """One run the backend still holds resources for — what the reaper sees.

    Deliberately the backend's view, not the ledger's: the whole point of the
    reaper is to find things the ledger has forgotten about.
    """

    handle: RunHandle
    #: The `stac-higher.run-id` this was launched for.
    run_id: str
    #: When the BACKEND says it created the run, or None when it does not say.
    #: A missing time is not an orphan signal — it just means the age rule
    #: cannot apply and the ledger decides alone.
    created_at: dt.datetime | None = None
    #: One of RUN_KINDS, from the backend's own label. A container launched
    #: before C-2 carries no kind label and reads as a process run.
    kind: str = "process"


def oom_error(memory_mb: int | None) -> str:
    """One wording for an out-of-memory kill, whatever the backend (K-4/K-5)."""
    limit = f" ({memory_mb} MB)" if memory_mb else ""
    return f"run was killed for exceeding its memory limit{limit}"


@dataclass(frozen=True)
class RunState:
    """What ``status()`` reports (spec §5.1)."""

    #: One of ``ledger.EXECUTOR_PHASES``: pending_capacity, starting, running,
    #: exited, lost.
    phase: str
    #: The backend's own words: the pending reason, the loss reason. A UI
    #: tooltip or an error string, never trusted as content.
    detail: str | None = None
    #: Set iff ``phase == "exited"``.
    exit: ExitStatus | None = None


@dataclass(frozen=True)
class RunEvent:
    """A wake-up from ``watch()``. Carries an identity, never a state."""

    #: None = RESYNC: reconcile everything submitted.
    run_id: str | None
    handle_id: str | None = None
    #: One of RUN_KINDS, from the backend's label.
    kind: str = "process"


#: Yielded first on every (re)connect of a watch stream.
RESYNC = RunEvent(run_id=None)


class Executor(abc.ABC):
    """Submit, inspect, cancel, read the logs of, and reap one isolated run."""

    #: short identifier surfaced in logs and stored as ``executor_backend``
    #: ("docker", "memory")
    name: str
    #: The phase ``record_submission`` writes: what a just-submitted run is on
    #: this backend.
    submitted_phase: ClassVar[str] = "pending_capacity"

    @abc.abstractmethod
    def submit(self, spec: RunSpec) -> RunHandle:
        """Create and start the run; return at once. Raises
        :class:`ExecutorUnavailable` when the backend itself is the problem,
        :class:`ImagePullFailed` / :class:`ImageMissing` when the run's image
        is, and :class:`HardwareUnavailable` when the backend refuses what the
        profile asks for."""

    @abc.abstractmethod
    def status(self, handle: RunHandle) -> RunState:
        """One read of the run's state; never blocks on the run. May enforce
        the backend's deadline (Docker). Raises :class:`ExecutorUnavailable`
        when the backend cannot be asked; ``lost`` only on confirmed absence."""

    @abc.abstractmethod
    def cancel(self, handle: RunHandle) -> None:
        """Stop the run, idempotently. Its resources are KEPT for ``logs``
        and ``reap``. Raises :class:`ExecutorUnavailable` when the backend
        cannot be asked (the run may still be running)."""

    @abc.abstractmethod
    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        """The run's combined stdout/stderr, truncated to ``max_bytes``.

        UNTRUSTED CONTENT (ADR 0013): the caller writes this to object storage
        and never interleaves it into the pipeline's own logs.
        """

    @abc.abstractmethod
    def reap(self, handle: RunHandle) -> None:
        """Remove the run's resources. Must be idempotent and must not raise
        for an already-gone run."""

    @abc.abstractmethod
    def list_launched(self) -> list[LaunchedRun]:
        """Every run the backend still holds resources for, INCLUDING those
        that have already exited — an exited-but-not-removed run is precisely
        the orphan the reaper is looking for.

        Raises :class:`ExecutorUnavailable` when the backend cannot be asked;
        an outage must never be reported as an empty list, because the caller
        would read that as "nothing to reap".
        """

    def watch(self) -> AsyncIterator[RunEvent]:
        """Optional push of state changes: :data:`RESYNC` first, then one
        event per change of a run this backend submitted. Raises
        :class:`ExecutorUnavailable` when the stream cannot be opened or
        drops; the caller reconnects."""
        raise NotImplementedError(f"{type(self).__name__} has no watch()")

    @property
    def supports_watch(self) -> bool:
        return type(self).watch is not Executor.watch


#: How a lost run reads when the backend gave no reason.
LOST_ERROR = "run lost by the executor"


def run_to_exit(
    executor: Executor,
    handle: RunHandle,
    timeout_seconds: int,
    *,
    poll_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ExitStatus:
    """Block until a submitted run exits — for BLOCKING callers only (image
    scans, R11). Polls ``status()``; past ``timeout_seconds`` on its own clock
    (the backend may enforce none) it cancels and reports ``timed_out``. An
    unreachable backend is ridden out until the deadline; a lost run is an
    error exit. Not on the ABC: the run path never waits."""
    deadline = clock() + timeout_seconds
    while True:
        try:
            state = executor.status(handle)
        except ExecutorUnavailable:
            state = None
        if state is not None and state.phase == "exited":
            return state.exit or ExitStatus(-1, error="exited without a status")
        if state is not None and state.phase == "lost":
            return ExitStatus(-1, error=state.detail or LOST_ERROR)
        if clock() >= deadline:
            # An unreachable backend: the caller reaps (which kills) in its finally.
            with contextlib.suppress(ExecutorUnavailable):
                executor.cancel(handle)
            return ExitStatus(137, timed_out=True)
        sleep(poll_seconds)
