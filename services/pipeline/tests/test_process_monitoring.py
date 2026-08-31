"""Process alerting conditions and the daily rollup (M5-E, spec §8/§10)."""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.flow.daily import SUBJECT_PROCESS, delta_row
from pipeline.flow.daily_repo import DailyStatsRepo, SubjectStats, rollup_tick
from pipeline.flow.monitor import evaluate_process_stalled
from pipeline.flow.repo import ProcessSourceCandidate

NOW = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)
SRC = "33333333-3333-4333-8333-333333333333"
PROC = "22222222-2222-4222-8222-222222222222"


def candidate(**overrides) -> ProcessSourceCandidate:
    base = {
        "id": SRC,
        "process_id": PROC,
        "process_name": "cloud-mask",
        "collection_id": "sentinel-2",
        "expectation": {"run_within_seconds": 3600},
        "flow_stats": {},
        "updated_at": NOW - dt.timedelta(days=1),
    }
    base.update(overrides)
    return ProcessSourceCandidate(**base)


# ---------------------------------------------------------------------------
# process_stalled
# ---------------------------------------------------------------------------


def test_a_recent_run_is_not_stalled():
    c = candidate(flow_stats={"last_run_at": (NOW - dt.timedelta(minutes=5)).isoformat()})
    assert evaluate_process_stalled(c, 3600, NOW) is None


def test_a_source_past_its_window_is_stalled_and_anchored_on_the_SOURCE():
    """I-63: per-source, so one stalled trigger does not silence another."""
    c = candidate(flow_stats={"last_run_at": (NOW - dt.timedelta(hours=3)).isoformat()})
    condition = evaluate_process_stalled(c, 3600, NOW)
    assert condition is not None
    assert condition.kind == "process_stalled"
    assert condition.source_id == SRC
    # NOT process-anchored: two sources on one process must alert separately.
    assert condition.process_id is None


def test_a_freshly_attached_source_does_not_fire_immediately():
    """The M2-B false-positive guard: with no runs yet the window restarts at
    the source's last edit, so wiring one up is not instantly an incident."""
    c = candidate(updated_at=NOW - dt.timedelta(minutes=1), flow_stats={})
    assert evaluate_process_stalled(c, 3600, NOW) is None


def test_a_source_that_has_NEVER_run_eventually_stalls():
    c = candidate(updated_at=NOW - dt.timedelta(hours=5), flow_stats={})
    condition = evaluate_process_stalled(c, 3600, NOW)
    assert condition is not None
    assert "never" in condition.message or "since the source was configured" in condition.message


def test_a_source_with_neither_a_run_nor_an_edit_time_is_skipped():
    """Nothing to measure against — better silent than a false alarm."""
    assert evaluate_process_stalled(candidate(updated_at=None), 3600, NOW) is None


# ---------------------------------------------------------------------------
# the daily rollup
# ---------------------------------------------------------------------------

DAY = dt.date(2026, 8, 30)


def test_a_day_records_the_DELTA_not_the_running_total():
    row = delta_row(
        SUBJECT_PROCESS, SRC, DAY, {"runs": 130, "items": 40}, {"runs": 100, "items": 30}
    )
    assert row.counters["runs"] == 30
    assert row.counters["items"] == 10


def test_the_first_bucket_attributes_the_whole_total():
    """Over-attributes day one for a subject older than the job — a visible,
    one-off artefact, and better than dropping the day or backfilling from
    ledgers the retention sweep may already have pruned."""
    row = delta_row(SUBJECT_PROCESS, SRC, DAY, {"runs": 100}, None)
    assert row.counters["runs"] == 100


def test_a_counter_that_went_backwards_clamps_to_zero():
    """A reset or a hand-edited rollup must not record a negative day, which
    no reader could plot."""
    row = delta_row(SUBJECT_PROCESS, SRC, DAY, {"runs": 5}, {"runs": 100})
    assert row.counters["runs"] == 0


def test_missing_and_non_numeric_counters_read_as_zero():
    row = delta_row(SUBJECT_PROCESS, SRC, DAY, {"runs": "many", "bytes": True}, None)
    assert row.counters["runs"] == 0
    assert row.counters["bytes"] == 0


class FakeDaily(DailyStatsRepo):
    def __init__(self, subjects, previous=None):
        self.subjects = subjects
        self.previous = previous or {}
        self.written: list = []
        self.pruned_before: dt.date | None = None

    async def list_subjects(self):
        return self.subjects

    async def cumulative_before(self, day):
        return self.previous

    async def upsert_rows(self, rows):
        self.written = rows
        return len(rows)

    async def prune(self, before):
        self.pruned_before = before
        return 0


@pytest.mark.asyncio
async def test_the_tick_snapshots_every_subject_and_prunes_the_window():
    repo = FakeDaily(
        subjects=[
            SubjectStats("association", "a1", {"items": 10}),
            SubjectStats(SUBJECT_PROCESS, SRC, {"runs": 4}),
        ],
        previous={(SUBJECT_PROCESS, SRC): {"runs": 1}},
    )
    written, _pruned = await rollup_tick(repo, day=DAY, retention_days=400)
    assert written == 2
    by_subject = {r.subject_id: r for r in repo.written}
    assert by_subject["a1"].counters["items"] == 10
    assert by_subject[SRC].counters["runs"] == 3
    # ~400 days back, so a year-over-year comparison always has a prior year.
    assert repo.pruned_before == DAY - dt.timedelta(days=400)
