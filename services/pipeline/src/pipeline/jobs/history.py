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
    # M5-E: the daily flow-stats history the lineage strip reads (P9-E).
    register_flow_stats_daily(queue, settings)


# ---------------------------------------------------------------------------
# M5-E: the daily flow-stats rollup (P9-E, spec §10)
# ---------------------------------------------------------------------------

JOB_FLOW_STATS_DAILY = "pipeline.flow_stats_daily"
#: Just after midnight UTC, so the bucket it writes is a COMPLETE day. Running
#: mid-day would snapshot a partial day as final; the UI derives today's
#: partial bucket live from the cumulative counters instead.
FLOW_STATS_DAILY_CRON = "5 0 * * *"


def register_flow_stats_daily(queue: QueueBackend, settings: Settings) -> None:
    async def flow_stats_daily(timestamp: int) -> None:
        import datetime as dt

        from pipeline.flow.daily_repo import PgDailyStatsRepo, rollup_tick

        # Yesterday: the day that just ended is the one with complete data.
        day = dt.datetime.fromtimestamp(timestamp, dt.UTC).date() - dt.timedelta(days=1)
        written, pruned = await rollup_tick(
            PgDailyStatsRepo(settings.database_url),
            day=day,
            retention_days=settings.flow_stats_retention_days,
        )
        logger.info(
            "flow stats daily rollup",
            extra={"day": day.isoformat(), "written": written, "pruned": pruned},
        )

    queue.register_periodic(
        flow_stats_daily, name=JOB_FLOW_STATS_DAILY, cron=FLOW_STATS_DAILY_CRON
    )
