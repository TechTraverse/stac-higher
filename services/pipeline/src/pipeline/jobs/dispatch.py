"""Delivery dispatch wiring (Slice B-i: poll-driven, real byte transfer).

``dispatch_poll`` drains the item_events outbox each minute via ``dispatch_once``,
which groups matches per association and enqueues a batched ``pipeline.deliver``
job. The ``deliver`` handler loads the destination connection, builds its adapter,
and runs each item through ``deliver_item`` (canonical bytes → destination,
recorded in ``delivery_log``). Slice C swaps the poll for a LISTEN-woken loop;
retry → dead-letter and payload/on_update policy are B-ii/B-iii.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import PgDeliveryRepo
from pipeline.delivery.worker import deliver_item
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import PgDispatchRepo
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_DISPATCH_POLL = "pipeline.dispatch_poll"
JOB_DELIVER = "pipeline.deliver"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def dispatch_poll(timestamp: int) -> None:
        repo = PgDispatchRepo(settings.database_url)

        async def _enqueue(batches: list[dict[str, Any]]) -> None:
            await queue.enqueue_batch(JOB_DELIVER, batches)

        matches = await dispatch_once(repo, _enqueue)
        if matches:
            logger.info(
                "dispatch poll enqueued delivery batches",
                extra={"matches": len(matches), "scheduled_timestamp": timestamp},
            )

    async def deliver(association_id: str, items: list[dict[str, Any]]) -> None:
        master_key = load_key_or_skip(settings, JOB_DELIVER)
        if master_key is None:
            return
        repo = PgDeliveryRepo(settings.database_url)
        target = await repo.load_target(association_id)
        if target is None:
            # Association disabled/deleted between dispatch and delivery — no-op.
            return
        try:
            adapter = build_adapter(
                target.connection, master_key, settings.egress_allow_hosts
            )
        except AdapterBuildError:
            logger.exception(
                "deliver: adapter build failed",
                extra={"association_id": association_id},
            )
            return
        config = parse_delivery_config(target.config)
        s3_client = build_platform_client(settings)
        for entry in items:
            item = await repo.get_item(target.collection_id, entry["item_id"])
            if item is None:
                continue
            await deliver_item(
                repo,
                adapter,
                s3_client,
                settings.staging_bucket,
                target=target,
                config=config,
                item=item,
                asset_keys=entry["asset_keys"],
                item_created_at=entry.get("item_created_at"),
            )

    queue.register_periodic(dispatch_poll, name=JOB_DISPATCH_POLL, cron=CRON)
    queue.register_task(deliver, name=JOB_DELIVER)
