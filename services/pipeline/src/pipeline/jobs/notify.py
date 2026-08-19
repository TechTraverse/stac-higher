"""Notification job wiring (M2-C): fan-out sweep + webhook dispatch.

Every minute, ``pipeline.notify_sweep`` fans newly firing alerts out into the
``notification_deliveries`` ledger and enqueues one ``pipeline.webhook_notify``
job per dispatchable row. The ledger owns durability, backoff, and the attempt
cap (webhook jobs record their outcome instead of raising), so no queue-level
RetrySpec is attached — exactly the delivery_log model.
"""

from __future__ import annotations

import logging

from pipeline import metrics
from pipeline.config import Settings
from pipeline.notify.fanout import WEBHOOK_JOB_NAME, handle_webhook_delivery, notify_tick
from pipeline.notify.repo import PgNotifyRepo
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

SWEEP_JOB_NAME = "pipeline.notify_sweep"
SWEEP_CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def notify_sweep(timestamp: int) -> None:
        repo = PgNotifyRepo(settings.database_url)

        async def enqueue(delivery_id: str) -> None:
            await queue.enqueue(WEBHOOK_JOB_NAME, {"delivery_id": delivery_id})

        result = await notify_tick(
            repo,
            enqueue,
            max_attempts=settings.webhook_max_attempts,
            retry_seconds=settings.webhook_retry_seconds,
            stall_seconds=settings.webhook_stall_seconds,
        )
        if result.fanned_out or result.revived or result.enqueued:
            logger.info(
                "notify sweep",
                extra={
                    "fanned_out": result.fanned_out,
                    "deliveries_created": result.deliveries_created,
                    "revived": result.revived,
                    "enqueued": result.enqueued,
                    "scheduled_timestamp": timestamp,
                },
            )

    async def webhook_notify(delivery_id: str) -> None:
        repo = PgNotifyRepo(settings.database_url)
        status = await handle_webhook_delivery(
            repo,
            delivery_id,
            max_attempts=settings.webhook_max_attempts,
            allow_hosts=settings.egress_allow_hosts,
            timeout=settings.webhook_timeout_seconds,
        )
        metrics.WEBHOOK_DELIVERIES.labels(outcome=status).inc()
        if status != "delivered":
            logger.info(
                "webhook delivery not delivered",
                extra={"delivery_id": delivery_id, "status": status},
            )

    queue.register_periodic(notify_sweep, name=SWEEP_JOB_NAME, cron=SWEEP_CRON)
    queue.register_task(webhook_notify, name=WEBHOOK_JOB_NAME)
