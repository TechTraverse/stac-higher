"""ITEMIZE stage: validate + upsert a group's STAC item, then post-ingest (§6.1).

Orchestrates the chain tail against seams (repo, pgstac writer, adapter, S3
client) so it is fully unit-testable. Re-reads each source file's latest ledger
row and acts only on `stored` members (idempotent, restart-safe): a crash mid-run
leaves them `stored` for the re-enqueued job to re-upsert (upsert is idempotent).
EXTRACT failure or a validation failure marks the members `failed` (no bad item
reaches the catalog); a missing collection is a permanent `failed`. On success
the members go `itemized` and post-ingest cleans the source.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.config import IngestConfig
from pipeline.ingest.extract import (
    ExtractError,
    ExtractMember,
    MetadataConfig,
    bbox_to_polygon,
    build_item,
    parse_metadata,
)
from pipeline.ingest.postingest import apply_post_ingest
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_ITEMIZED,
    STATUS_STORED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)

#: A bbox equal to (within this tolerance) the whole world is treated as "no
#: real extent" — the collection fallback then degrades to `global_fallback`
#: rather than claiming a bogus worldwide footprint as `collection_extent`.
_WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]
_GLOBAL_BBOX_EPSILON = 1e-6


def _is_global_bbox(bbox: Sequence[float]) -> bool:
    return all(abs(v - w) < _GLOBAL_BBOX_EPSILON for v, w in zip(bbox, _WORLD_BBOX, strict=False))


def _normalize_bbox_2d(bbox: Sequence[float]) -> list[float] | None:
    """Reduce a STAC bbox to its horizontal 2D extent `[west, south, east,
    north]`. STAC spatial extents may be 3D — `[w, s, min_elev, e, n,
    max_elev]` — and `PgstacWriter.get_collection_bbox` faithfully returns
    whatever pgstac stores. A naive `bbox[:4]` slice on a 6-element bbox
    mangles `min_elev` into "east" and silently drops `north`, so this
    normalizes by position instead of truncating. Any other length is
    unusable and returns `None` (the caller then falls back to the global
    world polygon)."""
    if len(bbox) == 6:
        return [bbox[0], bbox[1], bbox[3], bbox[4]]
    if len(bbox) == 4:
        return list(bbox)
    return None


async def _build_collection_fallback(
    writer: PgstacWriter, association: IngestAssociation, cfg: MetadataConfig
) -> dict[str, Any] | None:
    """The ISSUE I-27 opt-in collection-extent geometry fallback: only
    consulted when the association's metadata.defaults.geometry is
    `"collection"`. Degrades to a `global_fallback` world polygon when the
    collection has no usable (non-global) extent."""
    if cfg.default_geometry != "collection":
        return None
    raw_bbox = await writer.get_collection_bbox(association.collection_id)
    bbox = _normalize_bbox_2d(raw_bbox) if raw_bbox else None
    if not bbox or _is_global_bbox(bbox):
        return {
            "geometry": bbox_to_polygon(_WORLD_BBOX),
            "bbox": list(_WORLD_BBOX),
            "source": "global_fallback",
        }
    return {"geometry": bbox_to_polygon(bbox), "bbox": bbox, "source": "collection_extent"}


class ItemValidationError(Exception):
    """The built item fails stac-pydantic validation."""


@dataclass
class ItemizeOutcome:
    status: str  # "itemized" | "failed" | "skipped"
    item_id: str
    detail: str = ""


def validate_item(item_dict: Mapping[str, Any]) -> None:
    """stac-pydantic gate (offline, core-structural). Raises on invalid.

    Uses the core ``stac_pydantic.Item`` (not ``stac_pydantic.api.Item``): the
    API variant additionally requires a ``root`` link, which EXTRACT-built
    items never carry (they are plain catalog items, not API page entries).
    """
    from pydantic import ValidationError
    from stac_pydantic import Item

    try:
        Item.model_validate(dict(item_dict))
    except ValidationError as exc:
        raise ItemValidationError(str(exc)) from exc


def _member(entry: LedgerEntry, collection_id: str, item_id: str) -> ExtractMember:
    filename = entry.source_path.rsplit("/", 1)[-1]
    return ExtractMember(
        source_path=entry.source_path,
        filename=filename,
        canonical_key=canonical_asset_key(collection_id, item_id, filename),
        observed_at=entry.updated_at,
    )


async def _mark(
    repo: IngestRepo, entries: list[LedgerEntry], status: str, item_id: str | None
) -> None:
    # One statement for all members (all-or-nothing): a crash mid-mark must
    # never leave the group split across statuses, which would let a retry
    # rebuild the item from a subset of members (§ final-review fix).
    if not entries:
        return
    await repo.set_ledger_status_many([e.id for e in entries], status=status, item_id=item_id)


async def run_itemize(
    repo: IngestRepo,
    writer: PgstacWriter,
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    *,
    association: IngestAssociation,
    config: IngestConfig,
    item_id: str,
    source_paths: Sequence[str],
    bucket: str,
    asset_href_base: str,
) -> ItemizeOutcome:
    # Re-read: act only on members still `stored` (idempotent guard).
    stored: list[LedgerEntry] = []
    for sp in source_paths:
        row = await repo.get_latest_ledger(association.id, sp)
        if row is not None and row.status == STATUS_STORED:
            stored.append(row)
    if not stored:
        return ItemizeOutcome("skipped", item_id, "no stored members")

    members = [_member(e, association.collection_id, item_id) for e in stored]

    # EXTRACT (ISSUE I-27: opt in to the collection-extent geometry fallback
    # only when metadata.defaults.geometry == "collection" — the writer is
    # otherwise never consulted for this).
    cfg = parse_metadata(config.metadata)
    collection_fallback = await _build_collection_fallback(writer, association, cfg)
    try:
        item_dict = await build_item(
            collection_id=association.collection_id,
            item_id=item_id,
            members=members,
            metadata=config.metadata,
            s3_client=s3_client,
            bucket=bucket,
            asset_href_base=asset_href_base,
            collection_fallback=collection_fallback,
        )
    except ExtractError as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.warning("itemize extract failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"extract: {exc}")

    # VALIDATE
    try:
        validate_item(item_dict)
    except ItemValidationError as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.warning("itemize validation failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"validation: {exc}")

    # UPSERT
    try:
        await writer.upsert_items([item_dict])
    except CollectionMissing as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.error("itemize upsert failed: collection missing", extra={"item_id": item_id})
        return ItemizeOutcome("failed", item_id, f"collection missing: {exc}")
    # Any other exception propagates → the job retries (transient DB errors).

    await _mark(repo, stored, STATUS_ITEMIZED, item_id)

    # post-ingest (non-fatal)
    await apply_post_ingest(adapter, config, source_paths=[e.source_path for e in stored])

    logger.info("itemize done", extra={"item_id": item_id, "members": len(stored)})
    return ItemizeOutcome("itemized", item_id)
