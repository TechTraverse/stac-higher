"""Ingest poll scheduler: poll_frequency → whole-minute tick eligibility."""

from __future__ import annotations

from _ingest_fake import FakeIngestRepo
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.repo import IngestAssociation
from pipeline.ingest.scheduler import (
    due_associations,
    is_due,
    poll_interval_ticks,
)


def test_poll_interval_ticks_floors_to_one_minute():
    assert poll_interval_ticks(30) == 1
    assert poll_interval_ticks(60) == 1
    assert poll_interval_ticks(300) == 5
    assert poll_interval_ticks(310) == 5  # rounds to nearest minute


def test_is_due_every_n_minutes():
    # 300s = every 5 minutes: due at minute indices divisible by 5.
    assert is_due(300, 0) is True
    assert is_due(300, 5 * 60) is True
    assert is_due(300, 3 * 60) is False
    # 60s = every minute: always due.
    assert is_due(60, 7 * 60) is True


def _assoc(assoc_id: str, config: dict, *, enabled=True) -> IngestAssociation:
    conn = ConnectionRow(
        id="c", name="n", protocol="s3", config={}, credentials=None, host_key=None
    )
    return IngestAssociation(
        id=assoc_id,
        collection_id="coll",
        connection_id="c",
        config=config,
        connection=conn,
        enabled=enabled,
    )


async def test_due_associations_filters_by_frequency_and_bad_config():
    repo = FakeIngestRepo(
        associations=[
            _assoc("fast", {"source_path": "/a", "poll_frequency_seconds": 60}),
            _assoc("slow", {"source_path": "/b", "poll_frequency_seconds": 300}),
            _assoc("broken", {"poll_frequency_seconds": 60}),  # no source_path
        ]
    )
    # minute index 3 → fast (every min) due, slow (every 5) not, broken skipped.
    due = await due_associations(repo, 3 * 60)
    assert [a.id for a in due] == ["fast"]

    # minute index 5 → both fast and slow due.
    due = await due_associations(repo, 5 * 60)
    assert sorted(a.id for a in due) == ["fast", "slow"]
