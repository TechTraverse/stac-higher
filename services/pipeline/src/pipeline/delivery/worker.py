"""Deliver one item's assets to a destination (ROADMAP §6.4, Slice B-i).

For each requested asset key: read the **canonical** bytes from the platform
bucket (``assets/{collection}/{item_id}/{filename}``), render the destination
path from the association's ``path_template``, and write atomically via the
adapter (``put_atomic``). Records one ``delivery_log`` row per (association,
item), moving pending → delivering → delivered (or failed).

B-i scope: canonical-bytes stream only. Reference-mode source resolution +
S3→S3 server-side copy are B-ii; payload sidecars, on_update/overwrite, and
retry are B-ii/B-iii. A transfer failure marks the row ``failed`` and does NOT
re-raise, so one item's failure never aborts the rest of the batch job; the
B-iii retry sweep re-drives ``failed`` rows.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.delivery.config import DeliveryConfig
from pipeline.delivery.path import render_path
from pipeline.delivery.repo import DeliverTarget, DeliveryRepo
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)


def _asset_filename(asset: dict[str, Any]) -> str:
    """The canonical object filename for an asset — the last path segment of its
    ``href`` (which the ingest/upload paths set to ``/api/assets/.../{filename}``)."""
    href = asset.get("href")
    if not href:
        raise ValueError("asset has no href")
    return unquote(str(href).rstrip("/").rsplit("/", 1)[-1])


async def deliver_item(
    repo: DeliveryRepo,
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    bucket: str,
    *,
    target: DeliverTarget,
    config: DeliveryConfig,
    item: dict[str, Any],
    asset_keys: list[str],
    item_created_at: str | None,
) -> None:
    item_id = str(item["id"])
    row_id = await repo.upsert_pending(target.id, item_id, item_created_at)
    await repo.mark_delivering(row_id)
    try:
        assets = item.get("assets") or {}
        total = 0
        for key in asset_keys:
            asset = assets.get(key)
            if asset is None:
                # Asset vanished between match and delivery — skip, deliver the rest.
                continue
            filename = _asset_filename(asset)
            canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
            data = await asyncio.to_thread(
                platform.get_object, s3_client, bucket, canonical_key
            )
            dest = render_path(config.path_template, item, filename)
            await adapter.put_atomic(dest, data)
            total += len(data)
        await repo.mark_delivered(row_id, total)
        logger.info(
            "delivery complete",
            extra={"association_id": target.id, "item_id": item_id, "bytes": total},
        )
    except Exception as exc:  # record + continue, retry is B-iii (not enabled: BLE001)
        await repo.mark_failed(row_id, str(exc))
        logger.exception(
            "delivery failed",
            extra={"association_id": target.id, "item_id": item_id},
        )
