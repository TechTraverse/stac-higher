# W-1 · Ingest Date Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator can point an ingest association at a huge date-partitioned bucket and say "keep up with the last six hours, no more than 200 files a poll" — and the pipeline lists only the prefixes that window touches.

**Architecture:** All the arithmetic lives in one new pure module, `ingest/window.py`: parsing `-6h`, resolving a window against a supplied `now`, and expanding a key template into the prefixes to list. Nothing in it does I/O or reads a clock, so it is fully testable. DISCOVER then changes in three small ways — list each expanded prefix instead of one, drop entries whose modified time falls outside the window, and stop admitting new files once the per-poll cap is reached. The three config fields are a cross-runtime contract, so the Zod schema, the Python reader and the golden fixture move together.

**Tech Stack:** Python 3.12 (stdlib `datetime`/`re` only), pytest; Zod v4, React, vitest.

**Spec:** `docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md` §3, §5, §6.

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/w1-window -b ai/w1-window ai/main`; `npm install` at the worktree root; `cd services/pipeline && uv sync --extra dev`.
- Gates: `npm run verify` (repo root) and, from `services/pipeline/`, `uv run pytest` + `uv run ruff check .`.
- **All three fields are optional and default to today's behaviour.** Every existing association must parse and behave identically after this lands — the fixture's `defaults` document is what pins that.
- A new or changed cross-runtime shape ⇒ update `tests/contract-fixtures/ingest-config.json`, which BOTH suites consume.
- `ingest/window.py` takes `now` as a parameter everywhere. No `datetime.now()` inside it.
- Times are UTC throughout. `FileEntry.mtime` is epoch seconds (`float | None`).
- Never edit `app/src/components/ui/*` (hook-blocked). The PostToolUse hook runs `astro check` after `.ts`/`.tsx` edits — fix what it reports.
- Commit messages end with the session's attribution trailer.

---

### Task 1: `ingest/window.py` — bounds, windows, prefixes

**Files:**
- Create: `services/pipeline/src/pipeline/ingest/window.py`
- Create: `services/pipeline/tests/test_ingest_window.py`

**Interfaces:**
- Produces:
  - `class WindowError(ValueError)`
  - `@dataclass(frozen=True) class Window: begin: dt.datetime; end: dt.datetime`
  - `parse_bound(value: str, now: dt.datetime) -> dt.datetime` — RFC3339 or `-<n>[smhd]`
  - `resolve_window(begin: str | None, end: str | None, now: dt.datetime) -> Window | None` — `None` when `begin` is None
  - `in_window(window: Window, mtime: float) -> bool` — half-open `[begin, end)`
  - `validate_template(template: str) -> None`
  - `expand_prefixes(template: str, window: Window, *, max_prefixes: int) -> tuple[str, ...]`
  - `TOKENS: frozenset[str]`, `GRANULARITIES: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

`services/pipeline/tests/test_ingest_window.py`:

```python
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
    assert parse_bound("2026-08-01T00:00:00Z", NOW) == dt.datetime(
        2026, 8, 1, tzinfo=dt.UTC
    )
    # A non-UTC offset is accepted and converted, not rejected.
    assert parse_bound("2026-08-01T02:00:00+02:00", NOW) == dt.datetime(
        2026, 8, 1, tzinfo=dt.UTC
    )


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
    assert window == Window(
        begin=dt.datetime(2026, 9, 2, 12, 30, 45, tzinfo=dt.UTC), end=NOW
    )


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
    assert in_window(window, end) is False       # end is exclusive
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
    window = resolve_window("-3h", None, NOW)     # 15:30 .. 18:30
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
    window = resolve_window(
        "2026-01-30T22:00:00Z", "2026-02-01T01:00:00Z", NOW
    )
    assert expand_prefixes("{Y}/{m}/{d}/", window, max_prefixes=100) == (
        "2026/01/30/",
        "2026/01/31/",
        "2026/02/01/",
    )


def test_expansion_rolls_over_a_year_boundary():
    window = resolve_window(
        "2025-12-31T23:00:00Z", "2026-01-01T01:00:00Z", NOW
    )
    assert expand_prefixes("{Y}/{j}/{H}/", window, max_prefixes=100) == (
        "2025/365/23/",
        "2026/001/00/",
        "2026/001/01/",
    )


def test_expansion_over_the_ceiling_is_refused_rather_than_truncated():
    window = resolve_window("-90d", None, NOW)
    with pytest.raises(WindowError, match="max_prefixes"):
        expand_prefixes("{Y}/{j}/{H}/", window, max_prefixes=1000)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_window.py -q`
Expected: FAIL with `ModuleNotFoundError: pipeline.ingest.window`.

- [ ] **Step 3: Implement**

`services/pipeline/src/pipeline/ingest/window.py`:

```python
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
#: token -> the granularity it implies, finest last.
_TOKEN_GRANULARITY = {"Y": "year", "m": "month", "d": "day", "j": "day", "H": "hour"}
TOKENS = frozenset(_TOKEN_GRANULARITY)
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

    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as err:
        raise WindowError(
            f"window bound {value!r} is neither RFC3339 nor a -<n>[smhd] offset"
        ) from err
    if parsed.tzinfo is None:
        raise WindowError(f"window bound {value!r} needs a timezone offset")
    return parsed.astimezone(dt.UTC)


def resolve_window(
    begin: str | None, end: str | None, now: dt.datetime
) -> Window | None:
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
        raise WindowError(
            f"path_template has unknown token(s) {unknown}; known: "
            f"{sorted(TOKENS)}"
        )
    if not names:
        raise WindowError(
            "path_template must contain at least one date token, e.g. {Y}/{j}/{H}/"
        )


def _granularity(template: str) -> str:
    names = _TOKEN.findall(template)
    present = {_TOKEN_GRANULARITY[n] for n in names}
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


def expand_prefixes(
    template: str, window: Window, *, max_prefixes: int
) -> tuple[str, ...]:
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_window.py -q && uv run ruff check .`
Expected: PASS. If the year-rollover test disagrees on day-of-year, check whether 2025 is a leap year (it is not — 2025-12-31 is day 365) before changing the implementation.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/ingest/window.py services/pipeline/tests/test_ingest_window.py
git commit -m "feat(ingest): window bounds, membership and prefix expansion (W-1)"
```

---

### Task 2: The three config fields, both runtimes, one fixture

**Files:**
- Modify: `app/src/lib/associations/schemas.ts` (`ingestConfigSchema`)
- Modify: `services/pipeline/src/pipeline/ingest/config.py` (`IngestConfig`, `parse_ingest_config`)
- Modify: `services/pipeline/src/pipeline/config.py` (`INGEST_MAX_WINDOW_PREFIXES`)
- Modify: `tests/contract-fixtures/ingest-config.json`
- Test: both contract suites pick the fixture up automatically

**Interfaces:**
- Zod: `window: z.object({ begin: z.string().min(1), end: z.string().min(1).nullable().default(null) }).strict().optional()`, `path_template: z.string().min(1).optional()`, `max_files_per_poll: z.number().int().min(1).optional()`. A `superRefine` rejects `path_template` without `window`.
- Python: `IngestConfig` gains `window_begin: str | None`, `window_end: str | None`, `path_template: str | None`, `max_files_per_poll: int | None`. The parser validates the template eagerly (`validate_template`) so a bad one fails at parse, not mid-listing.
- `Settings.ingest_max_window_prefixes: int = 1000` from `INGEST_MAX_WINDOW_PREFIXES`.

- [ ] **Step 1: Fixture first**

In `tests/contract-fixtures/ingest-config.json`, leave `minimal` and `defaults` **unchanged** — the three fields are optional and absent by default, which is the compatibility promise. Append cases:

```json
    { "name": "rolling window, open end",
      "config": { "source_path": "/out", "window": { "begin": "-6h" } },
      "app": "accept", "pipeline": "accept" },
    { "name": "absolute backfill window",
      "config": { "source_path": "/out", "window": { "begin": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z" } },
      "app": "accept", "pipeline": "accept" },
    { "name": "window with a lagging end (skip partially-landed objects)",
      "config": { "source_path": "/out", "window": { "begin": "-2h", "end": "-10m" } },
      "app": "accept", "pipeline": "accept" },
    { "name": "window.end without begin",
      "config": { "source_path": "/out", "window": { "end": "-10m" } },
      "app": "reject", "pipeline": "reject" },
    { "name": "unparseable bound",
      "config": { "source_path": "/out", "window": { "begin": "yesterday" } },
      "app": "accept", "pipeline": "reject" },
    { "name": "path_template with a window",
      "config": { "source_path": "ABI-L2-MCMIPC/", "path_template": "{Y}/{j}/{H}/", "window": { "begin": "-6h" } },
      "app": "accept", "pipeline": "accept" },
    { "name": "path_template without a window has nothing to expand",
      "config": { "source_path": "/out", "path_template": "{Y}/{j}/" },
      "app": "reject", "pipeline": "reject" },
    { "name": "path_template with no date token",
      "config": { "source_path": "/out", "path_template": "static/", "window": { "begin": "-6h" } },
      "app": "reject", "pipeline": "reject" },
    { "name": "path_template with an unknown token",
      "config": { "source_path": "/out", "path_template": "{Y}/{doy}/", "window": { "begin": "-6h" } },
      "app": "reject", "pipeline": "reject" },
    { "name": "per-poll cap",
      "config": { "source_path": "/out", "max_files_per_poll": 200 },
      "app": "accept", "pipeline": "accept" },
    { "name": "per-poll cap below one",
      "config": { "source_path": "/out", "max_files_per_poll": 0 },
      "app": "reject", "pipeline": "reject" }
```

Note the deliberate asymmetry on "unparseable bound": Zod checks the *shape*, the pipeline checks the *grammar*. Extend the file's `$comment` with one sentence naming the three fields and that split.

- [ ] **Step 2: Run both contract suites to see them fail**

Run: `cd app && npx vitest run src/__tests__/contract-fixtures.test.ts`
Run: `cd services/pipeline && uv run pytest tests/test_contract_fixtures.py -q`
Expected: both FAIL — the new accept cases are rejected by `.strict()` / the lenient reader ignores what it should validate.

- [ ] **Step 3: Implement the Zod side**

In `ingestConfigSchema`, before `storage_mode`:

```ts
    // W-1: what comes IN. `begin`/`end` are each an RFC3339 timestamp or a
    // `-<n>[smhd]` offset, re-resolved every poll — that is what makes a
    // rolling window roll. The GRAMMAR is checked pipeline-side (one parser,
    // one set of rules); this gate checks the shape.
    window: z
      .object({
        begin: z.string().min(1, "window.begin is required"),
        end: z.string().min(1).nullable().default(null),
      })
      .strict()
      .optional(),
    // Expands the window into the key prefixes worth listing, so the LISTING
    // is bounded and not just its result. Tokens: {Y} {m} {d} {j} {H}.
    path_template: z.string().min(1).optional(),
    // Caps how many NEW files one poll admits, oldest first — a wide window
    // becomes a paced backfill instead of a stampede.
    max_files_per_poll: z.number().int().min(1).optional(),
```

Add to the existing `superRefine`:

```ts
    if (cfg.path_template !== undefined && cfg.window === undefined) {
      ctx.addIssue({
        code: "custom",
        path: ["path_template"],
        message:
          "path_template needs a window to expand — set window.begin, or drop " +
          "the template and scope source_path instead",
      });
    }
```

- [ ] **Step 4: Implement the Python side**

`ingest/config.py` — add to `IngestConfig`:

```python
    #: W-1 (spec §3). Raw strings, resolved against `now` on every poll by
    #: `ingest.window.resolve_window` — storing them resolved would freeze a
    #: rolling window at the moment its config was parsed.
    window_begin: str | None = None
    window_end: str | None = None
    path_template: str | None = None
    max_files_per_poll: int | None = None
```

and in `parse_ingest_config`, before the return:

```python
    window_raw = raw.get("window") or {}
    if not isinstance(window_raw, dict):
        raise IngestConfigError("window must be an object")
    window_begin = window_raw.get("begin")
    window_end = window_raw.get("end")
    if window_end is not None and window_begin is None:
        raise IngestConfigError("window.end without window.begin")

    path_template = raw.get("path_template")
    if path_template is not None:
        if not isinstance(path_template, str) or not path_template.strip():
            raise IngestConfigError("path_template must be a non-empty string")
        if window_begin is None:
            raise IngestConfigError("path_template needs a window to expand")
        try:
            validate_template(path_template)
        except WindowError as err:
            raise IngestConfigError(str(err)) from err

    # Fail on a bad bound HERE, not on the first poll: a config that cannot
    # produce a window is a configuration error, and the association's writer
    # should learn about it at write time.
    if window_begin is not None:
        try:
            resolve_window(window_begin, window_end, dt.datetime.now(dt.UTC))
        except WindowError as err:
            raise IngestConfigError(str(err)) from err

    max_files = raw.get("max_files_per_poll")
    if max_files is not None:
        max_files = int(max_files)
        if max_files < 1:
            raise IngestConfigError("max_files_per_poll must be >= 1")
```

with `import datetime as dt` and `from pipeline.ingest.window import WindowError, resolve_window, validate_template` at the top. Pass the four new values into the `IngestConfig(...)` construction.

Note the one place this module does read the clock: validating that a bound *parses*. That is a config check, not a resolution — the resolved window is recomputed per poll in DISCOVER.

`pipeline/config.py`: add `DEFAULT_INGEST_MAX_WINDOW_PREFIXES = 1000`, the field, and `INGEST_MAX_WINDOW_PREFIXES` in `from_env`.

- [ ] **Step 5: Run both suites, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`
Run: `cd app && npm test && npm run check`

```bash
git add app/src/lib/associations/schemas.ts services/pipeline/src/pipeline/ingest/config.py services/pipeline/src/pipeline/config.py tests/contract-fixtures/ingest-config.json
git commit -m "feat(ingest): window, path_template and max_files_per_poll config (W-1)"
```

---

### Task 3: DISCOVER honours the window

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/discover.py` (`DiscoverResult`, `discover_stage`)
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py` (pass `settings` through if `discover_stage` needs the ceiling — see Step 3)
- Test: `services/pipeline/tests/test_ingest_discover.py`

**Interfaces:**
- `discover_stage(repo, association, config, adapter, *, now: dt.datetime | None = None, max_prefixes: int = 1000) -> DiscoverResult` — `now` defaults to `datetime.now(UTC)`; tests pass it.
- `DiscoverResult` gains `out_of_window: int`, `undateable: int`, `deferred_by_cap: int`, `prefixes_listed: int`.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_ingest_discover.py`:

```python
# --- date window (W-1) ----------------------------------------------------- #

import datetime as dt

NOW = dt.datetime(2026, 9, 2, 18, 30, tzinfo=dt.UTC)


def _at(hours_ago: float) -> float:
    return (NOW - dt.timedelta(hours=hours_ago)).timestamp()


async def test_entries_outside_the_window_are_skipped_and_counted():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "window": {"begin": "-2h"}})
    adapter = FakeAdapter(
        entries=[
            _entry("products/fresh.tif", etag="v1", mtime=_at(1)),
            _entry("products/stale.tif", etag="v2", mtime=_at(5)),
        ]
    )

    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert result.out_of_window == 1
    assert await repo.get_latest_ledger("assoc1", "fresh.tif") is not None
    assert await repo.get_latest_ledger("assoc1", "stale.tif") is None


async def test_an_entry_with_no_mtime_is_skipped_when_a_window_is_set():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "window": {"begin": "-2h"}})
    adapter = FakeAdapter(entries=[_entry("products/a.tif", etag="v1", mtime=None)])

    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert result.undateable == 1
    assert await repo.get_latest_ledger("assoc1", "a.tif") is None


async def test_an_entry_with_no_mtime_is_admitted_when_no_window_is_set():
    # The window is what makes a missing timestamp disqualifying; without one
    # the etag still fingerprints the file perfectly well.
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(entries=[_entry("products/a.tif", etag="v1", mtime=None)])

    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert result.undateable == 0
    assert await repo.get_latest_ledger("assoc1", "a.tif") is not None


async def test_the_template_lists_one_prefix_per_hour_in_the_window():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config(
        {
            "source_path": "ABI-L2-MCMIPC/",
            "path_template": "{Y}/{j}/{H}/",
            "window": {"begin": "-2h"},
        }
    )
    adapter = FakeAdapter(entries=[])

    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert adapter.list_calls == [
        "ABI-L2-MCMIPC/2026/245/16/",
        "ABI-L2-MCMIPC/2026/245/17/",
        "ABI-L2-MCMIPC/2026/245/18/",
    ]
    assert result.prefixes_listed == 3


async def test_without_a_template_the_source_path_is_listed_once():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "window": {"begin": "-2h"}})
    adapter = FakeAdapter(entries=[])

    await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert adapter.list_calls == ["products/"]


async def test_the_per_poll_cap_admits_the_oldest_first_and_defers_the_rest():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "max_files_per_poll": 2})
    adapter = FakeAdapter(
        entries=[
            _entry("products/c.tif", etag="c", mtime=_at(1)),
            _entry("products/a.tif", etag="a", mtime=_at(3)),
            _entry("products/b.tif", etag="b", mtime=_at(2)),
        ]
    )

    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert result.deferred_by_cap == 1
    assert await repo.get_latest_ledger("assoc1", "a.tif") is not None
    assert await repo.get_latest_ledger("assoc1", "b.tif") is not None
    assert await repo.get_latest_ledger("assoc1", "c.tif") is None


async def test_the_cap_does_not_count_files_already_in_the_ledger():
    # Otherwise a steady state of known files would starve new arrivals.
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "max_files_per_poll": 1})
    adapter = FakeAdapter(entries=[_entry("products/a.tif", etag="a", mtime=_at(2))])
    await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    adapter.entries.append(_entry("products/b.tif", etag="b", mtime=_at(1)))
    result = await discover_stage(repo, _assoc({}), cfg, adapter, now=NOW)

    assert await repo.get_latest_ledger("assoc1", "b.tif") is not None
    assert result.deferred_by_cap == 0
```

Check `_entry`'s signature in the file before use — it already accepts `mtime`. `FakeAdapter.entries` is a plain list, so the append in the last test works.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_discover.py -k "window or template or cap or mtime" -q`

- [ ] **Step 3: Implement**

In `discover.py`, extend `DiscoverResult`:

```python
    #: W-1 counters.
    out_of_window: int = 0
    #: listed, in-scope, but carrying no modified time to gate on.
    undateable: int = 0
    #: matched everything but was held back by max_files_per_poll.
    deferred_by_cap: int = 0
    prefixes_listed: int = 0
```

Replace the head of `discover_stage`:

```python
async def discover_stage(
    repo: IngestRepo,
    association: IngestAssociation,
    config: IngestConfig,
    adapter: StorageAdapter,
    *,
    now: dt.datetime | None = None,
    max_prefixes: int = DEFAULT_MAX_WINDOW_PREFIXES,
) -> DiscoverResult:
    """Reconcile the live listing against the ledger. Idempotent per file."""
    at = now or dt.datetime.now(dt.UTC)
    result = DiscoverResult()
    window = resolve_window(config.window_begin, config.window_end, at)

    # W-1: with a template, list only the prefixes the window touches; the
    # window otherwise filters AFTER a full listing, which bounds the result
    # but not the cost of getting it.
    if config.path_template and window is not None:
        prefixes = [
            f"{config.source_path}{suffix}"
            for suffix in expand_prefixes(
                config.path_template, window, max_prefixes=max_prefixes
            )
        ]
    else:
        prefixes = [config.source_path]

    entries: list[FileEntry] = []
    for prefix in prefixes:
        entries.extend(await adapter.list(prefix))
    result.prefixes_listed = len(prefixes)

    immediate = effective_settle(config, adapter.protocol) == "immediate"
```

Then, inside the loop, after the `path_matches` check and before `fingerprint_of`:

```python
        if window is not None:
            if entry.mtime is None:
                # Cannot be gated, so it is not admitted — the same posture the
                # stage takes for an unfingerprintable file. Counted and logged
                # so "my FTP source ingests nothing" is diagnosable.
                result.undateable += 1
                logger.warning(
                    "ingest discover: no modified time, cannot apply the window",
                    extra={"association_id": association.id, "source_path": relpath},
                )
                continue
            if not in_window(window, entry.mtime):
                result.out_of_window += 1
                continue
```

The cap needs the loop to see candidates in age order and to know which are new. Restructure the body: collect `(relpath, entry, fingerprint, latest)` tuples through the existing filters, then

```python
    # W-1: oldest first, so a backfill advances forward in time and the ledger
    # shows it progressing. Only NEW files count against the cap — a steady
    # state of known files must not starve new arrivals.
    candidates.sort(key=lambda c: (c.entry.mtime is None, c.entry.mtime or 0.0))
    admitted = 0
    for candidate in candidates:
        if (
            config.max_files_per_poll is not None
            and candidate.latest is None
            and admitted >= config.max_files_per_poll
        ):
            result.deferred_by_cap += 1
            continue
        if candidate.latest is None:
            admitted += 1
        await _reconcile(...)
```

Add the new counters to the closing log line's `extra`. Import `datetime as dt`, `FileEntry`, and `expand_prefixes` / `in_window` / `resolve_window` from `pipeline.ingest.window`. The default for the keyword comes from `pipeline.config`:

```python
from pipeline.config import DEFAULT_INGEST_MAX_WINDOW_PREFIXES
```

so the constant has exactly one definition (Task 2 added it) and the job's `settings.ingest_max_window_prefixes` overrides it per deployment.

In `jobs/ingest.py`'s `discover` handler, pass `max_prefixes=settings.ingest_max_window_prefixes`.

- [ ] **Step 4: Run everything, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`
Expected: PASS, including the pre-existing DISCOVER tests — none of them set a window, so none should change behaviour. If one does, that is a compatibility break and the implementation is wrong, not the test.

```bash
git add services/pipeline/src/pipeline/ingest/discover.py services/pipeline/src/pipeline/jobs/ingest.py services/pipeline/tests/test_ingest_discover.py
git commit -m "feat(ingest): DISCOVER honours the window, the template and the poll cap (W-1)"
```

---

### Task 4: The three fields in the ingest form

**Files:**
- Modify: `app/src/components/collections/IngestFormDialog.tsx`
- Test: the existing data-flow component test (`grep -l IngestFormDialog app/src/__tests__/*.tsx`); if none exists, create `app/src/__tests__/ingest-form-window.test.tsx`

**Interfaces:**
- `form` state gains `windowBegin`, `windowEnd`, `pathTemplate`, `maxFilesPerPoll` (all strings, `""` = unset). The config builder emits `window` only when `windowBegin` is non-empty, `end` only when `windowEnd` is, and omits the other two when blank.

- [ ] **Step 1: Failing test** — render the dialog, fill source path plus `-6h` and `{Y}/{j}/{H}/` and `200`, submit, and assert the payload carries `window: { begin: "-6h", end: null }`, `path_template`, `max_files_per_poll: 200`. A second case leaves all four blank and asserts none of the three keys appear at all. Copy the render/submit idiom from the existing data-flow tests.

- [ ] **Step 2: Implement**

Add a "Date window" fieldset after the poll-frequency field with three inputs (`Window begin`, `Window end`, `Path template`), and put `Max files per poll` beside the poll frequency. Match the surrounding `<Label>` + `<Input>` markup exactly; the ids follow the file's `df-` convention (`df-window-begin`, `df-window-end`, `df-path-template`, `df-max-files`). Help text, one sentence each: begin and end take a timestamp or an offset like `-6h`; the template expands the window into prefixes so only those are listed; the cap paces a wide window.

The part with real semantics is building the config — a blank field must be **absent**, never an empty string, or `.strict()` rejects it:

```ts
  const windowBegin = form.windowBegin.trim();
  const windowEnd = form.windowEnd.trim();
  const pathTemplate = form.pathTemplate.trim();
  const maxFiles = form.maxFilesPerPoll.trim();

  const config = {
    source_path: form.sourcePath.trim(),
    // …the existing fields, unchanged…
    ...(windowBegin
      ? { window: { begin: windowBegin, ...(windowEnd ? { end: windowEnd } : {}) } }
      : {}),
    ...(pathTemplate ? { path_template: pathTemplate } : {}),
    ...(maxFiles ? { max_files_per_poll: Number(maxFiles) } : {}),
  };
```

Seed the four fields from an existing association the way the others are (`c.window?.begin ?? ""` and friends) so editing does not silently drop them.

- [ ] **Step 3: Run, typecheck, commit**

```bash
git add app/src/components/collections/IngestFormDialog.tsx app/src/__tests__/
git commit -m "feat(data-flow): date window, path template and poll cap in the ingest form (W-1)"
```

---

### Task 5: Docs and verification

**Files:**
- Modify: `docs/FEATURES.md` (ingest row), the ingest config reference in `docs/connections.md`, `docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md` (status line → implemented for W-1)
- Modify: `TODO.md` (W queue entry — do not tick; the lead does)

- [ ] **Step 1:** Document the three fields with the NODD worked example from spec §3.2, including the measured before/after (one listing of ~250,000 keys versus six of ~12). State the §3.4 split explicitly: the window governs what comes in, retention governs what stays.

- [ ] **Step 2: Full verification**

Run from the worktree root: `npm run verify`
Run from `services/pipeline/`: `uv run pytest && uv run ruff check .`

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs(ingest): the date window, path template and poll cap (W-1)"
```

**Lead-only, needs the internet and Docker:** create an anonymous s3 connection to `noaa-goes19` with `source_path: "ABI-L2-MCMIPC/"`, `path_template: "{Y}/{j}/{H}/"`, `window: {begin: "-1h"}`, `max_files_per_poll: 4`, reference mode, and `metadata.defaults.datetime: "file_mtime"`. Expect three listings, ~12 files found, four admitted per poll. That is the spec §2 gate.
