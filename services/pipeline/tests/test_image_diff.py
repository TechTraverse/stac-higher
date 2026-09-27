"""The rescan diff (container-images spec §8.2): what changed since the
image's previous scan, over the stored summaries."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.images.diff import scan_diff
from pipeline.images.scan_result import parse_scan_result

BASE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]
PREV = "11111111-2222-4333-8444-555555555555"


def _doc(**patch):
    return {**BASE, "kind": "rescan", **patch}


def test_no_previous_document_means_no_diff():
    current = parse_scan_result(_doc())
    assert scan_diff(None, current, current_pass=True, previous_scan_id=PREV) is None


def test_a_failed_previous_scan_means_no_diff():
    failed = {"version": 1, "kind": "rescan", "reference": BASE["reference"],
              "tag": BASE["tag"], "error": "boom"}
    current = parse_scan_result(_doc())
    assert scan_diff(failed, current, current_pass=True, previous_scan_id=PREV) is None


def test_an_unreadable_previous_document_means_no_diff():
    current = parse_scan_result(_doc())
    assert scan_diff({"nonsense": 1}, current, current_pass=True, previous_scan_id=PREV) is None


def test_an_unchanged_scan_diffs_to_nothing():
    current = parse_scan_result(_doc())
    diff = scan_diff(
        {**_doc(), "verdict": {"pass": True}}, current, current_pass=True, previous_scan_id=PREV
    )
    assert diff is not None
    assert (diff.new, diff.resolved, diff.newly_fixed, diff.new_kev) == ((), (), (), ())
    assert diff.verdict_changed is False
    assert set(diff.counts_delta.values()) == {0}


def test_a_previous_verdict_that_is_missing_never_reads_as_changed():
    current = parse_scan_result(_doc())
    diff = scan_diff(_doc(), current, current_pass=False, previous_scan_id=PREV)
    assert diff is not None and diff.verdict_changed is False
