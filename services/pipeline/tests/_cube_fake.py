"""In-memory CubeRepo for dispatcher and cube-job unit tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from pipeline.cubes.repo import CubeRepo, CubeSinkRef, LedgerEntry


@dataclass
class FakeSink:
    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool = True


@dataclass
class FakeLedgerRow:
    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str
    reason: str | None
    attempts: int = 0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


@dataclass
class FakeCubeRepo(CubeRepo):
    sinks: list[FakeSink] = field(default_factory=list)
    #: (cube_sink_id, item_id) -> row, mirroring UNIQUE (cube_sink_id, item_id)
    ledger: dict[tuple[str, str], FakeLedgerRow] = field(default_factory=dict)
    #: enabled_sinks_for_source calls (asserts the per-batch cache)
    sink_calls: int = 0
    #: raise from enabled_sinks_for_source / record_appends when set
    lookup_error: Exception | None = None
    record_error: Exception | None = None

    async def enabled_sinks_for_source(self, collection_id: str) -> list[CubeSinkRef]:
        self.sink_calls += 1
        if self.lookup_error is not None:
            raise self.lookup_error
        return [
            CubeSinkRef(id=s.id, cube_collection_id=s.cube_collection_id)
            for s in self.sinks
            if s.source_collection_id == collection_id and s.enabled
        ]

    async def record_appends(self, entries: Sequence[LedgerEntry]) -> int:
        if self.record_error is not None:
            raise self.record_error
        live = {s.id for s in self.sinks if s.enabled}
        inserted = 0
        for e in entries:
            key = (e.cube_sink_id, e.item_id)
            if e.cube_sink_id not in live or key in self.ledger:
                continue
            self.ledger[key] = FakeLedgerRow(
                e.cube_sink_id, e.item_id, e.item_datetime, e.status, e.reason
            )
            inserted += 1
        return inserted

    async def sinks_with_stale_pending(self, older_than_seconds: int) -> list[str]:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=older_than_seconds)
        live = {s.id for s in self.sinks if s.enabled}
        return sorted(
            {
                r.cube_sink_id
                for r in self.ledger.values()
                if r.status == "pending" and r.created_at < cutoff and r.cube_sink_id in live
            }
        )

    async def fail_pending(self, cube_sink_id: str, reason: str) -> int:
        changed = 0
        for r in self.ledger.values():
            if r.cube_sink_id == cube_sink_id and r.status == "pending":
                r.status, r.reason = "failed", reason
                r.attempts += 1
                changed += 1
        return changed

    def backdate(self, cube_sink_id: str, item_id: str, seconds: int) -> None:
        """Test helper: age one ledger row's created_at."""
        row = self.ledger[(cube_sink_id, item_id)]
        row.created_at -= dt.timedelta(seconds=seconds)

    def rows(self, cube_sink_id: str) -> list[FakeLedgerRow]:
        return sorted(
            (r for r in self.ledger.values() if r.cube_sink_id == cube_sink_id),
            key=lambda r: r.item_id,
        )
