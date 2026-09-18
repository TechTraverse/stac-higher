"""Service entrypoint: one process running schema setup, worker, and /health.

Order matters: apply the Procrastinate schema (idempotent) before the worker
starts, then run the worker (which owns the periodic scheduler) and the
health server concurrently. If either exits, the process exits — compose
restarts it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import uvicorn

from pipeline.config import Settings, sizing_warnings
from pipeline.db.pool import close_pools
from pipeline.health import create_health_app
from pipeline.jobs import (
    backfill,
    dispatch,
    drain,
    finalize,
    gc,
    health_sweep,
    heartbeat,
    history,
    ingest,
    monitor,
    notify,
    pgstac_drain,
    process,
    staging_cleanup,
)
from pipeline.log import configure_logging
from pipeline.queue.procrastinate_backend import ProcrastinateQueue
from pipeline.stac.pgstac_writer import close_writer_pools

logger = logging.getLogger(__name__)


def build_queue(settings: Settings) -> ProcrastinateQueue:
    # Fix round 1 (Important #3): size the Procrastinate connector pool to
    # the job slots + 4, matching the DB_POOL_MAX sizing rule — otherwise
    # psycopg_pool's default max_size=4 starves 12 slots' round trips.
    queue = ProcrastinateQueue(
        settings.database_url,
        schema=settings.queue_schema,
        pool_max_size=settings.worker_concurrency + 4,
    )
    heartbeat.register(queue)
    # Phase 2 connection bridge (ADR 0004): drain user-requested tests + sweep.
    drain.register(queue, settings)
    health_sweep.register(queue, settings)
    # Phase 3: sweep abandoned push-ingest uploads out of staging/.
    staging_cleanup.register(queue, settings)
    # Phase 4: poll-based ingest — scheduler + DISCOVER/GROUP/FETCH chain.
    ingest.register(queue, settings)
    # Phase 5 Slice A: delivery dispatch (outbox → match → enqueue); the poll
    # is the fallback wake path — main.py also runs the NOTIFY listener.
    dispatch.register(queue, settings)
    # Phase 5 Slice C: user-initiated backfill bridge (chunked bulk jobs).
    backfill.register(queue, settings)
    # M2-B: §6.6 alerting — expectation/health/job-failure conditions →
    # stac_higher.alerts (raise / re-fire / auto-resolve).
    monitor.register(queue, settings)
    # M2-C: §4 notification fan-out — firing alerts → group webhook channels
    # via the notification_deliveries ledger (in-app needs no dispatch).
    notify.register(queue, settings)
    # M2-F: §6.5 retention & GC — expire items per collection settings, then
    # collect marked asset prefixes after the grace window (ADR 0011).
    gc.register(queue, settings)
    # M2-G: §6 hygiene — hourly retention sweeps for the UNIQUE-keyed history
    # tables (partitioning covers item_events/audit_log — ADR 0012).
    history.register(queue, settings)
    # Phase 7 (P7-E): push-ingest finalize — staged items move staging →
    # canonical through the ADR 0014 seam; plus the §6.4 recovery sweep.
    finalize.register(queue, settings)
    # Phase 9 (M5-C): process triggers + the run ledger — the dispatcher's
    # item_event leg and the cron tick queue runs through the §7 rate
    # ceiling; the run tick executes them behind the ADR 0013 executor.
    process.register(queue, settings)
    # M3-A: drain pgstac.query_queue (partition stats deferred by the writer's
    # `use_queue` session GUC) — or only sample it where pg_cron drains.
    pgstac_drain.register(queue, settings)
    return queue


def blocking_executor(settings: Settings) -> ThreadPoolExecutor:
    """The loop's default executor, sized to the job slots plus the overlapping
    periodic ticks, and never below the stdlib default. Every blocking call in
    the worker is `asyncio.to_thread` (boto3, rasterio, pgstac), so the stdlib
    default of min(32, cpus + 4) threads would be a hidden concurrency ceiling
    on a small container — and on a large host it is the HIGHER number, so it
    stays the floor. Some jobs fan out more than one thread (process input
    staging runs 4 fetches per run, the health sweep one probe per
    connection); those queue on the executor rather than deadlock, since no
    pooled thread ever waits on another `to_thread` result."""
    stdlib_default = min(32, (os.cpu_count() or 1) + 4)
    return ThreadPoolExecutor(
        max_workers=max(settings.worker_concurrency + 4, stdlib_default),
        thread_name_prefix="pipeline-blocking",
    )


async def run(settings: Settings) -> None:
    queue = build_queue(settings)

    try:
        asyncio.get_running_loop().set_default_executor(blocking_executor(settings))
        for warning in sizing_warnings(settings):
            logger.warning(warning.message, extra=warning.extra)
        logger.info("applying queue schema", extra={"schema": settings.queue_schema})
        await queue.setup()

        server = uvicorn.Server(
            uvicorn.Config(
                create_health_app(queue),
                host="0.0.0.0",  # container-internal bind
                port=settings.health_port,
                log_config=None,  # propagate uvicorn logs to our JSON handler
            )
        )
        logger.info(
            "pipeline service starting",
            extra={
                "health_port": settings.health_port,
                "queue_backend": queue.name,
                "worker_concurrency": settings.worker_concurrency,
                "worker_bytes_concurrency": settings.worker_bytes_concurrency,
            },
        )
        # Slice C: the NOTIFY-woken dispatch loop runs alongside the worker as
        # the primary wake path; the worker's minute dispatch_poll is fallback.
        await asyncio.gather(
            server.serve(),
            queue.run_worker(
                concurrency=settings.worker_concurrency,
                bytes_concurrency=settings.worker_bytes_concurrency,
            ),
            dispatch.build_notify_listener(queue, settings),
        )
    finally:
        # Both pools before the queue: `queue.aclose()` releases
        # Procrastinate's own pool, and nothing after that point may still
        # want a connection. The async pool serves the repos (M3-B); the sync
        # one serves the pgstac writer (M3-A) — separate objects, separate
        # runtimes, both ours to release. Each close is isolated: a raise
        # here (or a second Ctrl-C's CancelledError) must not skip the
        # remaining closes, and must not shadow the original exception from
        # the `try` above — only `Exception` is caught, so a `CancelledError`
        # still propagates and cancellation isn't swallowed.
        try:
            await close_pools()
        except Exception:
            logger.warning(
                "pool cleanup step failed", extra={"step": "close_pools"}, exc_info=True
            )
        try:
            close_writer_pools()
        except Exception:
            logger.warning(
                "pool cleanup step failed", extra={"step": "close_writer_pools"}, exc_info=True
            )
        try:
            await queue.aclose()
        except Exception:
            logger.warning(
                "pool cleanup step failed", extra={"step": "queue.aclose"}, exc_info=True
            )


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("pipeline service stopped")


if __name__ == "__main__":
    main()
