"""Typed view over an ingest association's ``config`` jsonb (ROADMAP §5.1).

This is the Python side of the cross-runtime contract: the app writes the config
through ``app/src/lib/associations/schemas.ts`` (Zod, which applies every
default), and the pipeline reads the same JSON back out of
``collection_connections.config``. We re-apply the §5.1 defaults here so the
pipeline is robust even if a row predates a default or was written by hand — the
field names and default values MUST NOT drift from the Zod schema.

Only the fields the poll → DISCOVER → GROUP → FETCH chain needs are modelled as
typed attributes; ``metadata`` (an EXTRACT-stage concern, Slice B4) is carried
through as a raw dict.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Defaults mirror app/src/lib/associations/schemas.ts (§5.1). Keep in sync.
DEFAULT_POLL_FREQUENCY_SECONDS = 300
DEFAULT_GROUPING_TIMEOUT_SECONDS = 900
GROUPING_RULES = ("none", "shared_basename")
ON_TIMEOUT = ("ingest_partial", "discard")
STORAGE_MODES = ("copy", "reference")


class IngestConfigError(ValueError):
    """The stored ``config`` jsonb is not a usable ingest config."""


@dataclass(frozen=True)
class Grouping:
    rule: str = "none"
    timeout_seconds: int = DEFAULT_GROUPING_TIMEOUT_SECONDS
    on_timeout: str = "ingest_partial"


@dataclass(frozen=True)
class IngestConfig:
    source_path: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    poll_frequency_seconds: int = DEFAULT_POLL_FREQUENCY_SECONDS
    storage_mode: str = "copy"
    grouping: Grouping = field(default_factory=Grouping)
    post_ingest: str = "leave"
    #: EXTRACT-stage config (Slice B4) — carried through untouched.
    metadata: dict[str, Any] = field(default_factory=dict)


def _str_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise IngestConfigError("glob list must be an array of strings")
    return tuple(str(item) for item in raw)


def _enum(raw: Any, allowed: Sequence[str], default: str, field_name: str) -> str:
    if raw is None:
        return default
    value = str(raw)
    if value not in allowed:
        raise IngestConfigError(f"{field_name} must be one of {allowed}, got {value!r}")
    return value


def parse_ingest_config(raw: dict[str, Any]) -> IngestConfig:
    """Parse a ``collection_connections.config`` dict into an :class:`IngestConfig`.

    Raises :class:`IngestConfigError` when a required field is missing/invalid.
    Optional fields fall back to the §5.1 defaults.
    """
    source_path = raw.get("source_path")
    if not isinstance(source_path, str) or not source_path.strip():
        raise IngestConfigError("source_path is required")

    grouping_raw = raw.get("grouping") or {}
    grouping = Grouping(
        rule=_enum(grouping_raw.get("rule"), GROUPING_RULES, "none", "grouping.rule"),
        timeout_seconds=int(
            grouping_raw.get("timeout_seconds", DEFAULT_GROUPING_TIMEOUT_SECONDS)
        ),
        on_timeout=_enum(
            grouping_raw.get("on_timeout"), ON_TIMEOUT, "ingest_partial", "grouping.on_timeout"
        ),
    )

    poll = int(raw.get("poll_frequency_seconds", DEFAULT_POLL_FREQUENCY_SECONDS))
    metadata = raw.get("metadata")
    return IngestConfig(
        source_path=source_path,
        include=_str_list(raw.get("include")),
        exclude=_str_list(raw.get("exclude")),
        poll_frequency_seconds=poll,
        storage_mode=_enum(raw.get("storage_mode"), STORAGE_MODES, "copy", "storage_mode"),
        grouping=grouping,
        post_ingest=str(raw.get("post_ingest", "leave")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


# --------------------------------------------------------------------------- #
# glob matching (include / exclude), evaluated against source-relative paths
# --------------------------------------------------------------------------- #


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob to a regex.

    ``**`` matches across path segments (``**/`` also matches zero leading
    segments), ``*`` matches within a segment, ``?`` one non-slash char. This
    covers the §5.1 examples (``**/*.tif``, ``**/*.tmp``) without pulling in a
    glob dependency; ``fnmatch`` alone can't express ``**``.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i + 1 : i + 2] == "*":
                if pattern[i + 2 : i + 3] == "/":
                    out.append("(?:.*/)?")  # any leading dirs, or none
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_matches(relpath: str, include: Iterable[str], exclude: Iterable[str]) -> bool:
    """True if ``relpath`` (source-relative) survives the include/exclude globs.

    Empty ``include`` includes everything; any ``exclude`` match drops the path.
    Exclude is evaluated after include, so an excluded file is never ingested
    even if it also matched an include.
    """
    include = list(include)
    if include and not any(_glob_to_regex(p).match(relpath) for p in include):
        return False
    return not any(_glob_to_regex(p).match(relpath) for p in exclude)
