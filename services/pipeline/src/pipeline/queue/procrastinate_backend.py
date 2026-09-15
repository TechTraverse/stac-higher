"""Procrastinate (PostgreSQL LISTEN/NOTIFY) queue backend.

Only this module imports procrastinate. Constructing the backend opens no
connections; ``setup()`` / ``run_worker()`` / ``check_connection()`` do.

All Procrastinate objects live in a dedicated PostgreSQL schema (default
``procrastinate``) via a per-connection ``search_path`` — the pipeline never
touches ``stac_higher`` (see docs/decisions/0001-migration-ownership.md).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Sequence

import procrastinate
import psycopg

from pipeline.metrics import instrument_handler
from pipeline.queue.interface import (
    QUEUE_BYTES,
    QUEUE_DEFAULT,
    JobHandler,
    JobPayload,
    QueueBackend,
    QueueConnectionError,
    RetrySpec,
)

logger = logging.getLogger(__name__)


class ProcrastinateQueue(QueueBackend):
    name = "procrastinate"

    def __init__(
        self,
        database_url: str,
        *,
        schema: str = "procrastinate",
        # Fix round 1 (Important #3): unsized, psycopg_pool defaults to
        # max_size=4 — 12 job slots' fetch/finish/heartbeat round trips would
        # share 4 connections and PoolTimeout under load. Defaults here match
        # DEFAULT_WORKER_CONCURRENCY (12) + 4, build_queue overrides max_size
        # from the real settings.
        pool_min_size: int = 2,
        pool_max_size: int = 16,
    ) -> None:
        if not schema.isidentifier():
            raise ValueError(f"invalid schema name: {schema!r}")
        self.database_url = database_url
        self.schema = schema
        self._opened = False
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=database_url,
                min_size=pool_min_size,
                max_size=pool_max_size,
                # applied to every pooled connection: keep Procrastinate's
                # objects out of public / stac_higher
                kwargs={"options": f"-c search_path={schema},public"},
            )
        )

    def register_task(
        self,
        func: JobHandler,
        *,
        name: str,
        retry: RetrySpec | None = None,
        queue: str = QUEUE_DEFAULT,
    ) -> None:
        # Procrastinate's default (retry=False) fails a job permanently on the
        # first handler exception — a RetrySpec maps to its RetryStrategy.
        strategy: procrastinate.RetryStrategy | bool = (
            procrastinate.RetryStrategy(
                max_attempts=retry.max_attempts, wait=retry.wait_seconds
            )
            if retry is not None
            else False
        )
        # M2-H: run/duration/outcome metrics for every task, centrally.
        self.app.task(instrument_handler(func, name), name=name, retry=strategy, queue=queue)

    def register_periodic(self, func: JobHandler, *, name: str, cron: str) -> None:
        # queueing_lock: if a previous tick is still waiting, skip instead of
        # piling up (procrastinate's periodic deferrer handles the skip).
        task = self.app.task(instrument_handler(func, name), name=name, queueing_lock=name)
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
        job_ids = await self.app.tasks[job_name].batch_defer_async(*[dict(p) for p in payloads])
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
            logger.info("procrastinate schema already applied", extra={"schema": self.schema})
            return

        await self._ensure_open()
        await self.app.schema_manager.apply_schema_async()
        logger.info("procrastinate schema applied", extra={"schema": self.schema})

    async def run_worker(self, *, concurrency: int, bytes_concurrency: int) -> None:
        """Two Procrastinate workers in this process, one per queue (M3-D).

        A single worker with one semaphore cannot bound the byte-holding jobs
        without also capping the cheap ones — a slot claimed by a FETCH waiting
        on an in-process semaphore is still a slot. Two workers each claim only
        their own queue's jobs, so ``bytes`` never holds more than its share.
        Both run a periodic deferrer; the second defer of every tick hits the
        UNIQUE (task_name, periodic_id, defer_timestamp) row and is logged by
        Procrastinate as already deferred — one execution per tick, as before.

        Each worker's own signal handling is disabled
        (``install_signal_handlers=False``): asyncio's ``add_signal_handler``
        REPLACES the previous handler rather than layering, so the second
        worker to install one would silently steal SIGTERM/SIGINT from the
        first — that worker would then never see a stop request and this
        method's ``gather`` would hang until SIGKILL (Fix round 1, Important
        #1). This method owns a single handler for both instead: on
        SIGINT/SIGTERM it cancels both run tasks, which is Procrastinate's
        documented graceful-stop path for a worker started via
        ``run_worker_async`` — ``Worker.run`` catches the cancellation, calls
        its own ``stop()``, waits out ``shutdown_graceful_timeout`` for
        in-flight jobs, then re-raises ``CancelledError``, which this method
        treats as a clean stop rather than propagating it.
        """
        await self._ensure_open()
        default_task = asyncio.create_task(
            self.app.run_worker_async(
                queues=[QUEUE_DEFAULT],
                concurrency=concurrency - bytes_concurrency,
                name=QUEUE_DEFAULT,
                install_signal_handlers=False,
            )
        )
        bytes_task = asyncio.create_task(
            self.app.run_worker_async(
                queues=[QUEUE_BYTES],
                concurrency=bytes_concurrency,
                name=QUEUE_BYTES,
                install_signal_handlers=False,
            )
        )
        loop = asyncio.get_running_loop()
        stop_requested = asyncio.Event()

        def _stop() -> None:
            # asyncio signal callbacks receive no arguments.
            stop_requested.set()
            default_task.cancel()
            bytes_task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)
        try:
            await asyncio.gather(default_task, bytes_task)
        except asyncio.CancelledError:
            # Only swallow a cancellation this method itself requested; a
            # cancellation from elsewhere (e.g. the process's own task being
            # cancelled) must still propagate.
            if not stop_requested.is_set():
                raise
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

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
