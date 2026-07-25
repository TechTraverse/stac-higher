"""In-memory DispatchRepo for dispatcher-loop unit tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.repo import DispatchRepo, ItemEvent


@dataclass
class FakeDispatchRepo(DispatchRepo):
    events: list[ItemEvent] = field(default_factory=list)
    associations: dict[str, list[DeliverAssociation]] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    processed: list[int] = field(default_factory=list)
    #: number of list_deliver_associations calls (asserts the per-collection memo)
    assoc_calls: int = 0

    #: ids stamped by a prior claim (mirrors item_events.claimed_at, I-40).
    claimed: set[int] = field(default_factory=set)
    #: ids in their visibility cool-off (mirrors next_dispatch_at, I-38) —
    #: excluded from claims until a test calls make_due().
    deferred: set[int] = field(default_factory=set)
    #: (event_ids, retry_delay_seconds) per release_for_retry call.
    released: list[tuple[list[int], int]] = field(default_factory=list)

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        pending = [
            e
            for e in self.events
            if e.id not in self.processed
            and e.id not in self.claimed
            and e.id not in self.deferred
        ]
        batch = pending[:limit]
        self.claimed.update(e.id for e in batch)
        return batch

    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        self.processed.extend(event_ids)

    async def release_for_retry(
        self, event_ids: Sequence[int], retry_delay_seconds: int
    ) -> None:
        ids = list(event_ids)
        self.released.append((ids, retry_delay_seconds))
        for i, event in enumerate(self.events):
            if event.id in ids:
                self.events[i] = replace(
                    event, dispatch_attempts=event.dispatch_attempts + 1
                )
        self.claimed.difference_update(ids)
        self.deferred.update(ids)

    def make_due(self, event_ids: Sequence[int]) -> None:
        """Test helper: expire the cool-off (next_dispatch_at passes)."""
        self.deferred.difference_update(event_ids)

    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        self.assoc_calls += 1
        return self.associations.get(collection_id, [])

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))
