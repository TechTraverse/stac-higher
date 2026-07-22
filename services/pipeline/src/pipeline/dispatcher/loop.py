"""Poll-driven dispatch orchestration (Slice B-i: grouping + enqueue).

dispatch_once claims one batch of pending outbox rows, matches each non-delete
item against its collection's delivery associations, groups the matches into
one delivery batch per association, hands them to ``enqueue``, THEN marks the
whole claimed batch processed so the outbox drains. Slice C swaps the poll for
a LISTEN wake.

Finalize-gating seam (ROADMAP §6.4, deferred to Phase 7): once externally-
writable collections exist, insert events for items still in staging must be
deferred until finalize marks them ready. No such collections exist yet, so the
skeleton dispatches every insert; this comment marks where that gate lands.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation, Match, match_item
from pipeline.dispatcher.repo import DispatchRepo

logger = logging.getLogger(__name__)

#: enqueue callback: given the grouped delivery batches, hand them to the queue.
EnqueueDeliveries = Callable[[list[dict[str, Any]]], Awaitable[None]]


async def dispatch_once(
    repo: DispatchRepo, enqueue: EnqueueDeliveries, *, batch_size: int = 100
) -> list[Match]:
    """Claim a batch of outbox rows, match each non-delete item against its
    collection's delivery associations, group matches into per-association
    delivery batches, hand them to ``enqueue``, THEN drain the outbox.

    Enqueue-before-drain gives at-least-once delivery: if ``enqueue`` raises, the
    outbox rows stay pending and a later tick re-drives them.
    """
    events = await repo.claim_pending_events(batch_size)
    if not events:
        return []

    # Cache deliver associations per collection for this batch (a bulk upsert of
    # N items into one collection shares collection_id → one lookup, not N).
    assoc_cache: dict[str, list[DeliverAssociation]] = {}
    matches: list[Match] = []
    # association_id → batch payload (preserves association + item order).
    batches: dict[str, dict[str, Any]] = {}

    for event in events:
        # Deletions never propagate to destinations (ROADMAP §6.4) — drain only.
        if event.op == "delete":
            continue
        item = await repo.get_item(event.collection_id, event.item_id)
        if item is None:
            # Race: the outbox row beat the item's visibility. Best-effort skip;
            # a subsequent event re-drives it.
            logger.warning(
                "dispatch: item not found for event",
                extra={"collection_id": event.collection_id, "item_id": event.item_id},
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

    if batches:
        await enqueue(list(batches.values()))
    await repo.mark_processed([e.id for e in events])
    return matches
