"""The push-ingest producer layer (Phase 7 spec §6.1 brackets, §6.3 tiers).

Everything producer-specific about push lives here, OUTSIDE the neutral steps
(the ADR 0014 seam discipline): the resolver claims the ``staged_uploads``
ledger row and enforces the §4.2 admission rules; the recorder stamps the
row's verdict and applies the §6.3 op-discriminated rejection outcome. The
triggering event's ``op`` reaches the recorder inside
``provenance = {upload_id, event_op}`` — producer-specific by design
(residual R5), so the step signatures stay producer-free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline.finalize.repo import FinalizeRepo
from pipeline.finalize.seam import (
    PRODUCER_PUSH_INGEST,
    REF_PGSTAC,
    REF_STAGED,
    FinalizeOutcome,
    FinalizeRequest,
    ItemRef,
    OutcomeRecorder,
    ProducerResolver,
    RejectedItem,
    Resolution,
    ResolvedItem,
    StagedAsset,
)
from pipeline.finalize.status import (
    REASON_BOUND_TO_OTHER_ITEM,
    REASON_INVALID_ITEM,
    REASON_MULTI_SESSION,
    REASON_UNKNOWN_SESSION,
    REASON_WRONG_COLLECTION,
    TERMINAL_STATUSES,
    finalized_result,
    rejected_result,
    validate_status_doc,
)
from pipeline.finalize.steps import item_canonical_prefix
from pipeline.stac.pgstac_writer import PgstacWriter
from pipeline.storage.keys import (
    InvalidKeySegment,
    is_staged_href,
    parse_staged_href,
    staging_key,
    staging_prefix,
)

logger = logging.getLogger(__name__)

#: asset_gc reason for the insert-rejection delete (migration 017 CHECK set).
GC_REASON_ITEM_DELETE = "item_delete"


def build_push_request(
    upload_id: str, collection_id: str, item_id: str, event_op: str | None
) -> FinalizeRequest:
    """The push-shaped :class:`FinalizeRequest` (ADR 0014): one ``pgstac``
    ref, the session's staging prefix, and ``{upload_id, event_op}``
    provenance. ``event_op`` is ``None`` on a sweep-requeued recovery run —
    the recorder then applies the conservative rejection outcome (never a
    delete, see :class:`PushRecorder`)."""
    return FinalizeRequest(
        producer=PRODUCER_PUSH_INGEST,
        staging_prefix=staging_prefix(upload_id),
        output_collections=(collection_id,),
        items=(ItemRef(kind=REF_PGSTAC, collection_id=collection_id, item_id=item_id),),
        provenance={"upload_id": upload_id, "event_op": event_op},
    )


@dataclass
class PushResolver(ProducerResolver):
    """Claim + §4.2 admission for one push item (the ledger claim is always
    singular — strictly one upload session per item, both directions)."""

    repo: FinalizeRepo

    async def resolve(self, req: FinalizeRequest) -> Resolution:
        if len(req.items) != 1:
            raise ValueError("push finalize requests carry exactly one item ref")
        ref = req.items[0]
        if ref.kind == REF_STAGED:
            # Phase 9's ref kind — loading item JSON from staging is its first
            # task (§6.1 honest scoping), not a Phase 7 code path.
            raise NotImplementedError("staged item refs are a Phase 9 resolver")
        upload_id = str(req.provenance["upload_id"])

        session = await self.repo.get_session(upload_id)
        if session is None:
            # No ledger row exists, so there is nothing to claim or stamp —
            # the rejection applies to the ITEM only (claimed=False).
            return Resolution(
                rejected=(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_UNKNOWN_SESSION,
                        detail=f"no upload session {upload_id!r}",
                    ),
                ),
                claimed=False,
            )
        if session.status in TERMINAL_STATUSES or session.status == "finalizing":
            # §6.3 no-zombie rule: the row's verdict is already written (or a
            # concurrent duplicate holds the claim) — structured-log no-op,
            # never a fresh rejection stamp.
            return Resolution(skip="stale_claim", skip_detail=f"session status {session.status}")
        if session.item_id is not None and session.item_id != ref.item_id:
            # The row belongs to another item's push — reject THIS item
            # without touching the other push's pending session (claimed=False).
            return Resolution(
                rejected=(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_BOUND_TO_OTHER_ITEM,
                        detail=f"upload session {upload_id!r} is bound to another item",
                    ),
                ),
                claimed=False,
            )
        if session.collection_id != ref.collection_id:
            # Minted for a different collection. Deliberately unclaimed: the
            # session's rightful owner keeps a usable pending session (a
            # foreign item referencing it must not kill it), and an operator
            # who pushed to the wrong collection can re-push correctly.
            return Resolution(
                rejected=(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_WRONG_COLLECTION,
                        detail=(
                            f"upload session {upload_id!r} was minted for collection "
                            f"{session.collection_id!r}"
                        ),
                    ),
                ),
                claimed=False,
            )

        if not await self.repo.claim(upload_id, ref.item_id):
            # Raced by a concurrent duplicate between the read and the claim.
            return Resolution(skip="stale_claim", skip_detail="claim lost to a concurrent run")

        document = await self.repo.get_item(ref.collection_id, ref.item_id)
        if document is None:
            # The item vanished between the event and this run (deleted, or
            # never became visible). Nothing to finalize and no verdict to
            # write: hand the row back to the TTL clock.
            await self.repo.release_claim(upload_id)
            return Resolution(skip="item_missing", skip_detail="item not present in pgstac")

        staged: list[StagedAsset] = []
        session_ids: set[str] = set()
        for asset_key, entry in (document.get("assets") or {}).items():
            href = (entry or {}).get("href")
            if not is_staged_href(href):
                continue  # ordinary external hrefs pass through untouched
            try:
                parts = parse_staged_href(href)
            except InvalidKeySegment as exc:
                return Resolution(
                    rejected=(
                        RejectedItem(
                            item_ref=ref,
                            reason=REASON_INVALID_ITEM,
                            detail=f"malformed staged href on asset {asset_key!r}: {exc}",
                        ),
                    ),
                    claimed=True,
                )
            session_ids.add(parts.upload_id)
            staged.append(
                StagedAsset(
                    asset_key=asset_key,
                    filename=parts.filename,
                    staging_key=staging_key(parts.upload_id, parts.filename),
                )
            )

        if len(session_ids) > 1:
            # §4.2: an item whose staged hrefs span more than one session is a
            # rejection — the claim stays singular by construction.
            return Resolution(
                rejected=(
                    RejectedItem(
                        item_ref=ref,
                        reason=REASON_MULTI_SESSION,
                        detail=f"staged hrefs reference {len(session_ids)} upload sessions",
                    ),
                ),
                claimed=True,
            )
        if session_ids and upload_id not in session_ids:
            # The item now references a DIFFERENT session (a second PUT
            # replaced this one mid-flight). This session will never finalize
            # this item; rejecting would wrongly hit the item the newer
            # session is about to finalize. Release and stand down.
            await self.repo.release_claim(upload_id)
            return Resolution(
                skip="superseded",
                skip_detail="item no longer references this upload session",
            )

        # Zero staged hrefs is legal here: a crash-recovery re-run after the
        # move+upsert already landed finds the item canonical — the steps then
        # have nothing to move and the re-run records the verdict (§6.4).
        return Resolution(
            items=(ResolvedItem(ref=ref, document=document, staged_assets=tuple(staged)),),
            claimed=True,
        )


@dataclass
class PushRecorder(OutcomeRecorder):
    """Stamp the ledger verdict and apply the §6.3 tier-2 catalog outcome."""

    repo: FinalizeRepo
    writer: PgstacWriter

    async def record(self, req: FinalizeRequest, outcome: FinalizeOutcome) -> None:
        upload_id = str(req.provenance["upload_id"])
        event_op = req.provenance.get("event_op")
        reports = outcome.reports
        if not reports:
            return

        if all(r.outcome == "upserted" for r in reports):
            checksums: dict[str, str] = {}
            for r in reports:
                checksums.update(r.checksums)
            doc = finalized_result(
                [
                    {"collection_id": u.collection_id, "item_id": u.item_id}
                    for u in outcome.result.upserted
                ],
                checksums,
            )
            validate_status_doc({"status": "finalized", "result": doc, "error": None})
            await self.repo.record_finalized(upload_id, doc)
            logger.info(
                "push finalize recorded",
                extra={"upload_id": upload_id, "items": len(reports), "producer": req.producer},
            )
            return

        # Rejection: the §6.3 tier outcome, then the ledger stamp. The §4.3
        # snapshot is the PRIMARY discriminator — the transaction-API write
        # path splits a client PUT into delete+insert outbox events (ISSUES
        # I-46), so ``event_op == "insert"`` does NOT prove the item never
        # existed. Snapshot present ⇒ brokered update ⇒ restore, whatever the
        # op says. Without a snapshot, delete only an op-says-insert item
        # with no event history predating the session (a provable create);
        # anything else leaves the stored document (never destroy what this
        # push cannot be proven to have created).
        report = next(r for r in reports if r.outcome == "rejected")
        ref = report.ref
        restored = False
        session = await self.repo.get_session(upload_id)
        prior = session.prior_item if session else None
        if prior is not None:
            await self.writer.upsert_items([prior])
            restored = True
        elif (
            event_op == "insert"
            and session is not None
            and session.created_at is not None
            and not await self.repo.item_predates(
                ref.collection_id, ref.item_id, session.created_at
            )
        ):
            # A true create: delete restores the exact prior state — absence.
            # Mark-first (ADR 0011) when the steps already moved bytes under
            # the canonical prefix, so the partial move cannot orphan.
            if report.moved_any:
                await self.repo.mark_asset_prefix(
                    item_canonical_prefix(ref.collection_id, ref.item_id),
                    ref.collection_id,
                    ref.item_id,
                    GC_REASON_ITEM_DELETE,
                    await self.repo.gc_grace_days(ref.collection_id),
                )
            await self.repo.delete_item(ref.item_id, ref.collection_id)
        # else: tier-3 leave-broken (direct-path update, an unprovable
        # create, or a sweep-recovery run with no snapshot) — the distinct
        # alert wording and docs/push-ingest.md cover the manual fix.

        reasons = [r.reason or REASON_INVALID_ITEM for r in reports if r.outcome == "rejected"]
        doc = rejected_result(reasons, restored=restored)
        error = "; ".join(r.detail for r in reports if r.outcome == "rejected" and r.detail)
        validate_status_doc({"status": "rejected", "result": doc, "error": error or None})
        if outcome.claimed:
            await self.repo.record_rejected(upload_id, doc, error or None)
        else:
            # No claim was taken (unknown session / a row bound elsewhere):
            # the item outcome applied above, but there is no ledger row of
            # ours to stamp — log loudly instead (the alert monitor watches
            # rejected ROWS, so this path is visible only in logs/metrics).
            logger.warning(
                "push rejection on an unclaimed session (no ledger verdict written)",
                extra={
                    "upload_id": upload_id,
                    "collection_id": ref.collection_id,
                    "item_id": ref.item_id,
                    "reasons": reasons,
                },
            )
        logger.info(
            "push finalize rejected",
            extra={
                "upload_id": upload_id,
                "collection_id": ref.collection_id,
                "item_id": ref.item_id,
                "reasons": reasons,
                "event_op": event_op,
                "restored": restored,
                "producer": req.producer,
            },
        )
