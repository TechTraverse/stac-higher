"""The ingest date window (W-1, spec §3) — bounds, membership, prefixes.

Pure by construction: every function that needs the current time takes it as
an argument. That is what makes a rolling window testable — "the last six
hours" is only meaningful relative to a moment, and a module that reads the
clock itself can only be tested by mocking it.

Two jobs, deliberately separate:

- **membership** (`in_window`) is the precise gate, applied to every listed
  entry's modified time. It works for any protocol.
- **expansion** (`expand_prefixes`) is the coarse gate, and exists so the
  LISTING is bounded too. Without it a six-hour window over a date-partitioned
  archive still pages every key in the product prefix on every poll.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

#: `-<n><unit>`; only past offsets, since a window into the future has no
#: meaning for data that already exists.
_OFFSET = re.compile(r"^-(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_TOKEN = re.compile(r"\{([^{}]*)\}")
#: token -> the granularity it implies.
_TOKEN_GRANULARITY = {"Y": "year", "m": "month", "d": "day", "j": "day", "H": "hour"}
TOKENS = frozenset(_TOKEN_GRANULARITY)
#: coarsest first, finest last.
GRANULARITIES = ("year", "month", "day", "hour")


class WindowError(ValueError):
    """A window or template the operator wrote cannot be used."""


@dataclass(frozen=True)
class Window:
    begin: dt.datetime
    end: dt.datetime


def parse_bound(value: str, now: dt.datetime) -> dt.datetime:
    """An RFC3339 timestamp, or a `-<n>[smhd]` offset from ``now``."""
    if not isinstance(value, str) or not value.strip():
        raise WindowError("a window bound must be a non-empty string")
    text = value.strip()

    match = _OFFSET.match(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - dt.timedelta(seconds=amount * _UNIT_SECONDS[unit])

    # A bare date parses under fromisoformat but is not RFC3339 (no time part)
    # and carries no offset — refuse it rather than guess a midnight.
    if "T" not in text:
        raise WindowError(f"window bound {value!r} is neither RFC3339 nor a -<n>[smhd] offset")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as err:
        raise WindowError(
            f"window bound {value!r} is neither RFC3339 nor a -<n>[smhd] offset"
        ) from err
    if parsed.tzinfo is None:
        raise WindowError(f"window bound {value!r} needs a timezone offset")
    return parsed.astimezone(dt.UTC)


def resolve_window(begin: str | None, end: str | None, now: dt.datetime) -> Window | None:
    """Resolve the configured bounds against ``now``; None when unconfigured."""
    if begin is None:
        if end is not None:
            raise WindowError("window.end without window.begin")
        return None
    resolved_begin = parse_bound(begin, now)
    resolved_end = parse_bound(end, now) if end is not None else now
    if resolved_begin >= resolved_end:
        raise WindowError(
            f"window.begin ({resolved_begin.isoformat()}) is not before "
            f"window.end ({resolved_end.isoformat()})"
        )
    return Window(begin=resolved_begin, end=resolved_end)


def in_window(window: Window, mtime: float) -> bool:
    """Half-open `[begin, end)`.

    Half-open so consecutive backfill windows tile without double-ingesting
    the object that sits exactly on the seam.
    """
    moment = dt.datetime.fromtimestamp(mtime, dt.UTC)
    return window.begin <= moment < window.end


def validate_template(template: str) -> None:
    names = _TOKEN.findall(template or "")
    unknown = [n for n in names if n not in TOKENS]
    if unknown:
        raise WindowError(f"path_template has unknown token(s) {unknown}; known: {sorted(TOKENS)}")
    if not names:
        raise WindowError("path_template must contain at least one date token, e.g. {Y}/{j}/{H}/")


def _granularity(template: str) -> str:
    present = {_TOKEN_GRANULARITY[n] for n in _TOKEN.findall(template)}
    for candidate in reversed(GRANULARITIES):
        if candidate in present:
            return candidate
    raise WindowError("path_template contains no date token")  # pragma: no cover


def _floor(moment: dt.datetime, granularity: str) -> dt.datetime:
    if granularity == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "month":
        return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return moment.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _step(moment: dt.datetime, granularity: str) -> dt.datetime:
    if granularity == "hour":
        return moment + dt.timedelta(hours=1)
    if granularity == "day":
        return moment + dt.timedelta(days=1)
    if granularity == "month":
        return (
            moment.replace(year=moment.year + 1, month=1)
            if moment.month == 12
            else moment.replace(month=moment.month + 1)
        )
    return moment.replace(year=moment.year + 1)


def _render(template: str, moment: dt.datetime) -> str:
    values = {
        "Y": f"{moment.year:04d}",
        "m": f"{moment.month:02d}",
        "d": f"{moment.day:02d}",
        "j": f"{moment.timetuple().tm_yday:03d}",
        "H": f"{moment.hour:02d}",
    }
    return _TOKEN.sub(lambda m: values[m.group(1)], template)


def expand_prefixes(template: str, window: Window, *, max_prefixes: int) -> tuple[str, ...]:
    """Every rendered prefix the window touches, in chronological order.

    Refuses rather than truncates when the expansion is too large: silently
    listing the first N prefixes would make a too-wide window look like it
    worked while quietly ignoring most of its range.
    """
    validate_template(template)
    granularity = _granularity(template)

    prefixes: list[str] = []
    seen: set[str] = set()
    moment = _floor(window.begin, granularity)
    while moment < window.end:
        rendered = _render(template, moment)
        if rendered not in seen:
            seen.add(rendered)
            prefixes.append(rendered)
            if len(prefixes) > max_prefixes:
                raise WindowError(
                    f"window expands to more than max_prefixes ({max_prefixes}) "
                    f"{granularity} prefixes — narrow the window or use a coarser "
                    "template"
                )
        moment = _step(moment, granularity)
    return tuple(prefixes)
