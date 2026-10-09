"""In-memory CubeRepo for dispatcher, cube-job and append unit tests.

Mirrors PgCubeRepo (the DB-gated tests pin the SQL; test_cube_repo_fake.py
pins this). One-shot hooks simulate what the SQL cannot show in a unit test:
an app write landing just before ``record_commit``, and a crash inside
``record_commit`` or ``finish_rows``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.cubes.repo import (
    CubeRepo,
    CubeSink,
    CubeSinkRef,
    LedgerEntry,
    PendingRow,
    RowOutcome,
)


@dataclass
class FakeSink:
    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    source_prefixes: tuple[str, ...] = ()
    last_snapshot_id: str | None = None
    #: the app's updated_at::text; tests bump it to simulate a PUT/PATCH
    version: str = "v1"
    last_error: str | None = None
    last_appended_at: dt.datetime | None = None


@dataclass
class FakeLedgerRow:
    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str
    reason: str | None
    attempts: int = 0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    id: int = 0
    snapshot_id: str | None = None


@dataclass
class FakeCubeRepo(CubeRepo):
    sinks: list[FakeSink] = field(default_factory=list)
    #: (cube_sink_id, item_id) -> row, mirroring UNIQUE (cube_sink_id, item_id)
    ledger: dict[tuple[str, str], FakeLedgerRow] = field(default_factory=dict)
    #: enabled_sinks_for_source calls (asserts the per-batch cache)
    sink_calls: int = 0
    #: raise from enabled_sinks_for_source / record_appends when set
    lookup_error: Exception | None = None
    record_error_exc: Exception | None = None
    #: one-shot: runs just before record_commit applies (an app write racing it)
    before_record: Callable[[FakeCubeRepo], None] | None = None
    #: one-shot: raised by record_commit / finish_rows (a worker crash there)
    record_commit_error: Exception | None = None
    finish_error: Exception | None = None
    _next_id: int = 1

    def _sink(self, cube_sink_id: str) -> FakeSink | None:
        return next((s for s in self.sinks if s.id == cube_sink_id), None)

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
        if self.record_error_exc is not None:
            raise self.record_error_exc
        live = {s.id for s in self.sinks if s.enabled}
        inserted = 0
        for e in entries:
            key = (e.cube_sink_id, e.item_id)
            if e.cube_sink_id not in live or key in self.ledger:
                continue
            self.ledger[key] = FakeLedgerRow(
                e.cube_sink_id, e.item_id, e.item_datetime, e.status, e.reason, id=self._next_id
            )
            self._next_id += 1
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

    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:
        s = self._sink(cube_sink_id)
        if s is None:
            return None
        return CubeSink(
            id=s.id,
            source_collection_id=s.source_collection_id,
            cube_collection_id=s.cube_collection_id,
            enabled=s.enabled,
            config=dict(s.config),
            source_prefixes=tuple(s.source_prefixes),
            last_snapshot_id=s.last_snapshot_id,
            version=s.version,
        )

    async def take_pending(self, cube_sink_id: str, limit: int) -> list[PendingRow]:
        rows = sorted(
            (r for r in self.ledger.values()
             if r.cube_sink_id == cube_sink_id and r.status == "pending"),
            key=lambda r: (r.item_datetime, r.id),
        )[:limit]
        for r in rows:
            r.attempts += 1
        return [PendingRow(r.id, r.item_id, r.item_datetime, r.attempts) for r in rows]

    async def release_rows(self, cube_sink_id: str, row_ids: Sequence[int]) -> int:
        wanted = set(row_ids)
        changed = 0
        for r in self.ledger.values():
            if r.cube_sink_id == cube_sink_id and r.id in wanted and r.status == "pending":
                r.attempts = max(r.attempts - 1, 0)
                changed += 1
        return changed

    async def finish_rows(self, cube_sink_id: str, outcomes: Sequence[RowOutcome]) -> int:
        if self.finish_error is not None:
            exc, self.finish_error = self.finish_error, None
            raise exc
        by_id = {r.id: r for r in self.ledger.values() if r.cube_sink_id == cube_sink_id}
        changed = 0
        for o in outcomes:
            row = by_id.get(o.id)
            if row is None or row.status != "pending":
                continue
            row.status, row.reason, row.snapshot_id = o.status, o.reason, o.snapshot_id
            changed += 1
        return changed

    async def has_pending(self, cube_sink_id: str) -> bool:
        return any(
            r.cube_sink_id == cube_sink_id and r.status == "pending" for r in self.ledger.values()
        )

    async def record_commit(
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        if self.before_record is not None:
            hook, self.before_record = self.before_record, None
            hook(self)
        if self.record_commit_error is not None:
            exc, self.record_commit_error = self.record_commit_error, None
            raise exc
        s = self._sink(cube_sink_id)
        if s is None:
            return False
        if first_commit_version is not None and (
            s.version != first_commit_version or s.last_snapshot_id is not None
        ):
            return False
        s.last_snapshot_id = snapshot_id
        s.last_appended_at = appended_at
        s.source_prefixes = tuple(source_prefixes)
        s.last_error = None
        return True

    async def record_error(self, cube_sink_id: str, message: str) -> None:
        s = self._sink(cube_sink_id)
        if s is not None:
            s.last_error = message


    def backdate(self, cube_sink_id: str, item_id: str, seconds: int) -> None:
        """Test helper: age one ledger row's created_at."""
        row = self.ledger[(cube_sink_id, item_id)]
        row.created_at -= dt.timedelta(seconds=seconds)

    def rows(self, cube_sink_id: str) -> list[FakeLedgerRow]:
        return sorted(
            (r for r in self.ledger.values() if r.cube_sink_id == cube_sink_id),
            key=lambda r: r.item_id,
        )
