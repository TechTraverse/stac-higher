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
from pipeline.jobs import drain, health_sweep, heartbeat, ingest, staging_cleanup
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
        await asyncio.gather(server.serve(), queue.run_worker())
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
