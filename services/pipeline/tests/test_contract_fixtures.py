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
from pipeline.flow.expectation import parse_delivery_expectation, parse_ingest_expectation
from pipeline.ingest.config import parse_ingest_config
from pipeline.notify.config import parse_webhook_config

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


INGEST = _load("ingest-config.json")
DELIVERY = _load("delivery-config.json")
INGEST_EXPECTATION = _load("ingest-expectation.json")
DELIVERY_EXPECTATION = _load("delivery-expectation.json")
WEBHOOK = _load("webhook-channel-config.json")


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
