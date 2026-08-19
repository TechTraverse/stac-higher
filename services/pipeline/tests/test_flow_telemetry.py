"""Flow telemetry (M2-A): the rollup writes hooked into ingest jobs and the
delivery repo transitions.

The fakes apply the same pure ``flow.stats`` math the Pg repos apply
in-transaction, so these tests pin the SEMANTICS (which transition bumps what)
— the SQL persistence itself is DB-integration / rehearsal territory.
"""

from __future__ import annotations

import datetime as dt

from _delivery_fake import FakeDeliveryRepo
from _ingest_fake import FakeIngestRepo
from pipeline.config import Settings
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.discover import DiscoverResult
from pipeline.ingest.itemize import ItemizeOutcome
from pipeline.ingest.repo import IngestAssociation
from pipeline.jobs import ingest
from pipeline.queue.memory import InMemoryQueue

ASSOC = "a1"


# --------------------------------------------------------------------------- #
# delivery repo transitions → counts snapshot


async def test_delivery_lifecycle_counts_and_scalars():
    repo = FakeDeliveryRepo()
    pre = await repo.pre_record(ASSOC, [("item-1", None)])
    assert repo.flow_stats[ASSOC]["counts"]["pending"] == 1

    row_id = await repo.upsert_pending(ASSOC, "item-1", None)
    assert pre[0].id == row_id
    # pending → pending is a no-op for the snapshot
    assert repo.flow_stats[ASSOC]["counts"]["pending"] == 1

    await repo.mark_delivering(row_id)
    counts = repo.flow_stats[ASSOC]["counts"]
    assert (counts["pending"], counts["delivering"]) == (0, 1)

    await repo.mark_delivered(row_id, 512, {})
    stats = repo.flow_stats[ASSOC]
    assert stats["counts"]["delivering"] == 0
    assert stats["counts"]["delivered"] == 1
    assert stats["bytes"] == 512
    assert "last_activity_at" in stats
    assert "last_error_at" not in stats


async def test_delivery_failure_and_dead_stamp_error():
    repo = FakeDeliveryRepo()
    row_id = await repo.upsert_pending(ASSOC, "item-1", None)
    await repo.mark_delivering(row_id)
    await repo.mark_failed(row_id, "boom", next_attempt_at=dt.datetime.now(dt.UTC))
    stats = repo.flow_stats[ASSOC]
    assert stats["counts"]["failed"] == 1
    assert "last_error_at" in stats

    await repo.requeue_for_retry([row_id])
    assert repo.flow_stats[ASSOC]["counts"] == {
        "pending": 1,
        "delivering": 0,
        "delivered": 0,
        "failed": 0,
        "dead": 0,
    }

    await repo.mark_delivering(row_id)
    await repo.mark_failed(row_id, "boom", dead=True)
    counts = repo.flow_stats[ASSOC]["counts"]
    assert (counts["failed"], counts["dead"]) == (0, 1)


async def test_discarded_pre_records_leave_no_phantom_pending():
    repo = FakeDeliveryRepo()
    pre = await repo.pre_record(ASSOC, [("item-1", None), ("item-2", None)])
    await repo.discard_pre_records([p.id for p in pre])
    assert repo.flow_stats[ASSOC]["counts"]["pending"] == 0


async def test_delivered_latency_recorded_from_item_created_at():
    repo = FakeDeliveryRepo()
    created = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=42)).isoformat()
    row_id = await repo.upsert_pending(ASSOC, "item-1", created)
    await repo.mark_delivering(row_id)
    await repo.mark_delivered(row_id, 1, {})
    latency = repo.flow_stats[ASSOC]["last_latency_seconds"]
    assert 41 <= latency <= 60


# --------------------------------------------------------------------------- #
# ingest job hooks → rollup writes


def _wire(monkeypatch, repo: FakeIngestRepo):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest.register(queue, settings)
    assoc = IngestAssociation(
        id=ASSOC, collection_id="col", config={"source_path": "/o"}, connection=None
    )
    config = parse_ingest_config({"source_path": "/o"})

    async def _fake_load(_settings, _aid):
        return (repo, assoc, config)

    monkeypatch.setattr(ingest, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(ingest, "_load_association", _fake_load)
    monkeypatch.setattr(ingest, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(ingest, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(ingest, "PgPgstacWriter", lambda _url: object())
    return queue


async def test_discover_handler_bumps_files_and_bytes_on_settle(monkeypatch):
    repo = FakeIngestRepo()
    queue = _wire(monkeypatch, repo)

    async def _fake_discover(*_a, **_k):
        return DiscoverResult(listed=3, settled=2, settled_bytes=300)

    monkeypatch.setattr(ingest, "discover_stage", _fake_discover)
    await queue.tasks["pipeline.ingest_discover"](association_id=ASSOC)
    stats = repo.flow_stats[ASSOC]
    assert stats["files"] == 2
    assert stats["bytes"] == 300
    assert "last_activity_at" in stats


async def test_discover_handler_writes_nothing_on_empty_poll(monkeypatch):
    repo = FakeIngestRepo()
    queue = _wire(monkeypatch, repo)

    async def _fake_discover(*_a, **_k):
        return DiscoverResult(listed=3, unchanged=3)

    monkeypatch.setattr(ingest, "discover_stage", _fake_discover)
    await queue.tasks["pipeline.ingest_discover"](association_id=ASSOC)
    # An empty poll may be normal (§6.6) — no activity stamp, no rollup row.
    assert ASSOC not in repo.flow_stats


async def test_itemize_handler_bumps_items_on_success(monkeypatch):
    repo = FakeIngestRepo()
    queue = _wire(monkeypatch, repo)

    async def _fake_itemize(*_a, **_k):
        return ItemizeOutcome("itemized", "scene", bytes=128, latency_seconds=61.0)

    monkeypatch.setattr(ingest, "run_itemize", _fake_itemize)
    await queue.tasks["pipeline.ingest_itemize"](
        association_id=ASSOC, item_id="scene", source_paths=["scene.tif"]
    )
    stats = repo.flow_stats[ASSOC]
    assert stats["items"] == 1
    assert stats["bytes"] == 128
    assert stats["last_latency_seconds"] == 61.0
    assert "last_activity_at" in stats


async def test_itemize_handler_stamps_error_on_failure(monkeypatch):
    repo = FakeIngestRepo()
    queue = _wire(monkeypatch, repo)

    async def _fake_itemize(*_a, **_k):
        return ItemizeOutcome("failed", "scene", "extract: boom")

    monkeypatch.setattr(ingest, "run_itemize", _fake_itemize)
    await queue.tasks["pipeline.ingest_itemize"](
        association_id=ASSOC, item_id="scene", source_paths=["scene.tif"]
    )
    stats = repo.flow_stats[ASSOC]
    assert stats["failed"] == 1
    assert "last_error_at" in stats
    assert "last_activity_at" not in stats


async def test_itemize_handler_skip_writes_nothing(monkeypatch):
    repo = FakeIngestRepo()
    queue = _wire(monkeypatch, repo)

    async def _fake_itemize(*_a, **_k):
        return ItemizeOutcome("skipped", "scene", "no stored members")

    monkeypatch.setattr(ingest, "run_itemize", _fake_itemize)
    await queue.tasks["pipeline.ingest_itemize"](
        association_id=ASSOC, item_id="scene", source_paths=["scene.tif"]
    )
    assert ASSOC not in repo.flow_stats
