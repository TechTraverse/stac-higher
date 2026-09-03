"""The ingest date window: bound grammar, resolution, prefix expansion (W-1).

Pure — every function takes `now`, none of them touch a clock or a network,
which is why the interesting cases (a window crossing a year boundary, a
template that would expand to thousands of listings) are cheap to pin.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.ingest.window import (
    Window,
    WindowError,
    expand_prefixes,
    in_window,
    parse_bound,
    resolve_window,
    validate_template,
)

NOW = dt.datetime(2026, 9, 2, 18, 30, 45, tzinfo=dt.UTC)


# --- bound grammar --------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-90s", dt.datetime(2026, 9, 2, 18, 29, 15, tzinfo=dt.UTC)),
        ("-30m", dt.datetime(2026, 9, 2, 18, 0, 45, tzinfo=dt.UTC)),
        ("-6h", dt.datetime(2026, 9, 2, 12, 30, 45, tzinfo=dt.UTC)),
        ("-7d", dt.datetime(2026, 8, 26, 18, 30, 45, tzinfo=dt.UTC)),
        ("-0h", NOW),
    ],
)
def test_relative_bounds_resolve_against_now(value, expected):
    assert parse_bound(value, NOW) == expected


def test_absolute_bounds_are_rfc3339_and_normalised_to_utc():
    assert parse_bound("2026-08-01T00:00:00Z", NOW) == dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
    # A non-UTC offset is accepted and converted, not rejected.
    assert parse_bound("2026-08-01T02:00:00+02:00", NOW) == dt.datetime(2026, 8, 1, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "value",
    ["6h", "+6h", "-6", "-6y", "-6 h", "", "now", "2026-08-01", "-1.5h"],
)
def test_bad_bounds_are_refused(value):
    with pytest.raises(WindowError):
        parse_bound(value, NOW)


# --- window resolution ----------------------------------------------------- #


def test_no_begin_means_no_window():
    assert resolve_window(None, None, NOW) is None


def test_absent_end_is_open_to_now():
    window = resolve_window("-6h", None, NOW)
    assert window == Window(begin=dt.datetime(2026, 9, 2, 12, 30, 45, tzinfo=dt.UTC), end=NOW)


def test_both_bounds_may_be_relative_or_absolute():
    window = resolve_window("-2h", "-10m", NOW)
    assert window.begin == dt.datetime(2026, 9, 2, 16, 30, 45, tzinfo=dt.UTC)
    assert window.end == dt.datetime(2026, 9, 2, 18, 20, 45, tzinfo=dt.UTC)


def test_inverted_window_is_refused():
    with pytest.raises(WindowError, match="begin"):
        resolve_window("2026-08-02T00:00:00Z", "2026-08-01T00:00:00Z", NOW)


def test_membership_is_half_open():
    window = resolve_window("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", NOW)
    begin = dt.datetime(2026, 8, 1, tzinfo=dt.UTC).timestamp()
    end = dt.datetime(2026, 8, 2, tzinfo=dt.UTC).timestamp()
    assert in_window(window, begin) is True
    assert in_window(window, end - 1) is True
    assert in_window(window, end) is False  # end is exclusive
    assert in_window(window, begin - 1) is False


# --- template validation --------------------------------------------------- #


def test_template_must_carry_at_least_one_known_token():
    validate_template("{Y}/{j}/{H}/")
    with pytest.raises(WindowError, match="token"):
        validate_template("static/path/")
    with pytest.raises(WindowError, match="unknown"):
        validate_template("{Y}/{doy}/")


# --- prefix expansion ------------------------------------------------------ #


def test_hourly_expansion_covers_every_hour_the_window_touches():
    window = resolve_window("-3h", None, NOW)  # 15:30 .. 18:30
    assert expand_prefixes("{Y}/{j}/{H}/", window, max_prefixes=100) == (
        "2026/245/15/",
        "2026/245/16/",
        "2026/245/17/",
        "2026/245/18/",
    )


def test_granularity_is_the_finest_token_present():
    window = resolve_window("-3h", None, NOW)
    # No {H}: one prefix for the day, not four for the hours.
    assert expand_prefixes("{Y}/{j}/", window, max_prefixes=100) == ("2026/245/",)
    assert expand_prefixes("{Y}/", window, max_prefixes=100) == ("2026/",)


def test_month_and_day_tokens():
    window = resolve_window("2026-01-30T22:00:00Z", "2026-02-01T01:00:00Z", NOW)
    assert expand_prefixes("{Y}/{m}/{d}/", window, max_prefixes=100) == (
        "2026/01/30/",
        "2026/01/31/",
        "2026/02/01/",
    )


def test_expansion_rolls_over_a_year_boundary():
    window = resolve_window("2025-12-31T23:00:00Z", "2026-01-01T01:00:00Z", NOW)
    # The end is exclusive, so a window ending exactly on the hour does not
    # touch that hour's prefix — nothing in it could pass `in_window`.
    assert expand_prefixes("{Y}/{j}/{H}/", window, max_prefixes=100) == (
        "2025/365/23/",
        "2026/001/00/",
    )


def test_expansion_over_the_ceiling_is_refused_rather_than_truncated():
    window = resolve_window("-90d", None, NOW)
    with pytest.raises(WindowError, match="max_prefixes"):
        expand_prefixes("{Y}/{j}/{H}/", window, max_prefixes=1000)
