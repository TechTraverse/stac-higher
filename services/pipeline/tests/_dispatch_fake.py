"""In-memory DispatchRepo for dispatcher-loop unit tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

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

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        pending = [e for e in self.events if e.id not in self.processed]
        return pending[:limit]

    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        self.processed.extend(event_ids)

    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        self.assoc_calls += 1
        return self.associations.get(collection_id, [])

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))
