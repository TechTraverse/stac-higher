"""Typed view over an association's ``expectation`` jsonb (ROADMAP §5.1).

Python side of the cross-runtime contract: the app writes the expectation
through ``app/src/lib/associations/schemas.ts`` (Zod, strict per direction) and
the M2-B flow monitor reads the same JSON back out of
``collection_connections.expectation``. Field names MUST NOT drift from
``ingestExpectationSchema`` / ``deliveryExpectationSchema``; golden fixtures in
``tests/contract-fixtures/{ingest,delivery}-expectation.json`` keep both sides
honest.

Lenient-reader semantics (matches the config parsers): unknown keys are
ignored, numbers are coerced, but a present-and-unusable value raises —
a silently mis-read expectation would arm or disarm alerting invisibly.
"""

from __future__ import annotations

from typing import Any


class ExpectationError(ValueError):
    """The stored ``expectation`` jsonb is not a usable §5.1 expectation."""


def _window_seconds(raw: dict[str, Any], key: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpectationError(f"{key} must be a number of seconds, got {value!r}")
    seconds = int(value)
    if seconds < 1:
        raise ExpectationError(f"{key} must be >= 1, got {value!r}")
    return seconds


def parse_ingest_expectation(raw: dict[str, Any] | None) -> int | None:
    """``expect_activity_within_seconds`` for an ingest association, or ``None``
    when no expectation is declared (an empty poll may be normal — §6.6)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ExpectationError(f"expectation must be an object or null, got {raw!r}")
    return _window_seconds(raw, "expect_activity_within_seconds")


def parse_delivery_expectation(raw: dict[str, Any] | None) -> int | None:
    """``deliver_within_seconds`` (the NRT SLO) for a deliver association, or
    ``None`` when no expectation is declared."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ExpectationError(f"expectation must be an object or null, got {raw!r}")
    return _window_seconds(raw, "deliver_within_seconds")
