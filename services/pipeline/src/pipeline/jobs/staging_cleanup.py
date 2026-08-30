"""Periodic staging TTL cleanup (ROADMAP Phase 3, §5.3; Phase 7 §4.1).

Push-ingest uploads land under ``staging/{upload_id}/`` and are moved to
canonical storage by the finalize step (Phase 7). Anything that never finalizes
(abandoned uploads, post-move leftovers) must not accumulate — this hourly
sweep deletes staging objects older than ``STAGING_TTL_SECONDS``.

**One governing clock (Phase 7 §4.1):** a live upload session's expiry is
governed by its ``staged_uploads`` ledger row (``created_at + TTL``), not by
object mtime — so this sweep SKIPS prefixes whose ledger row is non-terminal
and younger than the TTL. Prefixes with no ledger row (legacy debris, future
Phase 9 run prefixes) keep the object-mtime rule. If the ledger cannot be read
the tick is skipped entirely: deleting bytes without consulting the clock
could sweep a live session mid-upload — fail safe, retry next hour.

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
from pipeline.finalize.repo import FinalizeRepo, PgFinalizeRepo
from pipeline.queue.interface import QueueBackend
from pipeline.storage.keys import staging_prefix as session_prefix
from pipeline.storage.platform import build_platform_client, cleanup_expired

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.staging_cleanup"
CRON = "0 * * * *"  # hourly
STAGING_PREFIX = "staging/"


async def cleanup_tick(
    settings: Settings, now_epoch: int, *, repo: FinalizeRepo | None = None
) -> int:
    """Delete staging uploads older than the TTL, honoring the ledger clock.
    Returns the count deleted (0 when the ledger is unreadable — fail safe)."""
    cutoff = dt.datetime.fromtimestamp(
        now_epoch - settings.staging_ttl_seconds, tz=dt.UTC
    )

    ledger = repo or PgFinalizeRepo(settings.database_url)
    try:
        active = await ledger.list_active_upload_ids(settings.staging_ttl_seconds)
        protected = frozenset(session_prefix(upload_id) for upload_id in active)
    except Exception as exc:
        logger.error(
            "staging cleanup skipped: ledger unreadable (will not delete blind)",
            extra={"job": JOB_NAME, "error": str(exc)},
        )
        return 0

    def _run() -> int:
        client = build_platform_client(settings)
        return cleanup_expired(
            client,
            settings.staging_bucket,
            STAGING_PREFIX,
            cutoff,
            protected_prefixes=protected,
        )

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
