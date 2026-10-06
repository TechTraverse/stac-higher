"""The queue interface business logic depends on.

Roadmap-locked design (ROADMAP §1 "Topology"): the job queue sits behind an
interface with per-deployment backends — Procrastinate (PostgreSQL
LISTEN/NOTIFY, default) now, SQS in Phase 8. Jobs are batch-oriented: one job
carries N files/items, so the job rate stays low at envelope scale.

Contract notes:

- Handlers are async or sync callables invoked with the payload's keys as
  keyword arguments. Payloads must be JSON-serializable.
- Periodic handlers additionally receive ``timestamp`` (int, unix seconds of
  the scheduled tick) as their first keyword argument.
- ``job_name`` is a stable string identity; the same name must be registered
  on the worker that executes it.
- ``setup()`` is idempotent one-time infrastructure preparation (Procrastinate:
  create/apply its schema; SQS later: validate queue existence).
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

JobHandler = Callable[..., Awaitable[None] | None]
JobPayload = Mapping[str, Any]


@dataclass(frozen=True)
class RetrySpec:
    """Queue-level retry for transient handler failures (ISSUES I-55).

    Smooths over faults the handler cannot see coming (DB connection drops,
    brief network blips) by re-attempting the job. Durable recovery — worker
    crashes, persistent failures — stays with the ledger sweeps; this only
    keeps one bad moment from failing a job permanently.
    """

    #: total attempts, including the first (backend maps as supported)
    max_attempts: int
    #: fixed wait between attempts, in seconds
    wait_seconds: int = 0


@dataclass(frozen=True)
class Enqueued:
    """What one ``enqueue`` did (virtual cube spec §5.1, decision §14.2).

    ``coalesced`` means a job with the same ``queueing_lock`` was already
    waiting, so nothing new was queued. That is success: the waiting job
    will see whatever the caller just wrote. Backends return it instead of
    raising, so no caller can turn coalescing into a retry loop.
    """

    #: the backend-scoped id of the new job; None when coalesced
    job_id: str | None
    coalesced: bool = False


#: Procrastinate's own default queue name — every task and periodic that does
#: not say otherwise. Runs with WORKER_CONCURRENCY - WORKER_BYTES_CONCURRENCY slots.
QUEUE_DEFAULT = "default"
#: The bounded queue (M3-D): jobs that hold object bytes or a GDAL block cache
#: — ingest FETCH/ITEMIZE, deliver. Runs with WORKER_BYTES_CONCURRENCY slots,
#: so resident memory is bounded by that number, not by the total.
QUEUE_BYTES = "bytes"


class QueueError(Exception):
    """Base class for queue failures."""


class QueueConnectionError(QueueError):
    """The queue backend is unreachable or not provisioned."""


class QueueBackend(abc.ABC):
    """Enqueue jobs, register handlers, and run the worker — backend-agnostic."""

    #: short identifier surfaced in /health ("procrastinate", "memory", "sqs")
    name: str

    @abc.abstractmethod
    def register_task(
        self,
        func: JobHandler,
        *,
        name: str,
        retry: RetrySpec | None = None,
        queue: str = QUEUE_DEFAULT,
    ) -> None:
        """Register ``func`` as the handler for jobs named ``name``.

        ``retry`` opts the task into queue-level retries on handler
        exceptions; without it a failure is terminal after one attempt.
        ``queue`` names the worker pool the job runs in (M3-D): ``QUEUE_DEFAULT``
        unless the handler holds bytes.
        """

    @abc.abstractmethod
    def register_periodic(self, func: JobHandler, *, name: str, cron: str) -> None:
        """Register ``func`` to run on a cron schedule (5-field cron syntax).

        The handler receives ``timestamp: int`` — the unix time of the tick —
        which makes scheduled runs idempotent across worker restarts.
        """

    @abc.abstractmethod
    async def enqueue(
        self,
        job_name: str,
        payload: JobPayload | None = None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Enqueued:
        """Enqueue one job.

        ``lock``: jobs sharing it never run at the same time (one writer per
        cube repository). ``queueing_lock``: while a job holding it is still
        waiting, another enqueue with it is refused and comes back as
        ``Enqueued(job_id=None, coalesced=True)``. A job that is already
        running does not hold its ``queueing_lock``, so it can enqueue its own
        successor.
        """

    @abc.abstractmethod
    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        """Enqueue many jobs of the same task in one backend round trip."""

    @abc.abstractmethod
    async def retry_stalled(self, job_name: str) -> int:
        """Hand ``job_name`` jobs that a dead worker left running back to the
        queue; returns how many were handled.

        A dead worker's job keeps its ``lock`` forever otherwise, so every
        later same-lock job waits behind it. If a waiting job already holds
        the stalled job's ``queueing_lock``, the stalled job is closed as
        failed instead: the waiting job does the same work, and requeueing
        would collide with it.

        Recovery is capped: the Procrastinate backend closes a stalled job
        failed instead of requeueing it once it has been recovered
        ``MAX_STALLED_ATTEMPTS`` (3) times, so a job that kills its worker
        on every run cannot crash-loop it. A job that cannot be touched (it
        left running meanwhile) is skipped, not counted, and the rest are
        still handled. The in-memory backend has no attempt counter (its
        tests strand jobs explicitly) and recovers without a cap.
        """

    @abc.abstractmethod
    async def setup(self) -> None:
        """Idempotently provision backend infrastructure (schema, queues)."""

    @abc.abstractmethod
    async def run_worker(self, *, concurrency: int, bytes_concurrency: int) -> None:
        """Consume and execute jobs until cancelled.

        ``concurrency`` is the total job slots; ``bytes_concurrency`` is how
        many of those belong to the byte-holding ``QUEUE_BYTES`` jobs (M3-D).
        A backend without separate worker pools (e.g. the in-memory test
        backend) may ignore both.
        """

    @abc.abstractmethod
    async def check_connection(self) -> None:
        """Raise :class:`QueueConnectionError` if the backend is unreachable."""

    async def aclose(self) -> None:  # noqa: B027 — optional hook, default no-op
        """Release backend resources (connection pools). Safe to call twice."""
