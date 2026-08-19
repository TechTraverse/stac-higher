"""Unit tests for the pure flow_stats rollup math (M2-A).

These functions are the single implementation of the rollup shared by the Pg
repos (applied inside the same transaction as each ledger/log write) and the
test fakes — so the math itself is fully unit-tested even though the SQL that
persists it is exercised only by the DB integration suite / live rehearsal.
"""

import datetime as dt

from pipeline.flow.expectation import (
    ExpectationError,
    parse_delivery_expectation,
    parse_ingest_expectation,
)
from pipeline.flow.stats import (
    apply_count_delta,
    apply_delivery_event,
    apply_ingest_activity,
    zero_counts,
)

NOW = dt.datetime(2026, 8, 18, 12, 0, 0, tzinfo=dt.UTC)


class TestApplyCountDelta:
    def test_insert_seeds_zero_counts(self):
        stats = apply_count_delta({}, None, "pending")
        assert stats["counts"] == {**zero_counts(), "pending": 1}

    def test_transition_moves_one_row(self):
        stats = {"counts": {**zero_counts(), "pending": 2}}
        stats = apply_count_delta(stats, "pending", "delivering")
        assert stats["counts"]["pending"] == 1
        assert stats["counts"]["delivering"] == 1

    def test_delete_decrements_only(self):
        stats = {"counts": {**zero_counts(), "pending": 3}}
        stats = apply_count_delta(stats, "pending", None, n=2)
        assert stats["counts"]["pending"] == 1

    def test_decrement_clamps_at_zero(self):
        stats = apply_count_delta({}, "delivered", "failed")
        assert stats["counts"]["delivered"] == 0
        assert stats["counts"]["failed"] == 1

    def test_batch_delta(self):
        stats = apply_count_delta({}, None, "pending", n=5)
        assert stats["counts"]["pending"] == 5

    def test_input_dict_not_mutated(self):
        original = {"counts": {**zero_counts(), "pending": 1}, "bytes": 7}
        apply_count_delta(original, "pending", "delivering")
        assert original["counts"]["pending"] == 1
        assert original["bytes"] == 7

    def test_preserves_scalar_fields(self):
        stats = {"bytes": 10, "last_activity_at": "x"}
        out = apply_count_delta(stats, None, "pending")
        assert out["bytes"] == 10
        assert out["last_activity_at"] == "x"


class TestApplyDeliveryEvent:
    def test_delivered_accumulates_bytes_and_stamps_activity(self):
        stats = apply_delivery_event(
            {"bytes": 100}, bytes_added=50, latency_seconds=2.5, activity=True, now=NOW
        )
        assert stats["bytes"] == 150
        assert stats["last_latency_seconds"] == 2.5
        assert stats["last_activity_at"] == NOW.isoformat()
        assert "last_error_at" not in stats

    def test_error_stamps_last_error_only(self):
        stats = apply_delivery_event({}, error=True, now=NOW)
        assert stats["last_error_at"] == NOW.isoformat()
        assert "last_activity_at" not in stats
        assert "bytes" not in stats

    def test_zero_bytes_event_leaves_bytes_untouched(self):
        assert "bytes" not in apply_delivery_event({}, activity=True, now=NOW)


class TestApplyIngestActivity:
    def test_settle_counts_files_and_stamps_activity(self):
        stats = apply_ingest_activity({}, files=3, bytes_added=0, now=NOW)
        assert stats["files"] == 3
        assert stats["last_activity_at"] == NOW.isoformat()
        assert "last_error_at" not in stats

    def test_itemize_accumulates(self):
        stats = {"files": 3, "items": 1, "bytes": 10}
        stats = apply_ingest_activity(
            stats, items=1, bytes_added=90, latency_seconds=61.0, now=NOW
        )
        assert stats["items"] == 2
        assert stats["bytes"] == 100
        assert stats["last_latency_seconds"] == 61.0
        assert stats["last_activity_at"] == NOW.isoformat()

    def test_failure_stamps_error_not_activity(self):
        stats = apply_ingest_activity({}, failed=1, now=NOW)
        assert stats["failed"] == 1
        assert stats["last_error_at"] == NOW.isoformat()
        assert "last_activity_at" not in stats


class TestParseExpectation:
    def test_none_is_no_expectation(self):
        assert parse_ingest_expectation(None) is None
        assert parse_delivery_expectation(None) is None

    def test_reads_the_declared_window(self):
        assert parse_ingest_expectation({"expect_activity_within_seconds": 3600}) == 3600
        assert parse_delivery_expectation({"deliver_within_seconds": 30}) == 30

    def test_non_dict_raises(self):
        import pytest

        with pytest.raises(ExpectationError):
            parse_ingest_expectation([1])  # type: ignore[arg-type]
