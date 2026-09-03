"""ITEMIZE stage: validate + upsert a group's STAC item, then post-ingest (§6.1).

Orchestrates the chain tail against seams (repo, pgstac writer, adapter, S3
client) so it is fully unit-testable. Re-reads each source file's latest ledger
row and acts only on `stored` members (idempotent, restart-safe): a crash mid-run
leaves them `stored` for the re-enqueued job to re-upsert (upsert is idempotent).
EXTRACT failure or a validation failure marks the members `failed` (no bad item
reaches the catalog); a missing collection is a permanent `failed`. On success
the members go `itemized` and post-ingest cleans the source.

For `metadata.strategy: extractor` (GOES spec §6.2) the stage splits at the
seam: the group is parked `extracting` and handed to an extractor run with the
draft item in its batch, and finalize's extract branch calls `complete_item` —
the one implementation of the VALIDATE → UPSERT → mark → post-ingest tail that
both paths share.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.config import IngestConfig
from pipeline.ingest.extract import (
    CanonicalByteSource,
    ExtractError,
    ExtractMember,
    MetadataConfig,
    SourceAdapterByteSource,
    bbox_to_polygon,
    build_item,
    parse_metadata,
)
from pipeline.ingest.postingest import apply_post_ingest
from pipeline.ingest.repo import (
    STATUS_EXTRACTING,
    STATUS_FAILED,
    STATUS_ITEMIZED,
    STATUS_STORED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.process.repo import ProcessRepo
from pipeline.process.trigger import trigger_run
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter
from pipeline.stac.validate import ItemValidationError, validate_item
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


@dataclass
class ItemizeOutcome:
    #: "itemized" | "failed" | "skipped" | "extracting" (detail = the run id)
    status: str
    item_id: str
    detail: str = ""
    #: telemetry for the flow_stats rollup (M2-A): member bytes itemized and
    #: the first-seen → itemized latency, populated on success only.
    bytes: int = 0
    latency_seconds: float | None = None


# The stac-pydantic gate now lives in ``pipeline/stac/validate.py`` (Phase 7
# §6.2: one gate, shared verbatim with finalize). Re-exported here so existing
# importers keep working.
__all__ = [
    "ItemValidationError",
    "ItemizeOutcome",
    "complete_item",
    "run_itemize",
    "validate_item",
]


def _member(entry: LedgerEntry, collection_id: str, item_id: str) -> ExtractMember:
    filename = entry.source_path.rsplit("/", 1)[-1]
    return ExtractMember(
        source_path=entry.source_path,
        filename=filename,
        canonical_key=canonical_asset_key(collection_id, item_id, filename),
        # I-100: the listed object mtime when DISCOVER recorded one, else the settle time.
        observed_at=entry.source_mtime or entry.updated_at,
    )


async def _mark(
    repo: IngestRepo,
    entries: list[LedgerEntry],
    status: str,
    item_id: str | None,
    reason: str | None = None,
) -> None:
    # One statement for all members (all-or-nothing): a crash mid-mark must
    # never leave the group split across statuses, which would let a retry
    # rebuild the item from a subset of members (§ final-review fix).
    if not entries:
        return
    await repo.set_ledger_status_many(
        [e.id for e in entries], status=status, item_id=item_id, reason=reason
    )


async def complete_item(
    repo: IngestRepo,
    writer: PgstacWriter,
    adapter: StorageAdapter,
    *,
    association: IngestAssociation,
    config: IngestConfig,
    item_id: str,
    members: list[LedgerEntry],
    item_dict: dict[str, Any],
) -> ItemizeOutcome:
    """The chain tail both paths share: VALIDATE → UPSERT → mark `itemized` →
    post-ingest → telemetry. `run_itemize` calls it for every non-extractor
    strategy; finalize's extract branch calls it with the item the extractor
    produced, so a run's output lands through exactly the same gate as an
    inline itemize (GOES spec §6.2)."""
    # VALIDATE
    try:
        validate_item(item_dict)
    except ItemValidationError as exc:
        await _mark(repo, members, STATUS_FAILED, None, reason=f"validation: {exc}")
        logger.warning("itemize validation failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"validation: {exc}")

    # UPSERT
    try:
        await writer.upsert_items([item_dict])
    except CollectionMissing as exc:
        await _mark(repo, members, STATUS_FAILED, None, reason=f"collection missing: {exc}")
        logger.error("itemize upsert failed: collection missing", extra={"item_id": item_id})
        return ItemizeOutcome("failed", item_id, f"collection missing: {exc}")
    # Any other exception propagates → the queue-level RetrySpec re-attempts
    # the job (transient DB errors), and the stored-stall sweep re-drives
    # anything that still never lands (ISSUES I-55).

    await _mark(repo, members, STATUS_ITEMIZED, item_id)

    # post-ingest (non-fatal)
    await apply_post_ingest(adapter, config, source_paths=[e.source_path for e in members])

    logger.info(
        "itemize done",
        extra={
            "item_id": item_id,
            "association_id": association.id,
            "members": len(members),
        },
    )
    first_seen = min((e.created_at for e in members if e.created_at), default=None)
    latency = (
        (dt.datetime.now(dt.UTC) - first_seen).total_seconds()
        if first_seen is not None
        else None
    )
    return ItemizeOutcome(
        "itemized",
        item_id,
        bytes=sum(e.size or 0 for e in members),
        latency_seconds=latency,
    )


async def _hand_to_extractor(
    repo: IngestRepo,
    process_repo: ProcessRepo | None,
    *,
    association: IngestAssociation,
    cfg: MetadataConfig,
    item_id: str,
    stored: list[LedgerEntry],
    draft: dict[str, Any],
    enqueue_now: Callable[[str], Awaitable[None]] | None,
    now: dt.datetime,
) -> ItemizeOutcome:
    """GOES spec §6.2 step 1: park the group as `extracting` and queue the
    extractor run with the draft in its batch. Everything that can be checked
    before spending a run — the process exists, is an extractor, is deployed —
    is checked here, and a miss fails the rows with the reason."""
    process_id = cfg.extractor_process_id or ""

    async def fail(reason: str) -> ItemizeOutcome:
        await _mark(repo, stored, STATUS_FAILED, None, reason=reason)
        logger.warning("itemize extractor unusable", extra={"item_id": item_id, "error": reason})
        return ItemizeOutcome("failed", item_id, reason)

    if process_repo is None:
        return await fail("extractor strategy but no process repository configured")
    kind = await process_repo.process_kind(process_id)
    if kind is None:
        return await fail(f"extractor process {process_id} does not exist")
    if kind != "extractor":
        return await fail(f"process {process_id} is a {kind}, not an extractor")
    # Before the revision read: `current_revision` filters on `enabled`, so a
    # disabled extractor would otherwise be reported as "no deployed revision"
    # and send the operator to the wrong screen (G-6 final review).
    if not await process_repo.process_is_enabled(process_id):
        return await fail(f"extractor process {process_id} is disabled")
    revision_id = await process_repo.current_revision(process_id)
    if not revision_id:
        return await fail(f"extractor process {process_id} has no deployed revision")

    # Park FIRST (all-or-nothing), then trigger: a crash between the two
    # leaves rows `extracting` with no run id, which the extract-stall sweep
    # fails after its threshold. The reverse order could execute a run whose
    # rows a retry then rebuilds a second time.
    # Park WITH the item id: the run planner resolves reference-mode inputs
    # through `reference_source_hrefs(collection, item_id)`, whose SQL filters
    # the ledger on `item_id`. Blanking it here made every reference-mode input
    # unresolvable on first ingest (G-6 final review). FETCH's convention is
    # that a non-failed row carries its item id.
    await _mark(repo, stored, STATUS_EXTRACTING, item_id)
    result = await trigger_run(
        process_repo,
        process_id=process_id,
        revision_id=revision_id,
        source_id=None,
        association_id=association.id,
        input_items=[
            {
                "item_id": item_id,
                "collection_id": association.collection_id,
                "op": "insert",
                "ledger_ids": [e.id for e in stored],
                "draft": draft,
            }
        ],
        now=now,
        enqueue_now=enqueue_now,
    )
    if result.run_id is None:
        return await fail("extractor run could not be queued")
    await repo.set_extract_run([e.id for e in stored], result.run_id)
    logger.info(
        "itemize handed to extractor",
        extra={"item_id": item_id, "run_id": result.run_id, "merged": result.merged},
    )
    return ItemizeOutcome("extracting", item_id, result.run_id)


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
    process_repo: ProcessRepo | None = None,
    enqueue_now: Callable[[str], Awaitable[None]] | None = None,
    now: dt.datetime | None = None,
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
    byte_source = (
        SourceAdapterByteSource(adapter, config.source_path)
        if config.storage_mode == "reference"
        else CanonicalByteSource(s3_client, bucket)
    )
    try:
        item_dict = await build_item(
            collection_id=association.collection_id,
            item_id=item_id,
            members=members,
            metadata=config.metadata,
            byte_source=byte_source,
            asset_href_base=asset_href_base,
            collection_fallback=collection_fallback,
            cfg=cfg,
        )
    except ExtractError as exc:
        await _mark(repo, stored, STATUS_FAILED, None, reason=f"extract: {exc}")
        logger.warning("itemize extract failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"extract: {exc}")

    if cfg.strategy == "extractor":
        return await _hand_to_extractor(
            repo, process_repo, association=association, cfg=cfg, item_id=item_id,
            stored=stored, draft=item_dict, enqueue_now=enqueue_now,
            now=now or dt.datetime.now(dt.UTC),
        )
    return await complete_item(
        repo, writer, adapter, association=association, config=config,
        item_id=item_id, members=stored, item_dict=item_dict,
    )
