"""Retention & GC job wiring (M2-F, ADR 0011): two five-minute sweeps.

``pipeline.retention_gc`` expires items past their collection's declared
retention (or everything, for archived collections) — marking canonical asset
prefixes in ``asset_gc`` before each catalog delete. ``pipeline.asset_collect``
deletes marked prefixes whose grace has passed from the platform bucket.
Opt-in by construction: collections without ``retention_days``/``archived``
are never touched.
"""

from __future__ import annotations

import logging
from functools import partial

from pipeline.config import Settings
from pipeline.gc.repo import PgGcRepo
from pipeline.gc.sweep import collect_tick, retention_tick
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client, delete_prefix

logger = logging.getLogger(__name__)

RETENTION_JOB_NAME = "pipeline.retention_gc"
COLLECT_JOB_NAME = "pipeline.asset_collect"
CRON = "*/5 * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def retention_gc(timestamp: int) -> None:
        repo = PgGcRepo(settings.database_url)
        result = await retention_tick(repo, batch_limit=settings.gc_batch_items)
        if result.expired_items or result.marks_created:
            logger.info(
                "retention sweep expired items",
                extra={
                    "collections": result.collections,
                    "expired_items": result.expired_items,
                    "marks_created": result.marks_created,
                    "scheduled_timestamp": timestamp,
                },
            )

    async def asset_collect(timestamp: int) -> None:
        repo = PgGcRepo(settings.database_url)
        due = await repo.list_due_marks(1)
        if not due:  # don't build an S3 client on an idle tick
            return
        client = build_platform_client(settings)
        result = await collect_tick(
            repo,
            partial(delete_prefix, client, settings.staging_bucket),
            batch_limit=settings.gc_batch_items,
        )
        logger.info(
            "asset collect swept due marks",
            extra={
                "collected_marks": result.collected_marks,
                "deleted_objects": result.deleted_objects,
                "errors": result.errors,
                "scheduled_timestamp": timestamp,
            },
        )

    queue.register_periodic(retention_gc, name=RETENTION_JOB_NAME, cron=CRON)
    queue.register_periodic(asset_collect, name=COLLECT_JOB_NAME, cron=CRON)
