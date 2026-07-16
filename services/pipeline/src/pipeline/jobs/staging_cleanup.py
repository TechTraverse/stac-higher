"""Periodic staging TTL cleanup (ROADMAP Phase 3, §5.3).

Push-ingest uploads land under ``staging/{upload_id}/`` and are moved to
canonical storage by the finalize step (Phase 7). Anything that never finalizes
(abandoned uploads) must not accumulate — this hourly sweep deletes staging
objects older than ``STAGING_TTL_SECONDS``.

Deterministic reference time: the tick uses the scheduled ``timestamp`` as "now"
so the cutoff is reproducible in tests. Storage/egress errors degrade to a
logged no-op tick — a background sweep must never crash the worker.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from botocore.exceptions import BotoCoreError, ClientError

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client, cleanup_expired

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.staging_cleanup"
CRON = "0 * * * *"  # hourly
STAGING_PREFIX = "staging/"


async def cleanup_tick(settings: Settings, now_epoch: int) -> int:
    """Delete staging uploads older than the TTL. Returns the count deleted."""
    cutoff = dt.datetime.fromtimestamp(
        now_epoch - settings.staging_ttl_seconds, tz=dt.UTC
    )

    def _run() -> int:
        client = build_platform_client(settings)
        return cleanup_expired(client, settings.staging_bucket, STAGING_PREFIX, cutoff)

    return await asyncio.to_thread(_run)


def register(queue: QueueBackend, settings: Settings) -> None:
    async def cleanup(timestamp: int) -> None:
        try:
            deleted = await cleanup_tick(settings, timestamp)
        except (EgressBlocked, ClientError, BotoCoreError) as exc:
            logger.error(
                "staging cleanup skipped",
                extra={"job": JOB_NAME, "error": str(exc)},
            )
            return
        if deleted:
            logger.info(
                "staging cleanup removed expired uploads",
                extra={"deleted": deleted, "scheduled_timestamp": timestamp},
            )

    queue.register_periodic(cleanup, name=JOB_NAME, cron=CRON)
