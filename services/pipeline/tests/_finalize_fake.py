"""Shared fakes for the finalize tests (mirrors the _*_fake.py idiom)."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any

from pipeline.finalize.repo import FinalizeRepo, StagedUploadRow, StaleClaim
from pipeline.finalize.store import ObjectStat
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter

UTC = dt.UTC


def valid_item(
    item_id: str = "S2A_001",
    collection: str = "sentinel-pushed",
    assets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "stac_extensions": [],
        "id": item_id,
        "collection": collection,
        "geometry": None,
        "properties": {"datetime": "2026-08-01T00:00:00Z"},
        "assets": assets or {},
        "links": [],
    }


@dataclass
class Session:
    """One staged_uploads row, mutated the way the repo methods would."""

    id: str
    collection_id: str
    status: str = "pending"
    item_id: str | None = None
    prior_item: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(UTC))
    claimed_at: dt.datetime | None = None
    finalized_at: dt.datetime | None = None


@dataclass
class FakeFinalizeRepo(FinalizeRepo):
    sessions: dict[str, Session] = field(default_factory=dict)
    items: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    archived: set[str] = field(default_factory=set)
    open_gc_marks: set[str] = field(default_factory=set)
    grace_days: dict[str, int] = field(default_factory=dict)
    #: (collection_id, item_id) pairs with item_events history predating any
    #: session — the I-46 guard's evidence of a pre-existing item.
    predating_items: set[tuple[str, str]] = field(default_factory=set)
    #: calls recorded for assertions
    deleted_items: list[tuple[str, str]] = field(default_factory=list)
    marks: list[tuple[str, str, str | None, str, int]] = field(default_factory=list)
    released: list[str] = field(default_factory=list)

    # -- ledger -----------------------------------------------------------

    async def get_session(self, upload_id: str) -> StagedUploadRow | None:
        s = self.sessions.get(upload_id)
        if s is None:
            return None
        return StagedUploadRow(
            id=s.id,
            collection_id=s.collection_id,
            item_id=s.item_id,
            status=s.status,
            prior_item=s.prior_item,
            created_at=s.created_at,
        )

    async def item_predates(self, collection_id: str, item_id: str, before) -> bool:
        return (collection_id, item_id) in self.predating_items

    async def claim(self, upload_id: str, item_id: str) -> bool:
        s = self.sessions.get(upload_id)
        if s is None or s.status != "pending":
            return False
        if s.item_id is not None and s.item_id != item_id:
            return False
        s.status = "finalizing"
        s.item_id = item_id
        s.claimed_at = dt.datetime.now(UTC)
        return True

    async def release_claim(self, upload_id: str) -> None:
        s = self.sessions[upload_id]
        if s.status == "finalizing":
            s.status = "pending"
            s.claimed_at = None
        self.released.append(upload_id)

    async def record_finalized(self, upload_id: str, result: dict[str, Any]) -> None:
        s = self.sessions[upload_id]
        if s.status != "finalizing":
            return
        s.status = "finalized"
        s.result = result
        s.error = None
        s.finalized_at = dt.datetime.now(UTC)

    async def record_rejected(
        self, upload_id: str, result: dict[str, Any], error: str | None
    ) -> None:
        s = self.sessions[upload_id]
        if s.status != "finalizing":
            return
        s.status = "rejected"
        s.result = result
        s.error = error
        s.finalized_at = dt.datetime.now(UTC)

    # -- catalog ----------------------------------------------------------

    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        return self.items.get((collection_id, item_id))

    async def delete_item(self, item_id: str, collection_id: str) -> bool:
        self.deleted_items.append((collection_id, item_id))
        self.items.pop((collection_id, item_id), None)
        return True

    # -- GC ----------------------------------------------------------------

    async def mark_asset_prefix(
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        if prefix in self.open_gc_marks:
            return False
        self.open_gc_marks.add(prefix)
        self.marks.append((prefix, collection_id, item_id, reason, grace_days))
        return True

    async def gc_grace_days(self, collection_id: str) -> int:
        return self.grace_days.get(collection_id, 30)

    # -- preflight ---------------------------------------------------------

    async def collection_archived(self, collection_id: str) -> bool:
        return collection_id in self.archived

    async def has_open_gc_mark(self, prefix: str) -> bool:
        return any(prefix.startswith(k) for k in self.open_gc_marks)

    # -- sweep -------------------------------------------------------------

    async def expire_pending(self, ttl_seconds: int) -> int:
        cutoff = dt.datetime.now(UTC) - dt.timedelta(seconds=ttl_seconds)
        expired = 0
        for s in self.sessions.values():
            if s.status == "pending" and s.created_at < cutoff:
                s.status = "expired"
                s.finalized_at = dt.datetime.now(UTC)
                expired += 1
        return expired

    async def list_stale_finalizing(self, stale_seconds: int, limit: int) -> list[StaleClaim]:
        cutoff = dt.datetime.now(UTC) - dt.timedelta(seconds=stale_seconds)
        stale = [
            StaleClaim(id=s.id, collection_id=s.collection_id, item_id=s.item_id)
            for s in self.sessions.values()
            if s.status == "finalizing" and s.claimed_at and s.claimed_at < cutoff
        ]
        return stale[:limit]

    async def requeue_stale(self, upload_ids: list[str]) -> None:
        for upload_id in upload_ids:
            s = self.sessions[upload_id]
            if s.status == "finalizing":
                s.status = "pending"
                s.claimed_at = None

    async def list_active_upload_ids(self, ttl_seconds: int) -> set[str]:
        cutoff = dt.datetime.now(UTC) - dt.timedelta(seconds=ttl_seconds)
        return {
            s.id
            for s in self.sessions.values()
            if s.status in ("pending", "finalizing")
            and (s.created_at >= cutoff or (s.claimed_at and s.claimed_at >= cutoff))
        }


@dataclass
class FakeObjectStore:
    """In-memory ObjectStore: keys → bytes, ETag = md5 like real S3."""

    objects: dict[str, bytes] = field(default_factory=dict)
    copied: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: keys whose canonical copy should come out corrupted (verify-mismatch)
    corrupt_on_copy: set[str] = field(default_factory=set)
    #: raise on delete of these keys (non-fatal-delete path)
    fail_delete: set[str] = field(default_factory=set)
    #: per-key ETag overrides — a multipart upload's ``<md5>-<n>`` ETag, or a
    #: deliberately divergent single-part one, which the content MD5 cannot
    #: express
    etags: dict[str, str] = field(default_factory=dict)

    def head(self, key: str) -> ObjectStat | None:
        data = self.objects.get(key)
        if data is None:
            return None
        etag = self.etags.get(key, hashlib.md5(data).hexdigest())
        return ObjectStat(etag=etag, size=len(data))

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]

    def copy(self, src_key: str, dest_key: str) -> None:
        data = self.objects[src_key]
        if src_key in self.corrupt_on_copy:
            data = data + b"\x00"
        self.objects[dest_key] = data
        self.copied.append((src_key, dest_key))

    def delete(self, key: str) -> None:
        if key in self.fail_delete:
            raise RuntimeError(f"delete failed: {key}")
        self.objects.pop(key, None)
        self.deleted.append(key)


@dataclass
class FakeWriter(PgstacWriter):
    upserted: list[dict[str, Any]] = field(default_factory=list)
    missing_collections: set[str] = field(default_factory=set)

    async def upsert_items(self, items) -> None:
        for item in items:
            if item.get("collection") in self.missing_collections:
                raise CollectionMissing(str(item.get("collection")))
        self.upserted.extend(dict(i) for i in items)

    async def get_collection_bbox(self, collection_id: str):
        return None
