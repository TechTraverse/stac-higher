"""In-memory DeliveryRepo for worker + deliver-job unit tests."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pipeline.delivery.repo import (
    DeliverTarget,
    DeliveryRepo,
    DeliveryRow,
    PreRecord,
    ReferenceSource,
    RetryRow,
)
from pipeline.flow.stats import apply_count_delta, apply_delivery_event


@dataclass
class FakeDeliveryRepo(DeliveryRepo):
    targets: dict[str, DeliverTarget] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    reference_sources: dict[str, list[ReferenceSource]] = field(default_factory=dict)
    #: per-association flow_stats rollup, maintained with the SAME pure math
    #: PgDeliveryRepo applies in-transaction (M2-A).
    flow_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    def _bump_stats(
        self,
        association_id: str,
        transitions: list[tuple[str | None, str | None]],
        *,
        bytes_added: int = 0,
        latency_seconds: float | None = None,
        activity: bool = False,
        error: bool = False,
    ) -> None:
        stats = self.flow_stats.get(association_id, {})
        for prev, new in transitions:
            if prev != new:
                stats = apply_count_delta(stats, prev, new)
        stats = apply_delivery_event(
            stats,
            bytes_added=bytes_added,
            latency_seconds=latency_seconds,
            activity=activity,
            error=error,
        )
        self.flow_stats[association_id] = stats

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
                prev = rec["status"]
                rec.update(
                    status="pending",
                    item_created_at=item_created_at,
                    next_attempt_at=None,
                    updated_at=dt.datetime.now(dt.UTC),
                )
                self._bump_stats(association_id, [(prev, "pending")])
                return rid
        rid = self._insert(association_id, item_id, item_created_at)
        self._bump_stats(association_id, [(None, "pending")])
        return rid

    def _insert(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
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
            "updated_at": dt.datetime.now(dt.UTC),
        }
        return rid

    def _touch(self, row_id: str) -> None:
        self.rows[row_id]["updated_at"] = dt.datetime.now(dt.UTC)

    async def mark_delivering(self, row_id: str) -> int:
        rec = self.rows[row_id]
        prev = rec["status"]
        rec["status"] = "delivering"
        rec["attempts"] += 1
        self._touch(row_id)
        self._bump_stats(rec["association_id"], [(prev, "delivering")])
        return rec["attempts"]

    async def mark_delivered(
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        rec = self.rows[row_id]
        prev = rec["status"]
        rec.update(
            status="delivered",
            bytes=byte_count,
            error=None,
            delivered_assets=dict(delivered_assets or {}),
            updated_at=dt.datetime.now(dt.UTC),
        )
        latency: float | None = None
        if rec.get("item_created_at"):
            created = dt.datetime.fromisoformat(rec["item_created_at"])
            latency = (dt.datetime.now(dt.UTC) - created).total_seconds()
        self._bump_stats(
            rec["association_id"],
            [(prev, "delivered")],
            bytes_added=byte_count,
            latency_seconds=latency,
            activity=True,
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
        prev = rec["status"]
        new_status = "dead" if dead else "failed"
        rec.update(
            status=new_status,
            error=error,
            delivered_assets=dict(delivered_assets or {}),
            next_attempt_at=next_attempt_at,
            updated_at=dt.datetime.now(dt.UTC),
        )
        self._bump_stats(rec["association_id"], [(prev, new_status)], error=True)

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
            prev = self.rows[rid]["status"]
            self.rows[rid].update(status="pending", next_attempt_at=None)
            self._touch(rid)
            self._bump_stats(self.rows[rid]["association_id"], [(prev, "pending")])

    # -- M2-0: pre-record + stall recovery ---------------------------------

    async def pre_record(
        self, association_id: str, items: list[tuple[str, str | None]]
    ) -> list[PreRecord]:
        out: list[PreRecord] = []
        for item_id, item_created_at in items:
            existing = next(
                (
                    rid
                    for rid, rec in self.rows.items()
                    if (rec["association_id"], rec["item_id"]) == (association_id, item_id)
                ),
                None,
            )
            if existing is not None:
                # INSERT-only: an existing row keeps its status/attempts, so the
                # on_update + overwrite gates still read the true prior state.
                rec = self.rows[existing]
                out.append(
                    PreRecord(
                        id=existing,
                        item_id=item_id,
                        created=False,
                        attempts=rec["attempts"],
                        delivered_assets=dict(rec.get("delivered_assets") or {}),
                    )
                )
                continue
            rid = self._insert(association_id, item_id, item_created_at)
            self._bump_stats(association_id, [(None, "pending")])
            out.append(PreRecord(id=rid, item_id=item_id, created=True))
        return out

    async def discard_pre_records(self, row_ids: list[str]) -> None:
        for rid in row_ids:
            rec = self.rows.get(rid)
            # Guarded like the SQL: only untouched placeholders.
            if rec is not None and rec["status"] == "pending" and rec["attempts"] == 0:
                self._bump_stats(rec["association_id"], [("pending", None)])
                del self.rows[rid]

    async def sweep_stalled_deliveries(
        self, older_than_seconds: int, limit: int
    ) -> int:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=older_than_seconds)
        now = dt.datetime.now(dt.UTC)
        swept = 0
        for rec in self.rows.values():
            if swept >= limit:
                break
            if rec["status"] not in ("pending", "delivering"):
                continue
            if rec.get("updated_at", now) >= cutoff:
                continue
            prev = rec["status"]
            rec.update(
                status="failed",
                error=f"stalled in {prev}; recovered by the delivery stall sweep",
                next_attempt_at=now,
                updated_at=now,
            )
            self._bump_stats(rec["association_id"], [(prev, "failed")], error=True)
            swept += 1
        return swept
