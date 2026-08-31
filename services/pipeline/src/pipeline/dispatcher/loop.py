"""Dispatch orchestration (Slice B-i: grouping + enqueue; Slice C: NOTIFY wake).

dispatch_once claims one batch of pending outbox rows, matches each non-delete
item against its collection's delivery associations, groups the matches into
one delivery batch per association, hands them to ``enqueue``, THEN marks the
claimed batch processed so the outbox drains. The primary wake path is the
LISTEN loop (``dispatcher.listener``); the minute poll stays as fallback —
both call this via ``dispatch_until_empty``.

Staged gate (Phase 7 spec §7.1/§7.2): an insert/update event whose current
item carries any ``staging://`` asset href is not deliverable — it is routed
to ``enqueue_finalize`` (payload per event: ``{upload_id, collection_id,
item_id, event_op}``, upload_id parsed from the FIRST staged href) and
drained without matching associations. No delivery ever streams from staging.
Finalize's upsert emits a fresh update event whose hrefs are canonical, and
THAT event dispatches to delivery once — the href content is the readiness
marker, so there is no state machine here. The gate is per-item-content:
items with no staged hrefs dispatch exactly as before.

Delete-event GC mark (spec §7.3): a claimed ``delete`` event marks the item's
canonical asset prefix in ``asset_gc`` (via ``mark_delete_gc``) BEFORE
draining, closing the I-51-shaped orphan an external proxy DELETE would
otherwise leave. A mark failure is routed through the I-38 defer path
(release with cool-off) rather than the I-39 poison-drain, so the event is
not drained until the mark commits or the bounded retry budget is spent.
Delete events still never match delivery associations.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation, Match, match_item
from pipeline.dispatcher.repo import DispatchRepo
from pipeline.storage.keys import is_staged_href, parse_staged_href

logger = logging.getLogger(__name__)

#: enqueue callback: given the grouped delivery batches, hand them to the queue.
EnqueueDeliveries = Callable[[list[dict[str, Any]]], Awaitable[None]]

#: enqueue callback for staged events: one ``pipeline.finalize`` payload per
#: event (``{upload_id, collection_id, item_id, event_op}``), batched per
#: claim like delivery batches.
EnqueueFinalizes = Callable[[list[dict[str, Any]]], Awaitable[None]]

#: GC-mark callback for delete events: ``(collection_id, item_id)`` → mark the
#: item's canonical prefix in ``asset_gc`` (idempotent; reason ``item_delete``).
MarkDeleteGc = Callable[[str, str], Awaitable[None]]

#: I-38 bounded visibility retry: an event whose item is not yet visible is
#: released (cool-off below) up to this many times before it drains for good.
#: The retry is driven by the next wake (NOTIFY or the minute poll), so the
#: worst case is ~MAX_VISIBILITY_ATTEMPTS minutes on a quiet outbox.
MAX_VISIBILITY_ATTEMPTS = 5
#: cool-off keeping a released event out of the claim window, so the listener's
#: drain-until-empty loop cannot burn the whole budget in one wake.
VISIBILITY_RETRY_SECONDS = 15


@dataclass
class DispatchResult:
    """One dispatch_once outcome. ``claimed`` drives drain-until-empty (a wake
    keeps dispatching while claims come back non-empty); ``matches`` is what
    was handed to ``enqueue``."""

    claimed: int = 0
    matches: list[Match] = field(default_factory=list)
    #: staged events routed to finalize this pass (spec §7.1).
    finalizes: int = 0


def _first_staged_href(item: dict[str, Any]) -> str | None:
    """The first ``staging://`` asset href in the item, in asset order, or
    None when the item is fully canonical/external (the hot path)."""
    for asset in (item.get("assets") or {}).values():
        href = asset.get("href") if isinstance(asset, dict) else None
        if is_staged_href(href):
            return href
    return None


async def dispatch_once(
    repo: DispatchRepo,
    enqueue: EnqueueDeliveries,
    *,
    enqueue_finalize: EnqueueFinalizes,
    mark_delete_gc: MarkDeleteGc,
    batch_size: int = 100,
) -> DispatchResult:
    """Claim a batch of outbox rows, route staged items to finalize and mark
    delete events for GC, match the remaining non-delete items against their
    collections' delivery associations, group matches into per-association
    delivery batches, hand them to their queues, THEN drain the outbox.

    Enqueue-before-drain gives at-least-once delivery/finalize: if either
    enqueue raises, the outbox rows stay pending and a later tick re-drives
    them (finalize's ledger claim makes the duplicate a no-op).
    """
    events = await repo.claim_pending_events(batch_size)
    if not events:
        return DispatchResult()

    # Cache deliver associations per collection for this batch (a bulk upsert of
    # N items into one collection shares collection_id → one lookup, not N).
    assoc_cache: dict[str, list[DeliverAssociation]] = {}
    matches: list[Match] = []
    # association_id → batch payload (preserves association + item order).
    batches: dict[str, dict[str, Any]] = {}
    # pipeline.finalize payloads for staged events (spec §7.1), claim-batched.
    finalizes: list[dict[str, Any]] = []
    # I-38: events released for a later visibility retry instead of drained.
    deferred: set[int] = set()

    for event in events:
        # Per-event isolation (ISSUES I-39): one poison event must never abort
        # the batch — an uncaught raise here would leave the whole claim
        # unprocessed and busy-loop on the offending row every tick. The
        # poison event is logged loudly and drained with the batch (its
        # delivery is skipped — dead-lettered by drain).
        try:
            # Deletions never propagate to destinations (ROADMAP §6.4), but
            # they DO mark asset_gc first (spec §7.3): an external proxy
            # DELETE runs nothing app-side, so this mark is what keeps the
            # item's canonical bytes from orphaning. Idempotent against
            # app/pipeline marks (open-key unique index). A mark failure is
            # transient-until-proven-otherwise: defer via the I-38 path (the
            # poison-drain would drain the event with no mark written).
            if event.op == "delete":
                try:
                    await mark_delete_gc(event.collection_id, event.item_id)
                except Exception:
                    if event.dispatch_attempts < MAX_VISIBILITY_ATTEMPTS:
                        deferred.add(event.id)
                        logger.warning(
                            "dispatch: gc mark failed, deferring delete event",
                            exc_info=True,
                            extra={
                                "event_id": event.id,
                                "collection_id": event.collection_id,
                                "item_id": event.item_id,
                                "dispatch_attempts": event.dispatch_attempts,
                            },
                        )
                    else:
                        logger.exception(
                            "dispatch: gc mark failed at attempt cap, draining"
                            " delete event — canonical bytes may be orphaned",
                            extra={
                                "event_id": event.id,
                                "collection_id": event.collection_id,
                                "item_id": event.item_id,
                                "dispatch_attempts": event.dispatch_attempts,
                            },
                        )
                continue
            item = await repo.get_item(event.collection_id, event.item_id)
            if item is None:
                # Race (I-38): the outbox row beat the item's visibility.
                # Bounded retry: release the claim with a cool-off so a later
                # wake re-drives it; at the cap, drain with a loud log.
                if event.dispatch_attempts < MAX_VISIBILITY_ATTEMPTS:
                    deferred.add(event.id)
                    logger.info(
                        "dispatch: item not yet visible, deferring event",
                        extra={
                            "event_id": event.id,
                            "collection_id": event.collection_id,
                            "item_id": event.item_id,
                            "dispatch_attempts": event.dispatch_attempts,
                        },
                    )
                else:
                    logger.warning(
                        "dispatch: item never became visible, draining event",
                        extra={
                            "event_id": event.id,
                            "collection_id": event.collection_id,
                            "item_id": event.item_id,
                            "dispatch_attempts": event.dispatch_attempts,
                        },
                    )
                continue
            # Staged gate (spec §7.1): a staged item is visible but not
            # deliverable — route it to finalize instead of delivery. The
            # parse is strict; an href a mint could never have produced
            # raises InvalidKeySegment and takes the poison-drain (retrying
            # cannot fix a malformed href).
            staged_href = _first_staged_href(item)
            if staged_href is not None:
                parts = parse_staged_href(staged_href)
                finalizes.append(
                    {
                        "upload_id": parts.upload_id,
                        "collection_id": event.collection_id,
                        "item_id": event.item_id,
                        "event_op": event.op,
                    }
                )
                logger.info(
                    "dispatch: staged item routed to finalize",
                    extra={
                        "event_id": event.id,
                        "collection_id": event.collection_id,
                        "item_id": event.item_id,
                        "upload_id": parts.upload_id,
                        "event_op": event.op,
                    },
                )
                continue
            if event.collection_id not in assoc_cache:
                assoc_cache[event.collection_id] = await repo.list_deliver_associations(
                    event.collection_id
                )
            occurred = event.occurred_at.isoformat() if event.occurred_at else None
            item_matches = match_item(item, assoc_cache[event.collection_id])
            for m in item_matches:
                batch = batches.setdefault(
                    m.association_id,
                    {"association_id": m.association_id, "items": []},
                )
                batch["items"].append(
                    {
                        "item_id": m.item_id,
                        "asset_keys": list(m.asset_keys),
                        "item_created_at": occurred,
                    }
                )
            matches.extend(item_matches)
        except Exception:
            logger.exception(
                "dispatch: event dead-lettered (drained without dispatching)",
                extra={
                    "event_id": event.id,
                    "collection_id": event.collection_id,
                    "item_id": event.item_id,
                },
            )

    # Enqueue-before-drain (both queues): a raise here leaves the whole claim
    # unprocessed for a redrive — at-least-once, with finalize's ledger claim
    # and delivery's delivery_log absorbing the duplicates.
    if finalizes:
        await enqueue_finalize(finalizes)
    if batches:
        await enqueue(list(batches.values()))
    await repo.mark_processed([e.id for e in events if e.id not in deferred])
    if deferred:
        await repo.release_for_retry(sorted(deferred), VISIBILITY_RETRY_SECONDS)
    return DispatchResult(
        claimed=len(events), matches=matches, finalizes=len(finalizes)
    )


async def dispatch_until_empty(
    repo: DispatchRepo,
    enqueue: EnqueueDeliveries,
    *,
    enqueue_finalize: EnqueueFinalizes,
    mark_delete_gc: MarkDeleteGc,
    batch_size: int = 100,
    max_batches: int = 1000,
) -> int:
    """Run dispatch_once until a claim comes back empty (or the safety cap);
    returns the total match count. A count, not the accumulated Match lists —
    the wake paths only log it, and holding up to max_batches * batch_size
    Match objects across a large drain would be pure ballast.

    Deferred I-38 events don't spin this loop: their cool-off keeps them out of
    the claim window, so the terminating empty claim still happens.
    """
    total = 0
    for _ in range(max_batches):
        result = await dispatch_once(
            repo,
            enqueue,
            enqueue_finalize=enqueue_finalize,
            mark_delete_gc=mark_delete_gc,
            batch_size=batch_size,
        )
        if not result.claimed:
            break
        total += len(result.matches)
    else:
        logger.warning(
            "dispatch hit its per-wake batch cap; more may remain",
            extra={"max_batches": max_batches, "batch_size": batch_size},
        )
    return total
