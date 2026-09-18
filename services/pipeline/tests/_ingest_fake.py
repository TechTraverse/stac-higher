"""In-memory IngestRepo + a fake StorageAdapter/S3 client for stage unit tests."""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.connections.adapters.base import FileEntry
from pipeline.connections.repo import ConnectionRow
from pipeline.flow.stats import apply_ingest_activity
from pipeline.ingest.repo import (
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def make_association(config: dict, *, collection_id: str = "col") -> IngestAssociation:
    """An enabled s3-source association with the standard test ids."""
    conn = ConnectionRow(
        id="c1", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    return IngestAssociation(
        id="assoc1",
        collection_id=collection_id,
        config=config,
        connection=conn,
    )


class FakeWriter(PgstacWriter):
    """Recording writer; opt into CollectionMissing or a collection bbox."""

    def __init__(self, raise_missing: bool = False, collection_bbox: list | None = None):
        self.items: list = []
        self.raise_missing = raise_missing
        self.collection_bbox = collection_bbox
        self.get_collection_bbox_calls: list[str] = []

    async def upsert_items(self, items):
        if self.raise_missing:
            raise CollectionMissing("Collection col is not present in the database")
        self.items.extend(items)

    async def get_collection_bbox(self, collection_id):
        self.get_collection_bbox_calls.append(collection_id)
        return self.collection_bbox


class RaisingWriter(PgstacWriter):
    """Writer whose upsert_items raises an unexpected (non-CollectionMissing)
    error, e.g. a transient DB connection failure."""

    def __init__(self):
        self.items: list = []

    async def upsert_items(self, items):
        raise RuntimeError("connection refused")

    async def get_collection_bbox(self, collection_id):
        return None


@dataclass
class FakeIngestRepo(IngestRepo):
    """Deterministic ledger over an in-memory dict. ``now`` stamps new rows."""

    associations: list[IngestAssociation] = field(default_factory=list)
    rows: dict[str, LedgerEntry] = field(default_factory=dict)
    now: dt.datetime = EPOCH
    _next_id: int = 1
    set_ledger_status_many_calls: int = 0
    #: per-row bounded-retry counters (mirrors ingest_files.retries, I-52).
    retries: dict[str, int] = field(default_factory=dict)
    #: per-association flow_stats rollup, same pure math as PgIngestRepo (M2-A).
    flow_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: extract_run_id -> status, so sweep_stuck_extracting can tell a live run
    #: from a vanished one (mirrors the process_runs join in PgIngestRepo).
    run_statuses: dict[str, str] = field(default_factory=dict)

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
        source_mtime: dt.datetime | None = None,
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
            source_mtime=source_mtime,
            created_at=self.now,
            updated_at=self.now,
        )
        return entry_id

    async def set_ledger_fields(self, entry_id: str, **fields: Any) -> None:
        row = self.rows[entry_id]
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = self.now

    async def transition_ledger(
        self, entry_id: str, *, expected_status: str, status: str, **fields: Any
    ) -> bool:
        row = self.rows[entry_id]
        if row.status != expected_status:
            return False
        row.status = status
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = self.now
        return True

    async def sweep_stuck_fetching(self, older_than_seconds: int) -> int:
        cutoff = self.now - dt.timedelta(seconds=older_than_seconds)
        count = 0
        for row in self.rows.values():
            if row.status == "fetching" and row.updated_at and row.updated_at < cutoff:
                row.status = "settled"
                row.updated_at = self.now
                count += 1
        return count

    async def sweep_failed_for_retry(
        self, max_retries: int, older_than_seconds: int
    ) -> int:
        cutoff = self.now - dt.timedelta(seconds=older_than_seconds)
        count = 0
        for entry_id, row in self.rows.items():
            if (
                row.status == "failed"
                and self.retries.get(entry_id, 0) < max_retries
                and row.updated_at
                and row.updated_at < cutoff
            ):
                row.status = "settled"
                row.reason = None
                self.retries[entry_id] = self.retries.get(entry_id, 0) + 1
                row.updated_at = self.now
                count += 1
        return count

    async def sweep_stuck_stored(
        self, max_retries: int, older_than_seconds: int
    ) -> tuple[int, int]:
        cutoff = self.now - dt.timedelta(seconds=older_than_seconds)
        resettled = dead_ended = 0
        for entry_id, row in self.rows.items():
            if row.status != "stored" or not row.updated_at or row.updated_at >= cutoff:
                continue
            if self.retries.get(entry_id, 0) < max_retries:
                row.status = "settled"
                self.retries[entry_id] = self.retries.get(entry_id, 0) + 1
                resettled += 1
            else:
                row.status = "failed"
                dead_ended += 1
            row.updated_at = self.now
        return resettled, dead_ended

    async def set_ledger_status_many(
        self,
        entry_ids: Sequence[str],
        *,
        status: str,
        item_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.set_ledger_status_many_calls += 1
        # A simple loop is fine in the fake — the invariant under test is that
        # the production Pg path is a single statement; the fake just needs to
        # update all rows as one logical operation.
        for entry_id in entry_ids:
            row = self.rows[entry_id]
            row.status = status
            row.item_id = item_id
            row.reason = reason
            row.updated_at = self.now

    async def get_ledger_entries(self, entry_ids: Sequence[str]) -> list[LedgerEntry]:
        return [self.rows[entry_id] for entry_id in entry_ids if entry_id in self.rows]

    async def set_extract_run(self, entry_ids: Sequence[str], run_id: str) -> None:
        for entry_id in entry_ids:
            self.rows[entry_id].extract_run_id = run_id

    async def fail_extracting_rows(
        self, entry_ids: Sequence[str], *, run_id: str, reason: str
    ) -> int:
        count = 0
        for entry_id in entry_ids:
            row = self.rows.get(entry_id)
            if row is None or row.status != "extracting" or row.extract_run_id != run_id:
                continue
            row.status = "failed"
            row.reason = reason
            row.item_id = None
            row.updated_at = self.now
            count += 1
        return count

    async def sweep_stuck_extracting(self, older_than_seconds: int) -> int:
        cutoff = self.now - dt.timedelta(seconds=older_than_seconds)
        count = 0
        for row in self.rows.values():
            if row.status != "extracting" or not row.updated_at or row.updated_at >= cutoff:
                continue
            run_status = self.run_statuses.get(row.extract_run_id) if row.extract_run_id else None
            if row.extract_run_id is None:
                row.reason = "extractor run was never queued"
            elif run_status not in ("queued", "running", "failed"):
                row.reason = f"extractor run {row.extract_run_id} is gone or closed"
            else:
                continue
            row.status = "failed"
            row.updated_at = self.now
            count += 1
        return count

    async def bump_flow_stats(
        self,
        association_id: str,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        self.flow_stats[association_id] = apply_ingest_activity(
            self.flow_stats.get(association_id, {}),
            files=files,
            bytes_added=bytes_added,
            items=items,
            failed=failed,
            latency_seconds=latency_seconds,
            now=self.now,
        )


@dataclass
class FakeAdapter:
    """A StorageAdapter stand-in: ``list`` returns canned entries, ``get`` bytes."""

    entries: list[FileEntry] = field(default_factory=list)
    blobs: dict[str, bytes] = field(default_factory=dict)
    protocol: str = "s3"
    list_calls: list[str] = field(default_factory=list)
    get_calls: list[str] = field(default_factory=list)
    #: (M3-C) the bucket a same-endpoint CopyObject could read from, or None
    #: to make copy_source() return None (adapter cannot be copied server-side).
    copy_bucket: str | None = None
    open_calls: list[str] = field(default_factory=list)

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

    def gdal_location(self, path, *, options=None):
        # Mirrors StorageAdapter's own default (M3-C, I-83): this stand-in
        # authenticates nothing, so SourceAdapterByteSource.locate() falls
        # back to the buffered `get()` — same as a real SFTP/FTP adapter.
        raise NotImplementedError("FakeAdapter: no VSI handler")

    @property
    def endpoint(self) -> str | None:
        return None

    def public_object_url(self, path: str) -> str:
        # Reference mode's stable source URL (the real S3Adapter derives it
        # from its endpoint + bucket); the fake just needs a deterministic one.
        return f"https://src.example/{path}"

    def copy_source(self, path: str) -> tuple[str, str] | None:
        return (self.copy_bucket, path) if self.copy_bucket else None

    async def open(self, path: str):
        self.open_calls.append(path)
        return io.BytesIO(self.blobs[path])


@dataclass
class FakeS3:
    """Captures the platform-storage calls the FETCH stage makes: buffered
    put_object (legacy), streamed upload_fileobj (M3-C) — both recorded as
    `puts` with the body bytes so assertions read the same — and server-side
    `copy` calls. `fail_copy=True` makes `copy` raise (fallback path)."""

    puts: list[dict[str, Any]] = field(default_factory=list)
    copies: list[dict[str, Any]] = field(default_factory=list)
    fail_copy: bool = False

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}

    def upload_fileobj(self, Fileobj: Any, Bucket: str, Key: str, **kwargs: Any) -> None:
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Fileobj.read(), **kwargs})

    def copy(self, CopySource: dict[str, str], Bucket: str, Key: str, **kwargs: Any) -> None:
        if self.fail_copy:
            raise RuntimeError("copy denied")
        self.copies.append({"CopySource": CopySource, "Bucket": Bucket, "Key": Key, **kwargs})
