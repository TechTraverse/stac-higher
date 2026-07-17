"""In-memory IngestRepo + a fake StorageAdapter/S3 client for stage unit tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.connections.adapters.base import FileEntry
from pipeline.ingest.repo import (
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@dataclass
class FakeIngestRepo(IngestRepo):
    """Deterministic ledger over an in-memory dict. ``now`` stamps new rows."""

    associations: list[IngestAssociation] = field(default_factory=list)
    rows: dict[str, LedgerEntry] = field(default_factory=dict)
    now: dt.datetime = EPOCH
    _next_id: int = 1
    set_ledger_status_many_calls: int = 0

    async def list_enabled_ingest_associations(self) -> list[IngestAssociation]:
        return [a for a in self.associations if a.enabled]

    async def get_association(self, association_id: str) -> IngestAssociation | None:
        for a in self.associations:
            if a.id == association_id and a.enabled:
                return a
        return None

    def _versions(self, association_id: str, source_path: str) -> list[LedgerEntry]:
        return [
            r
            for r in self.rows.values()
            if r.association_id == association_id and r.source_path == source_path
        ]

    async def get_latest_ledger(
        self, association_id: str, source_path: str
    ) -> LedgerEntry | None:
        versions = self._versions(association_id, source_path)
        return max(versions, key=lambda r: r.version) if versions else None

    async def list_ledger_by_status(
        self, association_id: str, status: str
    ) -> list[LedgerEntry]:
        latest: dict[str, LedgerEntry] = {}
        for r in self.rows.values():
            if r.association_id != association_id:
                continue
            cur = latest.get(r.source_path)
            if cur is None or r.version > cur.version:
                latest[r.source_path] = r
        return sorted(
            (r for r in latest.values() if r.status == status),
            key=lambda r: (r.created_at or EPOCH, r.source_path),
        )

    async def insert_ledger_version(
        self,
        association_id: str,
        source_path: str,
        *,
        version: int,
        status: str,
        size: int | None,
        fingerprint: str | None,
        item_id: str | None = None,
    ) -> str:
        entry_id = str(self._next_id)
        self._next_id += 1
        self.rows[entry_id] = LedgerEntry(
            id=entry_id,
            association_id=association_id,
            source_path=source_path,
            version=version,
            size=size,
            fingerprint=fingerprint,
            checksum=None,
            status=status,
            item_id=item_id,
            created_at=self.now,
            updated_at=self.now,
        )
        return entry_id

    async def set_ledger_fields(self, entry_id: str, **fields: Any) -> None:
        row = self.rows[entry_id]
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = self.now

    async def set_ledger_status_many(
        self, entry_ids: Sequence[str], *, status: str, item_id: str | None = None
    ) -> None:
        self.set_ledger_status_many_calls += 1
        # A simple loop is fine in the fake — the invariant under test is that
        # the production Pg path is a single statement; the fake just needs to
        # update all rows as one logical operation.
        for entry_id in entry_ids:
            row = self.rows[entry_id]
            row.status = status
            row.item_id = item_id
            row.updated_at = self.now


@dataclass
class FakeAdapter:
    """A StorageAdapter stand-in: ``list`` returns canned entries, ``get`` bytes."""

    entries: list[FileEntry] = field(default_factory=list)
    blobs: dict[str, bytes] = field(default_factory=dict)
    protocol: str = "s3"
    list_calls: list[str] = field(default_factory=list)
    get_calls: list[str] = field(default_factory=list)

    async def list(self, prefix: str = "") -> list[FileEntry]:
        self.list_calls.append(prefix)
        return list(self.entries)

    async def get(self, path: str) -> bytes:
        self.get_calls.append(path)
        return self.blobs[path]

    async def put(self, path: str, data: bytes) -> None:  # pragma: no cover - unused
        self.blobs[path] = data

    async def delete(self, path: str) -> None:  # pragma: no cover - unused
        self.blobs.pop(path, None)

    async def test(self):  # pragma: no cover - unused
        return {"ok": True}


@dataclass
class FakeS3:
    """Captures put_object calls the FETCH stage makes into platform storage."""

    puts: list[dict[str, Any]] = field(default_factory=list)

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}
