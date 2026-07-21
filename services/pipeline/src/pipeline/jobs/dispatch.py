"""Delivery dispatch wiring (Slice A: poll-driven skeleton).

A periodic ``dispatch_poll`` task drains the item_events outbox each minute via
``dispatch_once`` (matches → log, no transfer yet). Slice C replaces this poll
with a LISTEN-woken co-process for single-digit-second latency; the periodic tick
stays as the safety-net fallback.
"""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import PgDispatchRepo
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_DISPATCH_POLL = "pipeline.dispatch_poll"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def dispatch_poll(timestamp: int) -> None:
        repo = PgDispatchRepo(settings.database_url)
        matches = await dispatch_once(repo)
        if matches:
            logger.info(
                "dispatch poll produced matches",
                extra={"matches": len(matches), "scheduled_timestamp": timestamp},
            )

    queue.register_periodic(dispatch_poll, name=JOB_DISPATCH_POLL, cron=CRON)
