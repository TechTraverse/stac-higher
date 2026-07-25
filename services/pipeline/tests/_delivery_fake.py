"""In-memory DeliveryRepo for worker + deliver-job unit tests."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.repo import (
    DeliverTarget,
    DeliveryRepo,
    DeliveryRow,
    ReferenceSource,
    RetryRow,
)


@dataclass
class FakeDeliveryRepo(DeliveryRepo):
    targets: dict[str, DeliverTarget] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    reference_sources: dict[str, list[ReferenceSource]] = field(default_factory=dict)
    _seq: int = 0

    async def load_target(self, association_id: str) -> DeliverTarget | None:
        return self.targets.get(association_id)

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))

    async def get_row(self, association_id: str, item_id: str) -> DeliveryRow | None:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                return DeliveryRow(
                    id=rid,
                    status=rec["status"],
                    attempts=rec["attempts"],
                    delivered_assets=dict(rec.get("delivered_assets") or {}),
                )
        return None

    async def load_reference_sources(self, item_id: str) -> list[ReferenceSource]:
        return list(self.reference_sources.get(item_id, []))

    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        for rid, rec in self.rows.items():
            if (rec["association_id"], rec["item_id"]) == (association_id, item_id):
                # I-44: a NEW event on a settled row starts a fresh attempt
                # cycle; a sweep-requeued pending/delivering row keeps its
                # count so max_attempts dead-lettering converges (B-iii).
                if rec["status"] not in ("pending", "delivering"):
                    rec["attempts"] = 0
                rec.update(
                    status="pending",
                    item_created_at=item_created_at,
                    next_attempt_at=None,
                )
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
            "delivered_assets": {},
            "next_attempt_at": None,
        }
        return rid

    async def mark_delivering(self, row_id: str) -> int:
        rec = self.rows[row_id]
        rec["status"] = "delivering"
        rec["attempts"] += 1
        return rec["attempts"]

    async def mark_delivered(
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        rec = self.rows[row_id]
        rec.update(
            status="delivered",
            bytes=byte_count,
            error=None,
            delivered_assets=dict(delivered_assets or {}),
        )

    async def mark_failed(
        self,
        row_id: str,
        error: str,
        *,
        delivered_assets: dict[str, Any] | None = None,
        next_attempt_at: dt.datetime | None = None,
        dead: bool = False,
    ) -> None:
        rec = self.rows[row_id]
        rec.update(
            status="dead" if dead else "failed",
            error=error,
            delivered_assets=dict(delivered_assets or {}),
            next_attempt_at=next_attempt_at,
        )

    async def list_due_retries(self, limit: int) -> list[RetryRow]:
        now = dt.datetime.now(dt.UTC)
        due = [
            RetryRow(
                id=rid,
                association_id=rec["association_id"],
                item_id=rec["item_id"],
                attempts=rec["attempts"],
                item_created_at=rec.get("item_created_at"),
            )
            for rid, rec in self.rows.items()
            if rec["status"] == "failed"
            and rec.get("next_attempt_at") is not None
            and rec["next_attempt_at"] <= now
        ]
        return due[:limit]

    async def requeue_for_retry(self, row_ids: list[str]) -> None:
        for rid in row_ids:
            self.rows[rid].update(status="pending", next_attempt_at=None)
