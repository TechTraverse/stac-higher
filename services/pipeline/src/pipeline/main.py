"""Service entrypoint: one process running schema setup, worker, and /health.

Order matters: apply the Procrastinate schema (idempotent) before the worker
starts, then run the worker (which owns the periodic scheduler) and the
health server concurrently. If either exits, the process exits — compose
restarts it.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from pipeline.config import Settings
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
    staging_cleanup,
)
from pipeline.log import configure_logging
from pipeline.queue.procrastinate_backend import ProcrastinateQueue

logger = logging.getLogger(__name__)


def build_queue(settings: Settings) -> ProcrastinateQueue:
    queue = ProcrastinateQueue(settings.database_url, schema=settings.queue_schema)
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
    return queue


async def run(settings: Settings) -> None:
    queue = build_queue(settings)

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
        extra={"health_port": settings.health_port, "queue_backend": queue.name},
    )
    try:
        # Slice C: the NOTIFY-woken dispatch loop runs alongside the worker as
        # the primary wake path; the worker's minute dispatch_poll is fallback.
        await asyncio.gather(
            server.serve(),
            queue.run_worker(),
            dispatch.build_notify_listener(queue, settings),
        )
    finally:
        await queue.aclose()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("pipeline service stopped")


if __name__ == "__main__":
    main()
