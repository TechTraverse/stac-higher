"""Cube sink config reader and ledger vocabulary (virtual cube spec sections 3.1, 3.2).

The LENIENT reader of ``stac_higher.cube_sinks.config``: unknown keys are
ignored and repeated names are de-duplicated. Everything that would make an
append wrong (an unknown parser, a window that cannot be applied,
``append_dim`` not loadable) is rejected. Pinned against the app's strict Zod
writer by ``tests/contract-fixtures/cube-sink-config.json``; the vocabulary by
``cube-append-status.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

APPEND_STATUSES = ("pending", "appended", "skipped", "failed")
TERMINAL_STATUSES = ("appended", "skipped", "failed")
SKIP_REASONS = (
    "late",
    "duplicate",
    "no_source_connection",
    "unsupported_layout",
    "source_missing",
    "no_datetime",
)

#: Reserved item id (ADR 0022): ``assets/{c}/_cube/`` is a cube repository's
#: prefix, so no item of this id may ever be GC-marked by item.
CUBE_ITEM_ID = "_cube"

PARSERS = ("hdf5",)
DEFAULT_ASSET_KEY = "cube"
MAX_NAMES = 64
MAX_STEPS = 10_000
MAX_AGE_LIMIT_SECONDS = 30 * 86_400

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3_600, "d": 86_400}
_ASSET_KEY = re.compile(r"^[a-z0-9_-]{1,32}$")


class CubeSinkConfigError(ValueError):
    """A cube sink config the pipeline cannot run."""


@dataclass(frozen=True)
class CubeWindow:
    max_steps: int | None
    max_age_seconds: int | None


@dataclass(frozen=True)
class CubeSinkConfig:
    parser: str
    append_dim: str
    variables: tuple[str, ...]
    loadable_variables: tuple[str, ...]
    asset_key: str
    window: CubeWindow | None
    on_late: str


def parse_duration(value: Any) -> int:
    """``^\\d+[mhd]$`` to seconds, above zero and at most 30 days."""
    match = _DURATION.match(value) if isinstance(value, str) else None
    if match is None:
        raise CubeSinkConfigError("window.max_age must look like 24h (^\\d+[mhd]$)")
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if not 0 < seconds <= MAX_AGE_LIMIT_SECONDS:
        raise CubeSinkConfigError("window.max_age must be above zero and at most 30d")
    return seconds


def _names(raw: Any, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise CubeSinkConfigError(f"{field} must be a non-empty list")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise CubeSinkConfigError(f"{field} entries must be non-blank strings")
        if value.strip() not in names:
            names.append(value.strip())
    if len(names) > MAX_NAMES:
        raise CubeSinkConfigError(f"{field} allows at most {MAX_NAMES} names")
    return tuple(names)


def _window(raw: Any) -> CubeWindow | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CubeSinkConfigError("window must be an object")
    steps = raw.get("max_steps")
    if steps is not None and (
        isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS
    ):
        raise CubeSinkConfigError(f"window.max_steps must be an integer in 1..{MAX_STEPS}")
    age = raw.get("max_age")
    age_seconds = parse_duration(age) if age is not None else None
    if steps is None and age_seconds is None:
        raise CubeSinkConfigError("window needs max_steps, max_age or both")
    return CubeWindow(max_steps=steps, max_age_seconds=age_seconds)


def parse_cube_sink_config(raw: Any) -> CubeSinkConfig:
    if not isinstance(raw, dict):
        raise CubeSinkConfigError("cube sink config must be an object")
    parser = raw.get("parser", "hdf5")
    if parser not in PARSERS:
        raise CubeSinkConfigError(f"unsupported parser {parser!r}")
    append_dim = raw.get("append_dim")
    if not isinstance(append_dim, str) or not append_dim.strip():
        raise CubeSinkConfigError("append_dim is required")
    append_dim = append_dim.strip()
    variables = _names(raw.get("variables"), "variables")
    loadable = _names(raw.get("loadable_variables"), "loadable_variables")
    if append_dim not in loadable:
        raise CubeSinkConfigError("loadable_variables must include append_dim")
    asset_key = raw.get("asset_key", DEFAULT_ASSET_KEY)
    if not isinstance(asset_key, str) or not _ASSET_KEY.match(asset_key):
        raise CubeSinkConfigError("asset_key must match [a-z0-9_-]{1,32}")
    on_late = raw.get("on_late", "skip")
    if on_late != "skip":
        raise CubeSinkConfigError("on_late must be 'skip'")
    return CubeSinkConfig(
        parser=parser,
        append_dim=append_dim,
        variables=variables,
        loadable_variables=loadable,
        asset_key=asset_key,
        window=_window(raw.get("window")),
        on_late=on_late,
    )
