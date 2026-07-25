"""Dispatch orchestration (Slice B-i: grouping + enqueue; Slice C: NOTIFY wake).

dispatch_once claims one batch of pending outbox rows, matches each non-delete
item against its collection's delivery associations, groups the matches into
one delivery batch per association, hands them to ``enqueue``, THEN marks the
claimed batch processed so the outbox drains. The primary wake path is the
LISTEN loop (``dispatcher.listener``); the minute poll stays as fallback —
both call this via ``dispatch_until_empty``.

Finalize-gating seam (ROADMAP §6.4, deferred to Phase 7): once externally-
writable collections exist, insert events for items still in staging must be
deferred until finalize marks them ready. No such collections exist yet, so the
skeleton dispatches every insert; this comment marks where that gate lands.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation, Match, match_item
from pipeline.dispatcher.repo import DispatchRepo

logger = logging.getLogger(__name__)

#: enqueue callback: given the grouped delivery batches, hand them to the queue.
EnqueueDeliveries = Callable[[list[dict[str, Any]]], Awaitable[None]]

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


async def dispatch_once(
    repo: DispatchRepo, enqueue: EnqueueDeliveries, *, batch_size: int = 100
) -> DispatchResult:
    """Claim a batch of outbox rows, match each non-delete item against its
    collection's delivery associations, group matches into per-association
    delivery batches, hand them to ``enqueue``, THEN drain the outbox.

    Enqueue-before-drain gives at-least-once delivery: if ``enqueue`` raises, the
    outbox rows stay pending and a later tick re-drives them.
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
    # I-38: events released for a later visibility retry instead of drained.
    deferred: set[int] = set()

    for event in events:
        # Per-event isolation (ISSUES I-39): one poison event must never abort
        # the batch — an uncaught raise here would leave the whole claim
        # unprocessed and busy-loop on the offending row every tick. The
        # poison event is logged loudly and drained with the batch (its
        # delivery is skipped — dead-lettered by drain).
        try:
            # Deletions never propagate to destinations (ROADMAP §6.4) — drain only.
            if event.op == "delete":
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

    if batches:
        await enqueue(list(batches.values()))
    await repo.mark_processed([e.id for e in events if e.id not in deferred])
    if deferred:
        await repo.release_for_retry(sorted(deferred), VISIBILITY_RETRY_SECONDS)
    return DispatchResult(claimed=len(events), matches=matches)


async def dispatch_until_empty(
    repo: DispatchRepo,
    enqueue: EnqueueDeliveries,
    *,
    batch_size: int = 100,
    max_batches: int = 1000,
) -> list[Match]:
    """Run dispatch_once until a claim comes back empty (or the safety cap).

    Deferred I-38 events don't spin this loop: their cool-off keeps them out of
    the claim window, so the terminating empty claim still happens.
    """
    matches: list[Match] = []
    for _ in range(max_batches):
        result = await dispatch_once(repo, enqueue, batch_size=batch_size)
        if not result.claimed:
            break
        matches.extend(result.matches)
    else:
        logger.warning(
            "dispatch hit its per-wake batch cap; more may remain",
            extra={"max_batches": max_batches, "batch_size": batch_size},
        )
    return matches
