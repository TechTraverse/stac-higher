"""In-memory queue backend for unit tests.

Executes nothing on its own: tests call :meth:`run_pending` (or
:meth:`run_periodic`) to drive handlers deterministically.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.queue.interface import (
    QUEUE_DEFAULT,
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
    status: str = "pending"  # pending | done | failed


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

    async def enqueue(self, job_name: str, payload: JobPayload | None = None) -> str:
        if job_name not in self.tasks:
            raise QueueError(f"unknown task: {job_name}")
        job = Job(id=str(self._next_id), name=job_name, payload=dict(payload or {}))
        self._next_id += 1
        self.jobs.append(job)
        return job.id

    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        return [await self.enqueue(job_name, payload) for payload in payloads]

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

    async def run_pending(self) -> int:
        """Execute all pending jobs; returns how many ran."""
        ran = 0
        for job in self.jobs:
            if job.status != "pending":
                continue
            try:
                await _call(self.tasks[job.name], **job.payload)
                job.status = "done"
            except Exception:
                job.status = "failed"
                raise
            ran += 1
        return ran

    async def run_periodic(self, name: str, timestamp: int) -> None:
        """Simulate one scheduled tick of a periodic task."""
        await _call(self.periodic[name].func, timestamp=timestamp)


async def _call(func: JobHandler, **kwargs: Any) -> None:
    result = func(**kwargs)
    if inspect.isawaitable(result):
        await result
