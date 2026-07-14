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
from typing import Any

JobHandler = Callable[..., Awaitable[None] | None]
JobPayload = Mapping[str, Any]


class QueueError(Exception):
    """Base class for queue failures."""


class QueueConnectionError(QueueError):
    """The queue backend is unreachable or not provisioned."""


class QueueBackend(abc.ABC):
    """Enqueue jobs, register handlers, and run the worker — backend-agnostic."""

    #: short identifier surfaced in /health ("procrastinate", "memory", "sqs")
    name: str

    @abc.abstractmethod
    def register_task(self, func: JobHandler, *, name: str) -> None:
        """Register ``func`` as the handler for jobs named ``name``."""

    @abc.abstractmethod
    def register_periodic(self, func: JobHandler, *, name: str, cron: str) -> None:
        """Register ``func`` to run on a cron schedule (5-field cron syntax).

        The handler receives ``timestamp: int`` — the unix time of the tick —
        which makes scheduled runs idempotent across worker restarts.
        """

    @abc.abstractmethod
    async def enqueue(self, job_name: str, payload: JobPayload | None = None) -> str:
        """Enqueue one job; returns a backend-scoped job id."""

    @abc.abstractmethod
    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        """Enqueue many jobs of the same task in one backend round trip."""

    @abc.abstractmethod
    async def setup(self) -> None:
        """Idempotently provision backend infrastructure (schema, queues)."""

    @abc.abstractmethod
    async def run_worker(self) -> None:
        """Consume and execute jobs until cancelled."""

    @abc.abstractmethod
    async def check_connection(self) -> None:
        """Raise :class:`QueueConnectionError` if the backend is unreachable."""

    async def aclose(self) -> None:  # noqa: B027 — optional hook, default no-op
        """Release backend resources (connection pools). Safe to call twice."""
