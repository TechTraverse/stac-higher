"""Procrastinate (PostgreSQL LISTEN/NOTIFY) queue backend.

Only this module imports procrastinate. Constructing the backend opens no
connections; ``setup()`` / ``run_worker()`` / ``check_connection()`` do.

All Procrastinate objects live in a dedicated PostgreSQL schema (default
``procrastinate``) via a per-connection ``search_path`` — the pipeline never
touches ``stac_higher`` (see docs/decisions/0001-migration-ownership.md).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import procrastinate
import psycopg

from pipeline.queue.interface import (
    JobHandler,
    JobPayload,
    QueueBackend,
    QueueConnectionError,
)

logger = logging.getLogger(__name__)


class ProcrastinateQueue(QueueBackend):
    name = "procrastinate"

    def __init__(self, database_url: str, *, schema: str = "procrastinate") -> None:
        if not schema.isidentifier():
            raise ValueError(f"invalid schema name: {schema!r}")
        self.database_url = database_url
        self.schema = schema
        self._opened = False
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=database_url,
                # applied to every pooled connection: keep Procrastinate's
                # objects out of public / stac_higher
                kwargs={"options": f"-c search_path={schema},public"},
            )
        )

    def register_task(self, func: JobHandler, *, name: str) -> None:
        self.app.task(func, name=name)

    def register_periodic(self, func: JobHandler, *, name: str, cron: str) -> None:
        # queueing_lock: if a previous tick is still waiting, skip instead of
        # piling up (procrastinate's periodic deferrer handles the skip).
        task = self.app.task(func, name=name, queueing_lock=name)
        self.app.periodic(cron=cron)(task)

    async def _ensure_open(self) -> None:
        # deferring/working needs the app's connection pool; open it once for
        # the process lifetime (aclose releases it)
        if not self._opened:
            await self.app.open_async()
            self._opened = True

    async def enqueue(self, job_name: str, payload: JobPayload | None = None) -> str:
        await self._ensure_open()
        job_id = await self.app.tasks[job_name].defer_async(**dict(payload or {}))
        return str(job_id)

    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        if not payloads:
            return []
        await self._ensure_open()
        job_ids = await self.app.tasks[job_name].batch_defer_async(
            *[dict(p) for p in payloads]
        )
        return [str(job_id) for job_id in job_ids]

    async def setup(self) -> None:
        """Create the schema and apply Procrastinate's DDL, idempotently.

        ``procrastinate schema --apply`` is not re-runnable (objects already
        exist), so we gate it on the presence of ``procrastinate_jobs``.
        """
        try:
            async with await psycopg.AsyncConnection.connect(
                self.database_url, autocommit=True
            ) as conn:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                cursor = await conn.execute(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_schema = %s AND table_name = 'procrastinate_jobs'",
                    (self.schema,),
                )
                already_applied = await cursor.fetchone() is not None
        except psycopg.Error as exc:
            raise QueueConnectionError(f"cannot reach queue database: {exc}") from exc

        if already_applied:
            logger.info(
                "procrastinate schema already applied", extra={"schema": self.schema}
            )
            return

        await self._ensure_open()
        await self.app.schema_manager.apply_schema_async()
        logger.info("procrastinate schema applied", extra={"schema": self.schema})

    async def run_worker(self) -> None:
        await self._ensure_open()
        await self.app.run_worker_async()

    async def aclose(self) -> None:
        if self._opened:
            self._opened = False
            await self.app.close_async()

    async def check_connection(self) -> None:
        try:
            async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_schema = %s AND table_name = 'procrastinate_jobs'",
                    (self.schema,),
                )
                if await cursor.fetchone() is None:
                    raise QueueConnectionError(
                        f"procrastinate schema not applied in {self.schema!r}"
                    )
        except psycopg.Error as exc:
            raise QueueConnectionError(f"cannot reach queue database: {exc}") from exc
