"""Golden-fixture contract tests (ISSUE I-53): the Python half.

The same documents are run through the Zod schemas by
``app/src/__tests__/contract-fixtures.test.ts`` — a §5.1 shape or default
drifting on either side fails one of the suites. Fixture format and the
accept/reject semantics: ``tests/contract-fixtures/README.md`` (repo root).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from pipeline.delivery.config import parse_delivery_config
from pipeline.finalize.status import StatusContractError, validate_status_doc
from pipeline.flow.expectation import parse_delivery_expectation, parse_ingest_expectation
from pipeline.ingest.config import parse_ingest_config
from pipeline.notify.config import parse_webhook_config
from pipeline.process.config import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    parse_process_env,
    parse_process_expectation,
    parse_process_runtime,
    parse_process_trigger,
)
from pipeline.storage.keys import InvalidKeySegment, is_staged_href, parse_staged_href

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


INGEST = _load("ingest-config.json")
DELIVERY = _load("delivery-config.json")
INGEST_EXPECTATION = _load("ingest-expectation.json")
DELIVERY_EXPECTATION = _load("delivery-expectation.json")
WEBHOOK = _load("webhook-channel-config.json")
STAGED_HREF = _load("staged-asset-href.json")
PUSH_STATUS = _load("push-upload-status.json")
ALERT_KINDS = _load("alert-kinds.json")
PROCESS_TRIGGER = _load("process-trigger.json")
PROCESS_RUNTIME = _load("process-runtime.json")
PROCESS_ENV = _load("process-env.json")
PROCESS_EXPECTATION = _load("process-expectation.json")
PROCESS_INPUT_MANIFEST = _load("process-input-manifest.json")


def _check(parser, case: dict[str, Any]) -> None:
    if case["pipeline"] == "accept":
        parser(case["config"])
    else:
        # Both config parsers raise their ValueError subclass for contract
        # violations; container-type coercion failures raise TypeError.
        with pytest.raises((ValueError, TypeError)):
            parser(case["config"])


@pytest.mark.parametrize("case", INGEST["cases"], ids=lambda c: c["name"])
def test_ingest_cases(case):
    _check(parse_ingest_config, case)


@pytest.mark.parametrize("case", DELIVERY["cases"], ids=lambda c: c["name"])
def test_delivery_cases(case):
    _check(parse_delivery_config, case)


@pytest.mark.parametrize("case", INGEST_EXPECTATION["cases"], ids=lambda c: c["name"])
def test_ingest_expectation_cases(case):
    _check(parse_ingest_expectation, case)


@pytest.mark.parametrize("case", DELIVERY_EXPECTATION["cases"], ids=lambda c: c["name"])
def test_delivery_expectation_cases(case):
    _check(parse_delivery_expectation, case)


@pytest.mark.parametrize("case", WEBHOOK["cases"], ids=lambda c: c["name"])
def test_webhook_channel_config_cases(case):
    _check(parse_webhook_config, case)


def test_webhook_channel_config_matches_golden():
    parsed = parse_webhook_config(WEBHOOK["minimal"])
    assert parsed.url == WEBHOOK["defaults"]["url"]
    assert parsed.secret is None


def test_ingest_expectation_matches_golden():
    minimal = INGEST_EXPECTATION["minimal"]
    golden = INGEST_EXPECTATION["defaults"]
    assert parse_ingest_expectation(minimal) == golden["expect_activity_within_seconds"]
    assert parse_ingest_expectation(None) is None


def test_delivery_expectation_matches_golden():
    minimal = DELIVERY_EXPECTATION["minimal"]
    golden = DELIVERY_EXPECTATION["defaults"]
    assert parse_delivery_expectation(minimal) == golden["deliver_within_seconds"]
    assert parse_delivery_expectation(None) is None


@pytest.mark.parametrize("case", STAGED_HREF["cases"], ids=lambda c: c["name"])
def test_staged_href_cases(case):
    """The staging:// grammar (Phase 7 §4.2, grammar-cases style): `detected`
    pins the dispatcher-style prefix check; `parse` pins the strict
    upload_id/filename extraction (null = reject)."""
    assert is_staged_href(case["href"]) == case["detected"]
    if case["parse"] is None:
        with pytest.raises(InvalidKeySegment):
            parse_staged_href(case["href"])
    else:
        parts = parse_staged_href(case["href"])
        assert parts.upload_id == case["parse"]["upload_id"]
        assert parts.filename == case["parse"]["filename"]


@pytest.mark.parametrize("case", PUSH_STATUS["cases"], ids=lambda c: c["name"])
def test_push_upload_status_cases(case):
    """The staged_uploads status contract (status-contract style): the
    pipeline is the STRICT writer — `pipeline: accept` docs are within what
    the recorder/sweeps may write; `reject` docs must fail the writer gate
    (the app's Zod reader is the deliberately-lenient side)."""
    if case["pipeline"] == "accept":
        validate_status_doc(case["doc"])
    else:
        with pytest.raises(StatusContractError):
            validate_status_doc(case["doc"])


def test_push_status_enums_match_golden():
    """The fixture's status/terminal/reason sets are the writer's, verbatim."""
    from pipeline.finalize.status import REJECTION_REASONS, STATUSES, TERMINAL_STATUSES

    assert set(PUSH_STATUS["statuses"]) == STATUSES
    assert set(PUSH_STATUS["terminal"]) == TERMINAL_STATUSES
    assert set(PUSH_STATUS["reasons"]) == REJECTION_REASONS


def test_alert_kinds_match_golden():
    """The pinned-enum fixture (P7-H): each writer-side constant equals its
    fixture list VERBATIM (order included — the fixture is canonical), and
    the writer lists partition the full enum exactly."""
    from pipeline.flow.monitor import MONITOR_KINDS
    from pipeline.notify.repo import WEBHOOK_FAILED_KIND

    assert ALERT_KINDS["monitor_kinds"] == list(MONITOR_KINDS)
    assert ALERT_KINDS["notify_kinds"] == [WEBHOOK_FAILED_KIND]
    assert ALERT_KINDS["kinds"] == (
        ALERT_KINDS["monitor_kinds"]
        + ALERT_KINDS["declared_kinds"]
        + ALERT_KINDS["notify_kinds"]
    )
    assert len(set(ALERT_KINDS["kinds"])) == len(ALERT_KINDS["kinds"])


def test_ingest_defaults_match_golden():
    """Every §5.1 ingest default this parser applies must equal the app's."""
    golden = INGEST["defaults"]
    cfg = parse_ingest_config(INGEST["minimal"])
    assert cfg.include == tuple(golden["include"])
    assert cfg.exclude == tuple(golden["exclude"])
    assert cfg.poll_frequency_seconds == golden["poll_frequency_seconds"]
    assert cfg.storage_mode == golden["storage_mode"]
    assert cfg.grouping.rule == golden["grouping"]["rule"]
    assert cfg.grouping.timeout_seconds == golden["grouping"]["timeout_seconds"]
    assert cfg.grouping.on_timeout == golden["grouping"]["on_timeout"]
    assert cfg.post_ingest == golden["post_ingest"]


def test_delivery_defaults_match_golden():
    """Every §5.1 delivery default this parser applies must equal the app's."""
    golden = DELIVERY["defaults"]
    cfg = parse_delivery_config(DELIVERY["minimal"])
    assert cfg.item_filter == golden["item_filter"]
    assert cfg.asset_keys == golden["asset_keys"]
    assert cfg.payload == golden["payload"]
    assert cfg.on_update == golden["on_update"]
    assert cfg.overwrite == golden["overwrite"]
    assert cfg.max_attempts == golden["retry"]["max_attempts"]
    assert cfg.backoff == golden["retry"]["backoff"]
    assert cfg.max_concurrent_transfers == golden["max_concurrent_transfers"]


# ---------------------------------------------------------------------------
# Phase 9 / M5-0 — the process shapes (design spec §3, ROADMAP §5.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", PROCESS_TRIGGER["cases"], ids=lambda c: c["name"])
def test_process_trigger_cases(case):
    _check(parse_process_trigger, case)


@pytest.mark.parametrize("case", PROCESS_RUNTIME["cases"], ids=lambda c: c["name"])
def test_process_runtime_cases(case):
    """Note the deliberate asymmetry the fixture encodes: every `container`
    case is `pipeline: accept` / `app: reject`. The contract carries the arm so
    nothing is foreclosed; slice 1's refusal lives at the app's write gate
    (design spec §4), NOT here."""
    _check(parse_process_runtime, case)


@pytest.mark.parametrize("case", PROCESS_ENV["cases"], ids=lambda c: c["name"])
def test_process_env_cases(case):
    _check(parse_process_env, case)


@pytest.mark.parametrize("case", PROCESS_EXPECTATION["cases"], ids=lambda c: c["name"])
def test_process_expectation_cases(case):
    _check(parse_process_expectation, case)


def test_process_trigger_defaults_match_golden():
    """One minimal/defaults pair per union arm (the discriminated-union fixture
    style) — each field this reader defaults must equal the app's."""
    variants = PROCESS_TRIGGER["variants"]

    item_event = parse_process_trigger(variants["item_event"]["minimal"])
    assert item_event.kind == variants["item_event"]["defaults"]["kind"]
    assert item_event.item_filter == variants["item_event"]["defaults"]["item_filter"]

    cron = parse_process_trigger(variants["cron"]["minimal"])
    assert cron.kind == variants["cron"]["defaults"]["kind"]
    assert cron.schedule == variants["cron"]["defaults"]["schedule"]


def test_process_runtime_defaults_match_golden():
    for arm, variant in PROCESS_RUNTIME["variants"].items():
        golden = variant["defaults"]
        runtime = parse_process_runtime(variant["minimal"])
        assert runtime.kind == arm == golden["kind"]
        assert runtime.image == golden.get("image")
        assert runtime.memory_mb == golden["memory_mb"]
        assert runtime.timeout_seconds == golden["timeout_seconds"]
        assert runtime.max_attempts == golden["retry"]["max_attempts"]
        assert runtime.backoff == golden["retry"]["backoff"]

    # The module constants ARE those defaults — a drift here would let the
    # dataclass and the fixture disagree without any case failing.
    inline = PROCESS_RUNTIME["variants"]["inline_python"]["defaults"]
    assert inline["memory_mb"] == DEFAULT_MEMORY_MB
    assert inline["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS
    assert inline["retry"]["max_attempts"] == DEFAULT_MAX_ATTEMPTS


def test_process_env_defaults_match_golden():
    assert parse_process_env(PROCESS_ENV["minimal"]) == ()
    assert PROCESS_ENV["defaults"] == []
    # A null column (a revision that declared no env at all) reads as empty,
    # not as a contract violation.
    assert parse_process_env(None) == ()


def test_process_expectation_defaults_match_golden():
    golden = PROCESS_EXPECTATION["defaults"]
    assert parse_process_expectation(PROCESS_EXPECTATION["minimal"]) == golden[
        "run_within_seconds"
    ]
    # No expectation declared = no alerting, never a parse failure.
    assert parse_process_expectation(None) is None


def test_process_alert_kinds_are_monitor_owned():
    """M5-E claimed all three for the monitor. That is correct precisely
    because each is evaluated as a CONDITION observed from state — a breached
    per-source expectation, a dead run, a deferred run — so auto-resolve falls
    out of the condition's absence and no second writer is involved.

    (M5-0 parked them in `declared_kinds` rather than guessing; the partition
    test is what forced this to be a decision. The list stays as the mechanism
    for the next kind whose writer is undecided.)"""
    from pipeline.flow.monitor import MONITOR_KINDS

    for kind in ("process_stalled", "process_failed", "process_rate_limited"):
        assert kind in MONITOR_KINDS
        assert kind not in ALERT_KINDS["declared_kinds"]



def test_process_input_manifest_fixture_is_registered():
    """Producer-golden (GOES spec §3.1): the pipeline is the only writer, so
    the real assertion lives in test_process_inputs.py. This keeps the file
    loaded where the README says every fixture is loaded."""
    assert PROCESS_INPUT_MANIFEST["style"] == "producer-golden"
    assert PROCESS_INPUT_MANIFEST["version"] == 1
