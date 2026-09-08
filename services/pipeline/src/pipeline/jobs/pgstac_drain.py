"""pgstac query-queue drain wiring (M3-A, spec §4.3): a one-minute tick.

Locally the pipeline is the drainer (`PGSTAC_QUEUE_DRAINER=pipeline`); where
pg_cron owns `run_queued_queries` the tick samples only. The cadence IS the
staleness bound on partition statistics (spec §4.6) — one minute is the
scheduler's granularity, and the two `REFRESH MATERIALIZED VIEW`s inside a
drain scale with partition count, not write rate, so the cost to watch is
`pipeline_job_seconds{job="pipeline.pgstac_queue_drain"}` against
`SELECT count(*) FROM pgstac.partitions`.
"""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.queue.interface import QueueBackend
from pipeline.stac.query_queue import PgPgstacQueueRepo, drain_tick

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.pgstac_queue_drain"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def pgstac_queue_drain(timestamp: int) -> None:
        repo = PgPgstacQueueRepo(settings.database_url)
        result = await drain_tick(
            repo,
            mode=settings.pgstac_queue_drainer,
            stale_after_seconds=settings.pgstac_queue_stale_seconds,
            history_days=settings.pgstac_queue_history_days,
        )
        touched = (
            result.before.depth
            or (result.drained and result.drained.executed)
            or result.pruned
        )
        if touched:
            logger.info(
                "pgstac query queue tick",
                extra={
                    "mode": result.mode,
                    "depth_before": result.before.depth,
                    "depth_after": result.after.depth if result.after else None,
                    "oldest_age_seconds": (result.after or result.before).oldest_age_seconds,
                    "executed": result.drained.executed if result.drained else 0,
                    "errors": result.drained.errors if result.drained else 0,
                    "pruned_history_rows": result.pruned,
                    "stale": result.stale,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(pgstac_queue_drain, name=JOB_NAME, cron=CRON)
