"""evaluate() and parse_scan_result() edges the fixture cannot express
(C-1, container-images spec section 6.4/7.3)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from pipeline.images.policy import evaluate, load_image_policy
from pipeline.images.scan_result import MAX_TOP_FINDINGS, ScanResultError, parse_scan_result

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures"
POLICY_DOC = json.loads((FIXTURES / "image-policy.json").read_text())
BASE = POLICY_DOC["base_result"]
NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
POLICY = load_image_policy(FIXTURES.parents[1] / "infra" / "image-policy" / "default.json")


def test_a_failed_scan_has_no_verdict():
    failed = parse_scan_result(
        {
            "version": 1,
            "kind": "admission",
            "reference": BASE["reference"],
            "tag": "x",
            "error": "boom",
        }
    )
    with pytest.raises(ScanResultError):
        evaluate(failed, POLICY, now=NOW)


def test_the_verdict_is_the_spec_shape():
    verdict = evaluate(parse_scan_result(BASE), POLICY, now=NOW).as_json()
    assert set(verdict) == {
        "pass", "reasons", "counts", "fixed_counts", "kev", "max_risk", "evaluated_at",
        "policy_version",
    }
    assert verdict["pass"] is True and verdict["policy_version"] == 1
    assert verdict["evaluated_at"] == "2026-09-27T00:00:00+00:00"


def test_top_is_capped_at_25_findings():
    finding = BASE["top"][0]
    with pytest.raises(ScanResultError, match="at most"):
        parse_scan_result({**BASE, "top": [finding] * (MAX_TOP_FINDINGS + 1)})
    parse_scan_result({**BASE, "top": [finding] * MAX_TOP_FINDINGS})


def test_a_blank_error_is_not_a_failure_marker():
    with pytest.raises(ScanResultError):
        parse_scan_result({**BASE, "error": "   "})


def test_evaluate_is_pure():
    """Purity, asserted for real: the policy and scan-result inputs are
    deep-copied before evaluate() runs (twice), and afterwards the ORIGINALS
    must still equal those copies (evaluate() mutated nothing reachable from
    its arguments), and the two verdicts must be equal to each other."""
    policy_before = copy.deepcopy(POLICY)
    base_before = copy.deepcopy(BASE)
    result = parse_scan_result(BASE)
    result_before = copy.deepcopy(result)

    first = evaluate(result, POLICY, now=NOW)
    second = evaluate(result, POLICY, now=NOW)

    assert policy_before == POLICY
    assert base_before == BASE
    assert result_before == result
    assert first == second
