"""Poll-driven dispatch orchestration (Slice A skeleton — logs, no transfer).

dispatch_once claims one batch of pending outbox rows, matches each non-delete
item against its collection's delivery associations, LOGS the matched pairs, and
marks the whole claimed batch processed so the outbox drains. Slice B replaces
the log with delivery-job fan-out; Slice C swaps the poll for a LISTEN wake.

Finalize-gating seam (ROADMAP §6.4, deferred to Phase 7): once externally-
writable collections exist, insert events for items still in staging must be
deferred until finalize marks them ready. No such collections exist yet, so the
skeleton dispatches every insert; this comment marks where that gate lands.
"""

from __future__ import annotations

import logging

from pipeline.delivery.matcher import Match, match_item
from pipeline.dispatcher.repo import DispatchRepo

logger = logging.getLogger(__name__)


async def dispatch_once(repo: DispatchRepo, *, batch_size: int = 100) -> list[Match]:
    events = await repo.claim_pending_events(batch_size)
    if not events:
        return []

    matches: list[Match] = []
    for event in events:
        # Deletions never propagate to destinations (ROADMAP §6.4) — drain only.
        if event.op == "delete":
            continue
        item = await repo.get_item(event.collection_id, event.item_id)
        if item is None:
            # Race: the outbox row beat the item's visibility. Best-effort skip;
            # a subsequent update event (or Slice C's revisit) re-drives it.
            logger.warning(
                "dispatch: item not found for event",
                extra={"collection_id": event.collection_id, "item_id": event.item_id},
            )
            continue
        associations = await repo.list_deliver_associations(event.collection_id)
        item_matches = match_item(item, associations)
        for m in item_matches:
            logger.info(
                "dispatch match (skeleton — no transfer yet)",
                extra={
                    "association_id": m.association_id,
                    "item_id": m.item_id,
                    "asset_keys": list(m.asset_keys),
                    "op": event.op,
                },
            )
        matches.extend(item_matches)

    await repo.mark_processed([e.id for e in events])
    return matches
