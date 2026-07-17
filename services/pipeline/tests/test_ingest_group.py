"""GROUP stage: none/shared_basename grouping + timeout handling."""

from __future__ import annotations

import datetime as dt

from _ingest_fake import EPOCH, FakeIngestRepo
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.group import group_stage


async def _settle(repo, source_path, *, settled_at=EPOCH):
    repo.now = settled_at
    eid = await repo.insert_ledger_version(
        "assoc1", source_path, version=1, status="settled", size=10, fingerprint="fp"
    )
    return eid


async def test_none_rule_one_group_per_file():
    repo = FakeIngestRepo()
    await _settle(repo, "a.tif")
    await _settle(repo, "b.tif")
    cfg = parse_ingest_config({"source_path": "in", "grouping": {"rule": "none"}})
    result = await group_stage(repo, "assoc1", cfg, EPOCH)
    items = sorted(g.item_id for g in result.ready)
    assert items == ["a", "b"]
    assert all(len(g.members) == 1 for g in result.ready)


async def test_shared_basename_groups_siblings_after_timeout():
    repo = FakeIngestRepo()
    await _settle(repo, "scene.tif", settled_at=EPOCH)
    await _settle(repo, "scene.xml", settled_at=EPOCH)
    cfg = parse_ingest_config(
        {"source_path": "in", "grouping": {"rule": "shared_basename", "timeout_seconds": 120}}
    )
    # now is well past the timeout window
    now = EPOCH + dt.timedelta(seconds=600)
    result = await group_stage(repo, "assoc1", cfg, now)
    assert len(result.ready) == 1
    group = result.ready[0]
    assert group.item_id == "scene"
    assert sorted(m.source_path for m in group.members) == ["scene.tif", "scene.xml"]


async def test_shared_basename_waits_within_window():
    repo = FakeIngestRepo()
    await _settle(repo, "scene.tif", settled_at=EPOCH)
    cfg = parse_ingest_config(
        {"source_path": "in", "grouping": {"rule": "shared_basename", "timeout_seconds": 300}}
    )
    now = EPOCH + dt.timedelta(seconds=100)  # inside the window
    result = await group_stage(repo, "assoc1", cfg, now)
    assert result.ready == []
    assert result.waiting == 1


async def test_shared_basename_discard_on_timeout_marks_failed():
    repo = FakeIngestRepo()
    eid = await _settle(repo, "scene.tif", settled_at=EPOCH)
    cfg = parse_ingest_config(
        {
            "source_path": "in",
            "grouping": {
                "rule": "shared_basename",
                "timeout_seconds": 60,
                "on_timeout": "discard",
            },
        }
    )
    now = EPOCH + dt.timedelta(seconds=600)
    result = await group_stage(repo, "assoc1", cfg, now)
    assert result.ready == []
    assert result.discarded == 1
    assert repo.rows[eid].status == "failed"


async def test_reference_mode_forms_no_groups():
    repo = FakeIngestRepo()
    await _settle(repo, "a.tif")
    cfg = parse_ingest_config({"source_path": "in", "storage_mode": "reference"})
    result = await group_stage(repo, "assoc1", cfg, EPOCH)
    assert result.ready == []
    assert result.skipped_reference is True
