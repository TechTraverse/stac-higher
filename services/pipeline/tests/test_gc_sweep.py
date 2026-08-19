"""M2-F retention & collection ticks (spec §5, ADR 0011)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pipeline.gc.repo import DueMark, GcRepo, RetentionCollection
from pipeline.gc.sweep import collect_tick, item_prefix, retention_tick
from pipeline.storage.platform import delete_prefix


@dataclass
class FakeGcRepo(GcRepo):
    collections: list[RetentionCollection] = field(default_factory=list)
    #: collection_id -> expired item ids the query would return
    expired: dict[str, list[str]] = field(default_factory=dict)
    marks: list[dict] = field(default_factory=list)
    deleted_items: list[tuple[str, str]] = field(default_factory=list)
    due: list[DueMark] = field(default_factory=list)
    collected: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    delete_ok: bool = True

    async def list_gc_collections(self) -> list[RetentionCollection]:
        return list(self.collections)

    async def list_expired_items(
        self, collection_id: str, retention_days: int | None, limit: int
    ) -> list[str]:
        return list(self.expired.get(collection_id, []))[:limit]

    async def mark_asset_prefix(
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        if any(m["prefix"] == prefix for m in self.marks):
            return False  # open-key unique index
        self.marks.append(
            {
                "prefix": prefix,
                "collection_id": collection_id,
                "item_id": item_id,
                "reason": reason,
                "grace_days": grace_days,
            }
        )
        return True

    async def delete_item(self, item_id: str, collection_id: str) -> bool:
        if not self.delete_ok:
            return False
        self.deleted_items.append((collection_id, item_id))
        return True

    async def list_due_marks(self, limit: int) -> list[DueMark]:
        return list(self.due)[:limit]

    async def record_collected(self, mark_id: str) -> None:
        self.collected.append(mark_id)

    async def record_collect_error(self, mark_id: str, error: str) -> None:
        self.errors.append((mark_id, error))


class TestRetentionTick:
    async def test_marks_before_delete_with_the_collection_grace(self):
        repo = FakeGcRepo(
            collections=[RetentionCollection("sentinel-2", 14, 7)],
            expired={"sentinel-2": ["item-1", "item-2"]},
        )
        result = await retention_tick(repo, batch_limit=100)
        assert result.expired_items == 2
        assert result.marks_created == 2
        assert repo.marks[0] == {
            "prefix": "assets/sentinel-2/item-1/",
            "collection_id": "sentinel-2",
            "item_id": "item-1",
            "reason": "retention",
            "grace_days": 7,
        }
        assert ("sentinel-2", "item-1") in repo.deleted_items

    async def test_archived_collection_expires_everything_as_archive(self):
        repo = FakeGcRepo(
            collections=[RetentionCollection("old-mission", None, 0, archived=True)],
            expired={"old-mission": ["a", "b", "c"]},
        )
        result = await retention_tick(repo, batch_limit=100)
        assert result.expired_items == 3
        assert {m["reason"] for m in repo.marks} == {"archive"}
        assert {m["grace_days"] for m in repo.marks} == {0}

    async def test_unconfigured_platform_deletes_nothing(self):
        repo = FakeGcRepo()
        result = await retention_tick(repo, batch_limit=100)
        assert result.expired_items == 0
        assert repo.marks == []

    async def test_failed_catalog_delete_still_leaves_the_mark(self):
        # Mark-first ordering: the bytes are scheduled even when pgstac is
        # unreachable this tick; the item delete is retried next tick.
        repo = FakeGcRepo(
            collections=[RetentionCollection("sentinel-2", 14, 7)],
            expired={"sentinel-2": ["item-1"]},
            delete_ok=False,
        )
        result = await retention_tick(repo, batch_limit=100)
        assert result.expired_items == 0
        assert len(repo.marks) == 1

    async def test_re_marking_is_idempotent(self):
        repo = FakeGcRepo(
            collections=[RetentionCollection("sentinel-2", 14, 7)],
            expired={"sentinel-2": ["item-1"]},
            delete_ok=False,  # item survives, so next tick re-lists it
        )
        await retention_tick(repo, batch_limit=100)
        result = await retention_tick(repo, batch_limit=100)
        assert len(repo.marks) == 1
        assert result.marks_created == 0


class TestCollectTick:
    async def test_deletes_prefix_and_stamps_collected(self):
        repo = FakeGcRepo(due=[DueMark("m1", "assets/sentinel-2/item-1/")])
        swept: list[str] = []

        def deleter(prefix: str) -> int:
            swept.append(prefix)
            return 3

        result = await collect_tick(repo, deleter, batch_limit=10)
        assert swept == ["assets/sentinel-2/item-1/"]
        assert result.deleted_objects == 3
        assert repo.collected == ["m1"]

    async def test_empty_prefix_is_a_normal_zero(self):
        # Reference-mode items store no canonical bytes.
        repo = FakeGcRepo(due=[DueMark("m1", "assets/ref-coll/item-1/")])
        result = await collect_tick(repo, lambda prefix: 0, batch_limit=10)
        assert result.collected_marks == 1
        assert result.deleted_objects == 0

    async def test_storage_error_keeps_the_mark_open(self):
        repo = FakeGcRepo(
            due=[
                DueMark("m1", "assets/c/i1/"),
                DueMark("m2", "assets/c/i2/"),
            ]
        )

        def deleter(prefix: str) -> int:
            if prefix.endswith("i1/"):
                raise RuntimeError("minio down")
            return 1

        result = await collect_tick(repo, deleter, batch_limit=10)
        assert result.errors == 1
        assert result.collected_marks == 1
        assert repo.errors[0][0] == "m1"
        assert "minio down" in repo.errors[0][1]
        assert repo.collected == ["m2"]


class TestDeletePrefixPrimitive:
    def test_refuses_unscoped_prefixes(self):
        with pytest.raises(ValueError):
            delete_prefix(object(), "bucket", "")
        with pytest.raises(ValueError):
            delete_prefix(object(), "bucket", "/")

    def test_item_prefix_shape(self):
        assert item_prefix("sentinel-2", "item-1") == "assets/sentinel-2/item-1/"
