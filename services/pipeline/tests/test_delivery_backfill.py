"""Slice C: user-initiated backfill — chunking, cursor resume, sweep states."""

from dataclasses import dataclass, field

import pytest

from pipeline.config import Settings
from pipeline.delivery.backfill import (
    BackfillJob,
    BackfillRepo,
    blocked_reason,
    run_backfill,
)
from pipeline.jobs import backfill as backfill_jobs
from pipeline.jobs.backfill import JOB_NAME, backfill_sweep_tick
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio


@dataclass
class FakeBackfillRepo(BackfillRepo):
    open_jobs: list[BackfillJob] = field(default_factory=list)
    item_ids: dict[str, list[str]] = field(default_factory=dict)
    progress: list[tuple[str, int, str]] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    async def claim_open_backfills(self, limit, stale_running_seconds):
        claimed, self.open_jobs = self.open_jobs[:limit], self.open_jobs[limit:]
        return claimed

    async def list_item_ids(self, collection_id, after_item_id, limit):
        ids = self.item_ids.get(collection_id, [])
        if after_item_id is not None:
            ids = [i for i in ids if i > after_item_id]
        return ids[:limit]

    async def record_progress(self, backfill_id, items_enqueued, cursor_item_id):
        self.progress.append((backfill_id, items_enqueued, cursor_item_id))

    async def mark_completed(self, backfill_id):
        self.completed.append(backfill_id)

    async def mark_failed(self, backfill_id, error):
        self.failed.append((backfill_id, error))


def _collector():
    captured: list[list[dict]] = []

    async def _enqueue(batches):
        captured.append(batches)

    return _enqueue, captured


def _job(**kwargs):
    defaults = {"id": "b1", "association_id": "a1", "collection_id": "col"}
    return BackfillJob(**{**defaults, **kwargs})


async def test_run_backfill_chunks_and_records_progress():
    repo = FakeBackfillRepo(item_ids={"col": ["i1", "i2", "i3"]})
    enqueue, captured = _collector()
    total = await run_backfill(repo, enqueue, _job(), chunk_size=2)
    assert total == 3
    # 2-item chunk + 1-item chunk, each one batched deliver job for a1.
    assert [len(b[0]["items"]) for b in captured] == [2, 1]
    assert all(b[0]["association_id"] == "a1" for b in captured)
    # asset_keys None ⇒ the deliver job re-derives from current assets.
    assert captured[0][0]["items"][0] == {
        "item_id": "i1",
        "asset_keys": None,
        "item_created_at": None,
    }
    assert repo.progress == [("b1", 2, "i2"), ("b1", 3, "i3")]
    assert repo.completed == ["b1"]


async def test_run_backfill_resumes_from_cursor():
    """A stale-running reclaim resumes strictly after the recorded cursor."""
    repo = FakeBackfillRepo(item_ids={"col": ["i1", "i2", "i3"]})
    enqueue, captured = _collector()
    total = await run_backfill(
        repo, enqueue, _job(items_enqueued=2, cursor_item_id="i2"), chunk_size=2
    )
    assert total == 3
    assert [i["item_id"] for i in captured[0][0]["items"]] == ["i3"]
    assert repo.progress == [("b1", 3, "i3")]


async def test_run_backfill_empty_collection_completes_with_zero():
    repo = FakeBackfillRepo()
    enqueue, captured = _collector()
    total = await run_backfill(repo, enqueue, _job())
    assert total == 0
    assert captured == []
    assert repo.completed == ["b1"]


def test_blocked_reason_policy():
    assert blocked_reason(_job()) is None
    assert blocked_reason(_job(deleted=True)) == "association deleted"
    assert blocked_reason(_job(direction="ingest")) == "not a deliver association"
    assert blocked_reason(_job(enabled=False)) == (
        "association or connection disabled"
    )
    # deleted wins over the other states (a deleted row is also disabled).
    assert blocked_reason(_job(deleted=True, enabled=False)) == "association deleted"


async def test_sweep_fails_blocked_backfill_without_enqueueing():
    repo = FakeBackfillRepo(
        open_jobs=[_job(deleted=True)],
        item_ids={"col": ["i1"]},
    )
    enqueue, captured = _collector()
    finished = await backfill_sweep_tick(repo, enqueue)
    assert finished == 1
    assert repo.failed == [("b1", "association deleted")]
    assert captured == []


async def test_sweep_isolates_one_backfills_failure():
    """A raising backfill is marked failed; its sibling still completes."""

    class _ExplodingRepo(FakeBackfillRepo):
        async def list_item_ids(self, collection_id, after_item_id, limit):
            if collection_id == "boom":
                raise RuntimeError("pgstac exploded")
            return await super().list_item_ids(collection_id, after_item_id, limit)

    repo = _ExplodingRepo(
        open_jobs=[_job(id="b1", collection_id="boom"), _job(id="b2")],
        item_ids={"col": ["i1"]},
    )
    enqueue, captured = _collector()
    finished = await backfill_sweep_tick(repo, enqueue)
    assert finished == 2
    assert repo.failed == [("b1", "pgstac exploded")]
    assert repo.completed == ["b2"]
    assert len(captured) == 1


def test_register_wires_backfill_sweep():
    queue = InMemoryQueue()
    backfill_jobs.register(queue, Settings.from_env(env={}))
    assert JOB_NAME in queue.periodic


def test_build_queue_includes_backfill_sweep():
    queue = build_queue(Settings.from_env(env={}))
    assert JOB_NAME in {t for t in queue.app.tasks}
