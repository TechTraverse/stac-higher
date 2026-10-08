"""In-memory queue backend for unit tests.

Executes nothing on its own: tests call :meth:`run_pending` (or
:meth:`run_periodic`) to drive handlers deterministically. ``queueing_lock``
and ``lock`` follow Procrastinate's semantics (virtual cube spec §5.1): a
second enqueue while a same-``queueing_lock`` job is waiting is coalesced,
and same-``lock`` jobs never run at once.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.queue.interface import (
    QUEUE_DEFAULT,
    Enqueued,
    JobHandler,
    JobPayload,
    QueueBackend,
    QueueConnectionError,
    QueueError,
    RetrySpec,
)


@dataclass
class Job:
    id: str
    name: str
    payload: dict[str, Any]
    status: str = "pending"  # pending | running | done | failed
    lock: str | None = None
    queueing_lock: str | None = None
    #: set by strand(): running on a worker that died (retry_stalled's target)
    stalled: bool = False


@dataclass
class PeriodicSpec:
    func: JobHandler
    cron: str


@dataclass
class InMemoryQueue(QueueBackend):
    name: str = "memory"
    #: flip to simulate an unreachable backend in health tests
    connected: bool = True
    is_set_up: bool = False
    tasks: dict[str, JobHandler] = field(default_factory=dict)
    periodic: dict[str, PeriodicSpec] = field(default_factory=dict)
    #: retry specs by task name (recorded for assertions; run_pending stays
    #: single-shot — tests drive re-attempts explicitly)
    retry_specs: dict[str, RetrySpec] = field(default_factory=dict)
    #: queue name by task name (recorded for assertions; M3-D)
    queues: dict[str, str] = field(default_factory=dict)
    jobs: list[Job] = field(default_factory=list)
    _next_id: int = 1

    def register_task(
        self,
        func: JobHandler,
        *,
        name: str,
        retry: RetrySpec | None = None,
        queue: str = QUEUE_DEFAULT,
    ) -> None:
        if name in self.tasks or name in self.periodic:
            raise QueueError(f"task already registered: {name}")
        self.tasks[name] = func
        if retry is not None:
            self.retry_specs[name] = retry
        self.queues[name] = queue

    def register_periodic(self, func: JobHandler, *, name: str, cron: str) -> None:
        if name in self.tasks or name in self.periodic:
            raise QueueError(f"task already registered: {name}")
        self.periodic[name] = PeriodicSpec(func=func, cron=cron)

    async def enqueue(
        self,
        job_name: str,
        payload: JobPayload | None = None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Enqueued:
        if job_name not in self.tasks:
            raise QueueError(f"unknown task: {job_name}")
        if queueing_lock is not None and any(
            job.queueing_lock == queueing_lock and job.status == "pending"
            for job in self.jobs
        ):
            # Procrastinate refuses only while the holder is waiting ('todo').
            return Enqueued(job_id=None, coalesced=True)
        job = self._add_job(job_name, payload, lock=lock, queueing_lock=queueing_lock)
        return Enqueued(job_id=job.id)

    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        return [self._add_job(job_name, payload).id for payload in payloads]

    def _add_job(
        self,
        job_name: str,
        payload: JobPayload | None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Job:
        if job_name not in self.tasks:
            raise QueueError(f"unknown task: {job_name}")
        job = Job(
            id=str(self._next_id),
            name=job_name,
            payload=dict(payload or {}),
            lock=lock,
            queueing_lock=queueing_lock,
        )
        self._next_id += 1
        self.jobs.append(job)
        return job

    async def retry_stalled(self, job_name: str) -> int:
        recovered = 0
        for job in self.jobs:
            if job.name != job_name or not job.stalled:
                continue
            job.stalled = False
            covered = job.queueing_lock is not None and any(
                other.queueing_lock == job.queueing_lock and other.status == "pending"
                for other in self.jobs
            )
            job.status = "failed" if covered else "pending"
            recovered += 1
        return recovered

    async def setup(self) -> None:
        self.is_set_up = True

    async def run_worker(self, *, concurrency: int = 0, bytes_concurrency: int = 0) -> None:
        # No worker pools to size in-memory; kept for ABC parity with
        # ProcrastinateQueue (M3-D fix round 1).
        await self.run_pending()

    async def check_connection(self) -> None:
        if not self.connected:
            raise QueueConnectionError("in-memory queue marked disconnected")

    # -- test drivers ------------------------------------------------------

    def strand(self, job_id: str) -> None:
        """Leave a job ``running`` on a worker that died (a SIGKILL mid-job).
        It keeps its lock until :meth:`retry_stalled` recovers it."""
        job = next(j for j in self.jobs if j.id == job_id)
        job.status, job.stalled = "running", True

    async def run_pending(self) -> int:
        """Execute all pending jobs; returns how many ran.

        A job whose ``lock`` is held by a running job is left pending for a
        later pass (re-entrant drives see this; a sequential pass never
        overlaps anyway).
        """
        ran = 0
        for job in self.jobs:
            if job.status != "pending" or self._lock_held(job.lock):
                continue
            job.status = "running"
            try:
                await _call(self.tasks[job.name], **job.payload)
                job.status = "done"
            except Exception:
                job.status = "failed"
                raise
            ran += 1
        return ran

    def _lock_held(self, lock: str | None) -> bool:
        # Procrastinate: a 'doing' job holds its lock until it finishes, even
        # when its worker has died (retry_stalled releases those).
        return lock is not None and any(
            job.lock == lock and job.status == "running" for job in self.jobs
        )

    async def run_periodic(self, name: str, timestamp: int) -> None:
        """Simulate one scheduled tick of a periodic task."""
        await _call(self.periodic[name].func, timestamp=timestamp)


async def _call(func: JobHandler, **kwargs: Any) -> None:
    result = func(**kwargs)
    if inspect.isawaitable(result):
        await result
