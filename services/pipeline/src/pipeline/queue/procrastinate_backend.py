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

from pipeline.config import DEFAULT_WORKER_CONCURRENCY
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

#: How long a stop waits for in-flight jobs before aborting them — Procrastinate
#: re-queues a job aborted by a shutdown per its retry strategy, so the abort
#: is a retry, not a loss. Must stay BELOW the container's `stop_grace_period`
#: (docker-compose.yml: 30 s), or Docker's SIGKILL wins and the abort, the
#: worker unregistration and `main.run()`'s pool cleanup never run.
SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS = 25.0


class ProcrastinateQueue(QueueBackend):
    name = "procrastinate"

    def __init__(
        self,
        database_url: str,
        *,
        schema: str = "procrastinate",
        # Fix round 1 (Important #3): unsized, psycopg_pool defaults to
        # max_size=4 — 12 job slots' fetch/finish/heartbeat round trips would
        # share 4 connections and PoolTimeout under load. The default tracks
        # DEFAULT_WORKER_CONCURRENCY + 4 (the DB_POOL_MAX rule applied to this
        # second pool); build_queue overrides max_size from the real settings.
        pool_min_size: int = 2,
        pool_max_size: int = DEFAULT_WORKER_CONCURRENCY + 4,
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
        method's wait would hang until SIGKILL (Fix round 1, Important #1).
        This method owns a single handler for both instead: on SIGINT/SIGTERM
        it cancels both run tasks, which is Procrastinate's documented
        graceful-stop path for a worker started via ``run_worker_async`` —
        ``Worker.run`` catches the cancellation, calls its own ``stop()``,
        waits out ``shutdown_graceful_timeout`` for in-flight jobs, then
        re-raises ``CancelledError``.

        Both drains are always waited out, whichever finishes first (Fix
        round 2, Important #1): a plain ``asyncio.gather`` with the default
        ``return_exceptions=False`` returns as soon as the FIRST task ends,
        leaving the sibling's ``shutdown_graceful_timeout`` drain unobserved
        — this method instead waits for one to finish, and if that happened
        without a stop request (i.e. a worker crashed), cancels the other
        too before waiting for it, so a failure always tears down both
        workers instead of abandoning one mid-run.

        The previous SIGINT/SIGTERM handlers (e.g. uvicorn's own) are saved
        before installing ours and restored in ``finally`` (Fix round 2,
        Minor #2) — ``loop.remove_signal_handler`` alone resets the signal to
        ``SIG_DFL``, which would make a second SIGTERM hard-kill the process
        and skip ``main.run``'s cleanup.

        An outer cancellation — ``main.run``'s gather propagating a sibling's
        failure, or ``asyncio.run``'s teardown — lands in the ``asyncio.wait``
        below, which does NOT cancel its futures. The ``finally`` therefore
        cancels and waits out whichever worker is still running before the
        handlers go back (fix round 3), so the pools are never closed under
        an in-flight job. The stop callback is idempotent for the same
        reason: a second SIGTERM mid-drain must not re-cancel a worker whose
        ``Worker.run`` is already awaiting its drain — that cancel aborts the
        drain and orphans the loop task.
        """
        if concurrency - bytes_concurrency < 1:
            raise ValueError(
                "WORKER_BYTES_CONCURRENCY must leave at least one slot for the"
                f" default queue (concurrency={concurrency},"
                f" bytes_concurrency={bytes_concurrency})"
            )
        await self._ensure_open()
<<<<<<< HEAD
        loop = asyncio.get_running_loop()
        stop_requested = asyncio.Event()
        previous_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        default_task: asyncio.Task[None] | None = None
        bytes_task: asyncio.Task[None] | None = None

        def _stop() -> None:
            # asyncio signal callbacks receive no arguments.
            if stop_requested.is_set():
                return  # a repeat signal must not abort the drain in progress
            stop_requested.set()
            if default_task is not None:
                default_task.cancel()
            if bytes_task is not None:
                bytes_task.cancel()

        try:
            # Install before creating the tasks: a failure here must not
            # leave either task running unobserved (Fix round 2, Minor #2).
            for sig in previous_handlers:
                loop.add_signal_handler(sig, _stop)

            default_task = asyncio.create_task(
                self.app.run_worker_async(
                    queues=[QUEUE_DEFAULT],
                    concurrency=concurrency - bytes_concurrency,
                    name=QUEUE_DEFAULT,
                    install_signal_handlers=False,
                    shutdown_graceful_timeout=SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS,
                )
            )
            bytes_task = asyncio.create_task(
                self.app.run_worker_async(
                    queues=[QUEUE_BYTES],
                    concurrency=bytes_concurrency,
                    name=QUEUE_BYTES,
                    install_signal_handlers=False,
                    shutdown_graceful_timeout=SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS,
                )
            )

            _done, pending = await asyncio.wait(
                {default_task, bytes_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if pending and not stop_requested.is_set():
                # One worker ended on its own — both run with Procrastinate's
                # default wait=True, so only a crash ends a worker without a
                # stop request. Stop the other too, so its own
                # shutdown_graceful_timeout drain still happens instead of
                # leaving it running unattended.
                for task in pending:
                    task.cancel()
            if pending:
                await asyncio.wait(pending)

            failures = [
                exc
                for task in (default_task, bytes_task)
                if not task.cancelled() and (exc := task.exception()) is not None
            ]
            if failures:
                # Both may have crashed; the first is raised, the rest are
                # logged here so they are never "never retrieved".
                for extra in failures[1:]:
                    logger.error("second worker also failed", exc_info=extra)
                raise failures[0]
        finally:
            live = {
                task for task in (default_task, bytes_task) if task is not None and not task.done()
            }
            for task in live:
                # A task `_stop` or the crash path already cancelled is
                # mid-drain: a second cancel would land in `Worker.run`'s
                # `await loop_task` and abort that drain (fix round 4).
                if task.cancelling() == 0:
                    task.cancel()
            if live:
                await asyncio.wait(live)
                for task in live:
                    if not task.cancelled() and task.exception() is not None:
                        logger.error("worker failed during shutdown", exc_info=task.exception())
            for sig, previous in previous_handlers.items():
                loop.remove_signal_handler(sig)
                if previous is not None:  # None: a handler not installed from Python
                    signal.signal(sig, previous)
=======
        await self.app.run_worker_async(
            shutdown_graceful_timeout=SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS
        )
>>>>>>> origin/main

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
