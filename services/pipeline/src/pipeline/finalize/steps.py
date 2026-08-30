"""The producer-neutral finalize steps (Phase 7 spec §6.1, ADR 0014).

``run_finalize`` brackets the neutral step chain with the producer's
registered resolver/recorder hooks and contains **no producer branching** —
the seam test asserts this literally (this module never names a producer and
never imports a producer layer). Per resolved item the chain is:

    platform pre-flight → rewrite (in-memory) → validate → checksum →
    move (copy + verify) → upsert (restricted to output_collections) →
    delete staged originals

Two deliberate ordering notes:

- **Validation runs on the item as it will be after rewrite** (§6.1 step 2):
  hrefs are substituted in-memory first, so the validated document is the one
  that lands.
- **Staged originals are deleted only after the upsert commits.** The spec's
  step list reads "copy-verify-delete → rewrite → upsert"; deleting before
  the upsert would open a crash window (originals gone, item still carrying
  producer-staging hrefs) in which a re-run finds neither staged nor
  catalogued bytes and mis-reads a crash as a client error. Deferring the
  delete keeps §6.4's idempotency claim true at every crash point: before the
  upsert, staging is intact and the re-run walks through; after it, the item
  is already canonical (nothing left to move) and any leftover staged
  originals are TTL-swept — never a lost byte. The delete still happens only
  after the canonical copy is checksum-verified (the §6.4 named call site),
  and a delete failure is non-fatal (the TTL sweep is the one cleaner).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from copy import deepcopy

from pipeline.finalize.seam import (
    FinalizeOutcome,
    FinalizeRequest,
    FinalizeResult,
    ItemReport,
    PreflightChecks,
    ProducerHooks,
    RejectedItem,
    Resolution,
    ResolvedItem,
    UpsertedItem,
)
from pipeline.finalize.status import (
    REASON_CHECKSUM_MISMATCH,
    REASON_COLLECTION_ARCHIVED,
    REASON_GC_PENDING,
    REASON_INVALID_ITEM,
    REASON_MISSING_BYTES,
    REASON_WRONG_COLLECTION,
)
from pipeline.finalize.store import ObjectStore
from pipeline.metrics import FINALIZE_BYTES, FINALIZE_ITEMS
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter
from pipeline.stac.validate import ItemValidationError, validate_item
from pipeline.storage.keys import asset_href, canonical_asset_key

logger = logging.getLogger(__name__)


def item_canonical_prefix(collection_id: str, item_id: str) -> str:
    """The §5.3 canonical prefix holding ALL of one item's asset objects."""
    return f"assets/{collection_id}/{item_id}/"


def _rewrite_document(item: ResolvedItem, asset_href_base: str) -> dict:
    """Substitute each staged asset's href with its canonical `/api/assets/...`
    href, in-memory (the validated document is the one that lands)."""
    doc = deepcopy(item.document)
    assets = doc.get("assets") or {}
    for staged in item.staged_assets:
        entry = assets.get(staged.asset_key)
        if entry is not None:
            entry["href"] = asset_href(
                item.ref.collection_id,
                item.ref.item_id,
                staged.filename,
                base=asset_href_base,
            )
    return doc


async def _finalize_item(
    item: ResolvedItem,
    req: FinalizeRequest,
    *,
    preflight: PreflightChecks,
    store: ObjectStore,
    writer: PgstacWriter,
    asset_href_base: str,
) -> ItemReport:
    ref = item.ref
    checksums: dict[str, str] = {}
    bytes_moved = 0
    moved_any = False

    def _reject(reason: str, detail: str) -> ItemReport:
        return ItemReport(
            ref=ref,
            outcome="rejected",
            reason=reason,
            detail=detail,
            checksums=checksums,
            bytes_moved=bytes_moved,
            moved_any=moved_any,
        )

    # Upsert may only touch the request's declared output collections.
    if ref.collection_id not in req.output_collections:
        return _reject(
            REASON_WRONG_COLLECTION,
            f"collection {ref.collection_id!r} is not among the request's output collections",
        )

    # 1. Platform pre-flight (§6.1): execution-time safety re-checks.
    if await preflight.collection_archived(ref.collection_id):
        return _reject(
            REASON_COLLECTION_ARCHIVED,
            f"collection {ref.collection_id!r} is archived and takes no item writes",
        )
    prefix = item_canonical_prefix(ref.collection_id, ref.item_id)
    if await preflight.has_open_gc_mark(prefix):
        return _reject(
            REASON_GC_PENDING,
            "an open asset_gc mark covers the item's canonical prefix; "
            "retryable once the mark collects",
        )

    # 2. Rewrite in memory, then validate the document that will land.
    doc = _rewrite_document(item, asset_href_base)
    try:
        validate_item(doc)
    except ItemValidationError as exc:
        return _reject(REASON_INVALID_ITEM, str(exc))

    # 3. Checksum every staged object BEFORE any byte moves (a missing or
    #    unreadable object rejects the whole item with nothing yet written).
    stats = {}
    for staged in item.staged_assets:
        stat = await asyncio.to_thread(store.head, staged.staging_key)
        if stat is None:
            return _reject(
                REASON_MISSING_BYTES,
                f"staged object {staged.filename} was never uploaded",
            )
        data = await asyncio.to_thread(store.get, staged.staging_key)
        checksums[staged.filename] = f"sha256:{hashlib.sha256(data).hexdigest()}"
        stats[staged.staging_key] = stat

    # 4. Move: server-side copy + verify (ETag/size against the source we just
    #    hashed). Deletes are deferred until after the upsert (module note).
    for staged in item.staged_assets:
        canonical_key = canonical_asset_key(ref.collection_id, ref.item_id, staged.filename)
        await asyncio.to_thread(store.copy, staged.staging_key, canonical_key)
        moved_any = True
        src = stats[staged.staging_key]
        dest = await asyncio.to_thread(store.head, canonical_key)
        if dest is None or dest.etag != src.etag or dest.size != src.size:
            return _reject(
                REASON_CHECKSUM_MISMATCH,
                f"canonical copy of {staged.filename} does not match the staged object",
            )
        bytes_moved += src.size

    # 5. Upsert (emits the ordinary outbox event that drives delivery, §7).
    try:
        await writer.upsert_items([doc])
    except CollectionMissing as exc:
        return _reject(
            REASON_WRONG_COLLECTION,
            f"collection {ref.collection_id!r} does not exist in the catalog: {exc}",
        )

    # 6. Remove the staged originals — checksum-verified and upserted, so this
    #    is the §6.4 named deletion call site. Non-fatal: the TTL sweep is the
    #    backstop cleaner for anything left behind.
    for staged in item.staged_assets:
        try:
            await asyncio.to_thread(store.delete, staged.staging_key)
        except Exception:
            logger.warning(
                "finalize: staged original not deleted; TTL sweep will collect it",
                extra={"staging_key": staged.staging_key, "item_id": ref.item_id},
            )

    return ItemReport(
        ref=ref,
        outcome="upserted",
        checksums=checksums,
        bytes_moved=bytes_moved,
        moved_any=moved_any,
    )


async def run_finalize(
    req: FinalizeRequest,
    *,
    hooks: dict[str, ProducerHooks],
    preflight: PreflightChecks,
    store: ObjectStore,
    writer: PgstacWriter,
    asset_href_base: str = "/api/assets",
) -> FinalizeResult:
    """The ADR 0014 entrypoint: resolve → neutral steps per item → record.

    Producer differences live only in ``req`` and in the ``hooks`` registered
    for ``req.producer`` — this function and the steps never branch on the
    producer value (they only pass it through to logs/metrics labels).
    """
    try:
        producer_hooks = hooks[req.producer]
    except KeyError:
        raise ValueError(f"no finalize hooks registered for producer {req.producer!r}") from None

    resolution: Resolution = await producer_hooks.resolver.resolve(req)

    if resolution.skip:
        logger.info(
            "finalize skipped (no-op)",
            extra={
                "producer": req.producer,
                "skip": resolution.skip,
                "detail": resolution.skip_detail,
                "staging_prefix": req.staging_prefix,
            },
        )
        FINALIZE_ITEMS.labels(producer=req.producer, outcome=resolution.skip).inc()
        return FinalizeResult()

    reports: list[ItemReport] = [
        ItemReport(ref=r.item_ref, outcome="rejected", reason=r.reason, detail=r.detail)
        for r in resolution.rejected
    ]
    for resolved in resolution.items:
        reports.append(
            await _finalize_item(
                resolved,
                req,
                preflight=preflight,
                store=store,
                writer=writer,
                asset_href_base=asset_href_base,
            )
        )

    result = FinalizeResult(
        upserted=tuple(
            UpsertedItem(collection_id=r.ref.collection_id, item_id=r.ref.item_id)
            for r in reports
            if r.outcome == "upserted"
        ),
        rejected=tuple(
            RejectedItem(item_ref=r.ref, reason=r.reason or "", detail=r.detail)
            for r in reports
            if r.outcome == "rejected"
        ),
    )

    for report in reports:
        FINALIZE_ITEMS.labels(producer=req.producer, outcome=report.outcome).inc()
        if report.bytes_moved:
            FINALIZE_BYTES.labels(producer=req.producer).inc(report.bytes_moved)

    await producer_hooks.recorder.record(
        req, FinalizeOutcome(result=result, reports=tuple(reports), claimed=resolution.claimed)
    )

    logger.info(
        "finalize done",
        extra={
            "producer": req.producer,
            "staging_prefix": req.staging_prefix,
            "upserted": len(result.upserted),
            "rejected": len(result.rejected),
        },
    )
    return result
