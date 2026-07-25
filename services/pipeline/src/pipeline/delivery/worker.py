"""Deliver one item's assets to a destination (ROADMAP §6.4, Slice B-ii).

For each requested asset key: resolve the source bytes (canonical platform
bucket, the ingest source adapter for reference-mode assets, or an S3→S3
server-side copy when the destination shares the platform endpoint), render
the destination path from ``path_template``, apply the association's
``on_update``/``overwrite`` policy against ``delivery_log.delivered_assets``
(log-based — never a destination round-trip), and write atomically via the
adapter. Payload sidecars land beside the assets: a checksum per written file,
the item JSON on every processed event, and the completion marker LAST (§6.4).

Records one ``delivery_log`` row per (association, item). A transfer failure
marks the row ``failed`` and does NOT re-raise, so one item's failure never
aborts the rest of the batch job; the B-iii retry sweep re-drives ``failed``
rows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.config import DeliveryConfig
from pipeline.delivery.path import render_path
from pipeline.delivery.payload import (
    checksum_payload,
    completion_payload,
    item_json_payload,
)
from pipeline.delivery.repo import DeliverTarget, DeliveryRepo, ReferenceSource
from pipeline.delivery.transfer import (
    etag_fingerprint,
    is_multipart_etag,
    sha256_fingerprint,
)
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)

#: Retry backoff (B-iii): exponential doubles from the base per attempt, capped;
#: fixed retries at the base interval. Tuned for the 60s dispatch granularity.
RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 3600


def retry_delay_seconds(backoff: str, attempts: int) -> int:
    """Seconds until the retry sweep should re-drive a failed delivery."""
    if backoff == "fixed":
        return RETRY_BASE_SECONDS
    return min(RETRY_BASE_SECONDS * 2 ** max(attempts - 1, 0), RETRY_CAP_SECONDS)


#: Builds a live adapter for a reference-mode item's ingest source connection
#: (decrypt → adapter; supplied by the deliver job, faked in unit tests).
SourceAdapterFactory = Callable[[ConnectionRow], StorageAdapter]


def _asset_filename(asset: dict[str, Any]) -> str:
    """The canonical object filename for an asset — the last path segment of its
    ``href`` (which the ingest/upload paths set to ``/api/assets/.../{filename}``)."""
    href = asset.get("href")
    if not href:
        raise ValueError("asset has no href")
    return unquote(str(href).rstrip("/").rsplit("/", 1)[-1])


def _should_write(overwrite: str, prev: dict[str, Any] | None, fingerprint: str) -> bool:
    """Log-based overwrite gate (spec decision 2): decide from our own
    delivered_assets, never a destination round-trip. A first delivery
    (no prior entry) always writes."""
    if prev is None or overwrite == "always":
        return True
    if overwrite == "never":
        return False
    return prev.get("fingerprint") != fingerprint  # if_newer


async def _read_reference(
    ref: ReferenceSource,
    cache: dict[str, StorageAdapter],
    build_source_adapter: SourceAdapterFactory,
) -> bytes:
    """Reference-mode asset (spec decision 3): bytes live at the ingest source.
    Build (and cache per connection) the source adapter and read in place —
    the ``SourceAdapterByteSource`` pattern from EXTRACT."""
    src = cache.get(ref.connection.id)
    if src is None:
        src = build_source_adapter(ref.connection)
        cache[ref.connection.id] = src
    return await src.get(ref.fetch_path)


async def _stream_canonical(
    s3_client: platform.S3Like, bucket: str, canonical_key: str
) -> tuple[bytes, str]:
    """Read the canonical object and sha256-fingerprint it in one worker thread
    (hashing a large buffer on the event loop would stall other coroutines)."""

    def _read_and_hash() -> tuple[bytes, str]:
        data = platform.get_object(s3_client, bucket, canonical_key)
        return data, sha256_fingerprint(data)

    return await asyncio.to_thread(_read_and_hash)


def _hexdigest(algo: str, data: bytes) -> str:
    return hashlib.new(algo, data).hexdigest()


async def _write_sidecar(
    adapter: StorageAdapter,
    config: DeliveryConfig,
    item: dict[str, Any],
    payload: tuple[str, bytes],
) -> int:
    """Render the sidecar's own filename through the path template and write it
    atomically. Returns the byte count (counted into delivery_log.bytes)."""
    filename, body = payload
    await adapter.put_atomic(render_path(config.path_template, item, filename), body)
    return len(body)


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
    build_source_adapter: SourceAdapterFactory | None = None,
    server_side_copy: bool = False,
) -> None:
    item_id = str(item["id"])
    prior = await repo.get_row(target.id, item_id)
    if prior is not None and prior.status == "delivered" and config.on_update == "ignore":
        # Fire-once-per-item (§6.4): the item already delivered and this
        # association ignores updates — consume the event, touch nothing.
        logger.info(
            "delivery skipped (on_update: ignore)",
            extra={"association_id": target.id, "item_id": item_id},
        )
        return
    row_id = await repo.upsert_pending(target.id, item_id, item_created_at)
    attempts = await repo.mark_delivering(row_id)
    delivered: dict[str, dict[str, Any]] = dict(prior.delivered_assets) if prior else {}
    try:
        ref_sources: dict[str, ReferenceSource] = {}
        if build_source_adapter is not None:
            for ref in await repo.load_reference_sources(item_id):
                if ref.filename in ref_sources:
                    # Basename collision (ISSUES I-49): reference routing is
                    # keyed by bare filename — two ledger rows sharing one
                    # mis-route silently, so at minimum say so.
                    logger.warning(
                        "reference basename collision — keeping the first source",
                        extra={
                            "item_id": item_id,
                            "ref_filename": ref.filename,
                            "shadowed_fetch_path": ref.fetch_path,
                        },
                    )
                    continue
                ref_sources[ref.filename] = ref
        source_adapters: dict[str, StorageAdapter] = {}
        assets = item.get("assets") or {}
        checksums_algo = config.payload.get("checksums")
        total = 0
        wrote_any = False
        for key in asset_keys:
            asset = assets.get(key)
            if asset is None:
                # Asset vanished between match and delivery — skip, deliver the rest.
                continue
            if config.overwrite == "never" and key in delivered:
                # 'never' skips regardless of the fingerprint — decide before
                # reading any bytes (the read would only be thrown away).
                continue
            filename = _asset_filename(asset)
            ref = ref_sources.get(filename)
            canonical_key: str | None = None
            data: bytes | None = None
            etag = ""
            if ref is not None:
                data = await _read_reference(ref, source_adapters, build_source_adapter)
                fingerprint = await asyncio.to_thread(sha256_fingerprint, data)
                size = len(data)
            else:
                canonical_key = canonical_asset_key(target.collection_id, item_id, filename)
                # sha256 sidecars need the bytes; md5 can ride a single-part etag.
                use_copy = server_side_copy and checksums_algo != "sha256"
                if use_copy:
                    etag, size = await asyncio.to_thread(
                        platform.head_object, s3_client, bucket, canonical_key
                    )
                    if checksums_algo == "md5" and is_multipart_etag(etag):
                        use_copy = False  # multipart etag is not an md5 — stream
                    else:
                        fingerprint = etag_fingerprint(etag, size)
                if not use_copy:
                    data, fingerprint = await _stream_canonical(
                        s3_client, bucket, canonical_key
                    )
                    size = len(data)
            if not _should_write(config.overwrite, delivered.get(key), fingerprint):
                continue  # keep the prior entry — it reflects the destination
            dest = render_path(config.path_template, item, filename)
            if data is None:
                try:
                    await adapter.copy_object_from(bucket, canonical_key, dest)
                except Exception:  # copy denied/failed — stream instead (not enabled: BLE001)
                    logger.warning(
                        "server-side copy failed; streaming instead",
                        extra={"association_id": target.id, "item_id": item_id, "dest": dest},
                        exc_info=True,
                    )
                    data, fingerprint = await _stream_canonical(
                        s3_client, bucket, canonical_key
                    )
                    size = len(data)
                    await adapter.put_atomic(dest, data)
            else:
                await adapter.put_atomic(dest, data)
            total += size
            wrote_any = True
            if checksums_algo:
                if data is None:
                    digest = etag  # copy path: a single-part etag IS the md5
                elif checksums_algo == "sha256":
                    digest = fingerprint.removeprefix("sha256:")  # already computed
                else:
                    digest = await asyncio.to_thread(_hexdigest, checksums_algo, data)
                total += await _write_sidecar(
                    adapter, config, item, checksum_payload(filename, checksums_algo, digest)
                )
            delivered[key] = {"fingerprint": fingerprint, "size": size, "filename": filename}
        if config.payload.get("item_json"):
            # Rewritten on every processed event — item metadata can change
            # with no asset change.
            total += await _write_sidecar(adapter, config, item, item_json_payload(item))
            wrote_any = True
        if config.payload.get("completion_marker") and wrote_any:
            # LAST (§6.4): a consumer that sees the marker sees every listed
            # file. Pruned to the item's CURRENT assets (ISSUES I-49): a key
            # delivered in an earlier cycle but since removed from the item
            # must not be listed as part of the product.
            current = {k: v for k, v in delivered.items() if k in assets}
            total += await _write_sidecar(
                adapter, config, item, completion_payload(item_id, current)
            )
        await repo.mark_delivered(row_id, total, delivered)
        logger.info(
            "delivery complete",
            extra={"association_id": target.id, "item_id": item_id, "bytes": total},
        )
    except Exception as exc:  # record + schedule retry / dead-letter (not enabled: BLE001)
        dead = attempts >= config.max_attempts
        next_attempt_at = (
            None
            if dead
            else dt.datetime.now(dt.UTC)
            + dt.timedelta(seconds=retry_delay_seconds(config.backoff, attempts))
        )
        # The partial delivered map is kept (I-49) so the retry skips assets
        # this cycle already wrote (the overwrite gate sees their fingerprints).
        await repo.mark_failed(
            row_id,
            str(exc),
            delivered_assets=delivered,
            next_attempt_at=next_attempt_at,
            dead=dead,
        )
        logger.exception(
            "delivery dead-lettered" if dead else "delivery failed; retry scheduled",
            extra={
                "association_id": target.id,
                "item_id": item_id,
                "attempts": attempts,
                "max_attempts": config.max_attempts,
                "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
            },
        )
