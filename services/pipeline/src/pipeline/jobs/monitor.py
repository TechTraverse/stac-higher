"""Flow monitor job wiring (M2-B): the periodic §6.6 alerting tick.

Every minute, gather the currently-true conditions (declared-expectation
violations, error connections, dead/terminal job failures, and — P7-H —
push items rejected at finalize inside the lookback) and reconcile the
``stac_higher.alerts`` table — raise, re-fire (``last_seen``), auto-resolve.
Notification fan-out to the owning group's channels is M2-C.
"""

from __future__ import annotations

import logging

from pipeline import metrics
from pipeline.config import Settings
from pipeline.flow.monitor import monitor_tick
from pipeline.flow.repo import PgFlowMonitorRepo
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.flow_monitor"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def flow_monitor(timestamp: int) -> None:
        repo = PgFlowMonitorRepo(settings.database_url)
        raised, resolved = await monitor_tick(
            repo,
            ingest_max_retries=settings.ingest_max_retries,
            push_lookback_seconds=settings.push_alert_lookback_seconds,
        )
        if raised:
            metrics.ALERTS.labels(event="raised").inc(raised)
        if resolved:
            metrics.ALERTS.labels(event="auto_resolved").inc(resolved)
        if raised or resolved:
            logger.info(
                "flow monitor reconciled alerts",
                extra={
                    "raised": raised,
                    "auto_resolved": resolved,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(flow_monitor, name=JOB_NAME, cron=CRON)
