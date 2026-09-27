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
- ``list_launched`` exists because the caller's ``reap`` is not enough on its
  own: a worker killed between launch and reap never runs the ``finally``, so
  the platform needs a way to ENUMERATE what it started and reconcile it
  against the ledger (M3-W-1). Every backend must be able to answer it —
  a backend that cannot list what it launched cannot be operated.
"""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass, field

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


class ImagePullFailed(ExecutorError):
    """The daemon refused or could not pull a user image by digest (C-2,
    spec §8.4). A per-run OUTCOME, not an outage: the registry may have
    deleted the manifest or revoked the credential, so the caller spends an
    attempt rather than requeueing forever."""


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
    # existing construction valid; no executor reads them yet (K-3 does).
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

    @abc.abstractmethod
    def list_launched(self) -> list[LaunchedRun]:
        """Every run the backend still holds resources for, INCLUDING those
        that have already exited — an exited-but-not-removed run is precisely
        the orphan the reaper is looking for.

        Raises :class:`ExecutorUnavailable` when the backend cannot be asked;
        an outage must never be reported as an empty list, because the caller
        would read that as "nothing to reap".
        """
