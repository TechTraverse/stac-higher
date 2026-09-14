"""FETCH stage: copy (or reference) a product group's bytes into canonical
storage (§6.1).

**Copy mode**: for each member of a ready group, move its bytes into the
platform bucket under the canonical key
``assets/{collection}/{item_id}/{filename}`` (§5.3) — server-side copy or a
hashed streamed upload, per ``ingest/transfer.py`` below. The ledger row moves
``settled → fetching → stored``.

**Reference mode** (Slice C, s3-only): no byte copy. ``_reference_stage``
records the source object's stable, credential-free URL
(``adapter.public_object_url``) as ``source_href`` and advances the ledger row
directly ``settled → stored``.

Either way EXTRACT + ITEMIZE (Slice B4) build the STAC item from the
``stored`` rows; asset hrefs in the eventual item point at ``/api/assets/...``
in both storage modes (the app's asset route resolves the canonical object,
or redirects to ``source_href``, offline).

Idempotent: a member is fetched only while its latest ledger row is still
``settled``, so a re-enqueued group (GROUP re-emits until FETCH runs) can't
double-store. Copy mode moves bytes by server-side copy when the platform can
read the source bucket, else by a bounded streamed multipart upload (M3-C,
``ingest/transfer.py``); nothing is buffered whole except for SFTP/FTP sources
(I-83). A per-member failure marks only that member ``failed`` — the rest of
the group still stores, and ITEMIZE handles partial products.
"""

from __future__ import annotations

import asyncio
import logging

from pipeline import metrics
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.config import IngestConfig
from pipeline.ingest.discover import source_fetch_path
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_FETCHING,
    STATUS_SETTLED,
    STATUS_STORED,
    IngestAssociation,
    IngestRepo,
)
from pipeline.ingest.transfer import TransferPolicy
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)


async def fetch_stage(
    repo: IngestRepo,
    association: IngestAssociation,
    config: IngestConfig,
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    bucket: str,
    item_id: str,
    source_paths: list[str],
    *,
    transfer: TransferPolicy | None = None,
) -> int:
    """Copy a group's settled files into canonical storage. Returns count stored.

    Takes the primitives that cross the queue (``item_id`` + source paths) and
    reloads each file's current ledger row itself — the stage is the single
    source of truth for the latest row, so callers need not preload it.
    """
    if config.storage_mode == "reference":
        return await _reference_stage(repo, association, config, adapter, item_id, source_paths)

    policy = transfer or TransferPolicy()
    stored = 0
    for source_path in source_paths:
        # Re-read: only fetch a row that is still settled (idempotent guard).
        latest = await repo.get_latest_ledger(association.id, source_path)
        if latest is None or latest.status != STATUS_SETTLED:
            continue
        await repo.set_ledger_fields(latest.id, status=STATUS_FETCHING, item_id=item_id)
        try:
            fetch_path = source_fetch_path(config.source_path, source_path)
            filename = source_path.rsplit("/", 1)[-1]
            key = canonical_asset_key(association.collection_id, item_id, filename)
            checksum, mode = await _transfer(adapter, s3_client, bucket, key, fetch_path, policy)
            metrics.INGEST_FETCH_TRANSFERS.labels(mode=mode).inc()
            await repo.set_ledger_fields(latest.id, status=STATUS_STORED, checksum=checksum)
            stored += 1
        except Exception:
            await repo.set_ledger_fields(latest.id, status=STATUS_FAILED)
            logger.exception(
                "ingest fetch failed for source file",
                extra={
                    "association_id": association.id,
                    "item_id": item_id,
                    "source_path": source_path,
                },
            )
    logger.info(
        "ingest fetch group done",
        extra={
            "association_id": association.id,
            "item_id": item_id,
            "stored": stored,
            "files": len(source_paths),
        },
    )
    return stored


async def _transfer(
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    bucket: str,
    key: str,
    fetch_path: str,
    policy: TransferPolicy,
) -> tuple[str | None, str]:
    """Move one member into canonical storage. Returns ``(sha256 or None, mode)``:
    the copy path reads no bytes, so it records no checksum (an honest absence,
    nothing reads the column today)."""
    source = adapter.copy_source(fetch_path) if policy.server_side_copy else None
    mode = "stream"
    if source is not None:
        try:
            await asyncio.to_thread(
                platform.copy_from_bucket, s3_client, source[0], source[1], bucket, key,
                chunk_bytes=policy.chunk_bytes, concurrency=policy.concurrency,
            )
            return None, "copy"
        except Exception:  # the platform keys cannot read the source bucket — stream instead
            logger.warning(
                "server-side copy failed; streaming instead",
                extra={"bucket": bucket, "key": key, "source_bucket": source[0]},
                exc_info=True,
            )
            mode = "copy_fallback"
    body = await adapter.open(fetch_path)
    try:
        checksum, _size = await asyncio.to_thread(
            platform.upload_stream, s3_client, bucket, key, body,
            chunk_bytes=policy.chunk_bytes, concurrency=policy.concurrency,
        )
    finally:
        # Release the connection a real S3 StreamingBody holds open, on both
        # the success and the failure path (self-review: never leak it).
        close = getattr(body, "close", None)
        if close is not None:
            close()
    return checksum, mode


async def _reference_stage(
    repo: IngestRepo,
    association: IngestAssociation,
    config: IngestConfig,
    adapter: StorageAdapter,
    item_id: str,
    source_paths: list[str],
) -> int:
    """Reference mode: no byte copy. Record the stable source URL and advance the
    ledger settled → stored so EXTRACT/ITEMIZE run. Idempotent (only acts on a
    still-`settled` row). A per-member failure marks only that member failed."""
    stored = 0
    for source_path in source_paths:
        latest = await repo.get_latest_ledger(association.id, source_path)
        if latest is None or latest.status != STATUS_SETTLED:
            continue
        try:
            href = adapter.public_object_url(source_fetch_path(config.source_path, source_path))
            await repo.set_ledger_fields(
                latest.id, status=STATUS_STORED, item_id=item_id, source_href=href
            )
            stored += 1
        except Exception:
            await repo.set_ledger_fields(latest.id, status=STATUS_FAILED)
            logger.exception(
                "ingest reference-fetch failed for source file",
                extra={
                    "association_id": association.id,
                    "item_id": item_id,
                    "source_path": source_path,
                },
            )
    logger.info(
        "ingest reference-fetch group done",
        extra={
            "association_id": association.id,
            "item_id": item_id,
            "stored": stored,
            "files": len(source_paths),
        },
    )
    return stored
