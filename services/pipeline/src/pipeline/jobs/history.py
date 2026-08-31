"""History retention job wiring (M2-G, ADR 0012; P7-H adds staged_uploads):
the hourly hygiene sweep for the unpartitioned history tables (see
pipeline/history/sweep.py for the exact — deliberately conservative —
rules)."""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.history.sweep import PgHistoryRepo, history_tick
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.history_retention"
CRON = "17 * * * *"  # hourly, offset to avoid the top-of-hour pileup


def register(queue: QueueBackend, settings: Settings) -> None:
    async def history_retention(timestamp: int) -> None:
        repo = PgHistoryRepo(settings.database_url)
        result = await history_tick(
            repo,
            checks_days=settings.connection_checks_retention_days,
            history_days=settings.history_retention_days,
        )
        if (
            result.checks_deleted
            or result.checks_failed_stranded
            or result.ingest_files_deleted
            or result.delivery_log_deleted
            or result.staged_uploads_deleted
        ):
            logger.info(
                "history retention sweep",
                extra={
                    "checks_deleted": result.checks_deleted,
                    "checks_failed_stranded": result.checks_failed_stranded,
                    "ingest_files_deleted": result.ingest_files_deleted,
                    "delivery_log_deleted": result.delivery_log_deleted,
                    "staged_uploads_deleted": result.staged_uploads_deleted,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(history_retention, name=JOB_NAME, cron=CRON)
