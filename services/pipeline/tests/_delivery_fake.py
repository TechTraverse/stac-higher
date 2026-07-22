"""In-memory DeliveryRepo for worker + deliver-job unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.repo import DeliverTarget, DeliveryRepo


@dataclass
class FakeDeliveryRepo(DeliveryRepo):
    targets: dict[str, DeliverTarget] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    async def load_target(self, association_id: str) -> DeliverTarget | None:
        return self.targets.get(association_id)

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))

    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                rec.update(status="pending", item_created_at=item_created_at)
                return rid
        self._seq += 1
        rid = f"row{self._seq}"
        self.rows[rid] = {
            "association_id": association_id,
            "item_id": item_id,
            "item_created_at": item_created_at,
            "status": "pending",
            "attempts": 0,
            "bytes": None,
            "error": None,
        }
        return rid

    async def mark_delivering(self, row_id: str) -> None:
        rec = self.rows[row_id]
        rec["status"] = "delivering"
        rec["attempts"] += 1

    async def mark_delivered(self, row_id: str, byte_count: int) -> None:
        rec = self.rows[row_id]
        rec.update(status="delivered", bytes=byte_count, error=None)

    async def mark_failed(self, row_id: str, error: str) -> None:
        rec = self.rows[row_id]
        rec.update(status="failed", error=error)
