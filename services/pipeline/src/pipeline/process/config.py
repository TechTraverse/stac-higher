"""Typed views over the Phase 9 process jsonb columns (ROADMAP §5.6).

Python side of the cross-runtime contract: the app writes these documents
through ``app/src/lib/processes/schemas.ts`` (Zod, strict, defaults applied)
and the pipeline reads the same JSON back out of ``process_sources.trigger`` /
``.expectation`` and ``process_revisions.runtime`` / ``.env``. Field names and
default values MUST NOT drift; the golden fixtures in
``tests/contract-fixtures/process-{trigger,runtime,env,expectation}.json`` keep
both sides honest and are consumed by both suites.

Lenient-reader semantics, matching every other parser here: unknown keys are
ignored and numbers are coerced, but a present-and-unusable value raises —
a silently mis-read trigger or limit would run the wrong thing, or nothing,
invisibly.

**The slice-1 asymmetry is deliberate**: ``runtime.kind == "container"`` parses
FINE here while the app's write gate refuses it (design spec §4). The contract
carries the arm so nothing is foreclosed; the accreditation-scope decision is
enforced at the single place that stores revisions, not duplicated here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: Trigger kinds the §5.6 shape admits.
TRIGGER_KINDS = ("item_event", "cron")
#: Runtime kinds the §5.6 shape admits (see the module docstring on
#: ``container``).
RUNTIME_KINDS = ("inline_python", "container")
BACKOFF = ("exponential", "fixed")

#: Mirrors ``processRuntimeSchema``'s bounds. The memory floor leaves room for
#: the interpreter itself; the timeout ceiling bounds how long a wedged run can
#: hold a container slot (ADR 0013 — a run is not a service).
MIN_MEMORY_MB = 128
MAX_TIMEOUT_SECONDS = 86_400

DEFAULT_MEMORY_MB = 512
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3

#: Structural five-field cron, matching ``cronScheduleSchema``. Deliberately
#: not semantic: the scheduler is the authority on whether a structurally valid
#: schedule ever fires.
_CRON_FIELD = r"(?:\*|[0-9a-zA-Z]+(?:-[0-9a-zA-Z]+)?)(?:/\d+)?"
_CRON_LIST = rf"{_CRON_FIELD}(?:,{_CRON_FIELD})*"
_CRON_RE = re.compile(rf"^{_CRON_LIST}(?: {_CRON_LIST}){{4}}$")

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProcessConfigError(ValueError):
    """A stored process jsonb document is not a usable §5.6 shape."""


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _obj(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProcessConfigError(f"{what} must be an object, got {raw!r}")
    return raw


def _kind(raw: dict[str, Any], allowed: Sequence[str], what: str) -> str:
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in allowed:
        raise ProcessConfigError(f"{what}.kind must be one of {allowed}, got {kind!r}")
    return kind


def _int_in_range(
    raw: Any, field_name: str, *, default: int, minimum: int, maximum: int | None = None
) -> int:
    """Coerce a number to int (truncating, the lenient-reader direction) and
    bound it. Booleans are rejected outright — ``True`` is an ``int`` in Python
    and would silently become ``1``."""
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProcessConfigError(f"{field_name} must be a number, got {raw!r}")
    value = int(raw)
    if value < minimum:
        raise ProcessConfigError(f"{field_name} must be >= {minimum}, got {raw!r}")
    if maximum is not None and value > maximum:
        raise ProcessConfigError(f"{field_name} must be <= {maximum}, got {raw!r}")
    return value


def _non_blank(raw: Any, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProcessConfigError(f"{field_name} must be a non-empty string, got {raw!r}")
    return raw.strip()


# ---------------------------------------------------------------------------
# trigger (process_sources.trigger)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessTrigger:
    kind: str
    #: item_event only — optional CQL2 subset, evaluated on the same path as a
    #: delivery association's item_filter. NOT compiled here (ISSUES I-41).
    item_filter: str | None = None
    #: cron only — five-field, minute-granular.
    schedule: str | None = None


def parse_process_trigger(raw: Any) -> ProcessTrigger:
    doc = _obj(raw, "trigger")
    kind = _kind(doc, TRIGGER_KINDS, "trigger")

    if kind == "item_event":
        item_filter = doc.get("item_filter")
        if item_filter is not None:
            item_filter = _non_blank(item_filter, "trigger.item_filter")
        return ProcessTrigger(kind=kind, item_filter=item_filter)

    schedule = _non_blank(doc.get("schedule"), "trigger.schedule")
    if not _CRON_RE.match(schedule):
        raise ProcessConfigError(
            f"trigger.schedule must be a five-field cron expression, got {schedule!r}"
        )
    return ProcessTrigger(kind=kind, schedule=schedule)


# ---------------------------------------------------------------------------
# runtime (process_revisions.runtime)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessRuntime:
    kind: str
    #: container only — the user-supplied image reference. Always ``None`` for
    #: inline_python, whose code is mounted read-only into the platform
    #: executor image (spec §4).
    image: str | None = None
    memory_mb: int = DEFAULT_MEMORY_MB
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff: str = "exponential"


def parse_process_runtime(raw: Any) -> ProcessRuntime:
    doc = _obj(raw, "runtime")
    kind = _kind(doc, RUNTIME_KINDS, "runtime")

    image: str | None = None
    if kind == "container":
        image = _non_blank(doc.get("image"), "runtime.image")

    retry_raw = doc.get("retry")
    if retry_raw is None:
        retry_raw = {}
    retry = _obj(retry_raw, "runtime.retry")

    backoff = retry.get("backoff")
    if backoff is None:
        backoff = "exponential"
    if not isinstance(backoff, str) or backoff not in BACKOFF:
        raise ProcessConfigError(
            f"runtime.retry.backoff must be one of {BACKOFF}, got {backoff!r}"
        )

    return ProcessRuntime(
        kind=kind,
        image=image,
        memory_mb=_int_in_range(
            doc.get("memory_mb"),
            "runtime.memory_mb",
            default=DEFAULT_MEMORY_MB,
            minimum=MIN_MEMORY_MB,
        ),
        timeout_seconds=_int_in_range(
            doc.get("timeout_seconds"),
            "runtime.timeout_seconds",
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        max_attempts=_int_in_range(
            retry.get("max_attempts"),
            "runtime.retry.max_attempts",
            default=DEFAULT_MAX_ATTEMPTS,
            minimum=1,
        ),
        backoff=backoff,
    )


# ---------------------------------------------------------------------------
# env (process_revisions.env)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretRef:
    """A pointer into the §5.2 encrypted-credentials envelope: a named key
    inside a connection's write-only credentials blob."""

    connection_id: str
    key: str


@dataclass(frozen=True)
class EnvEntry:
    name: str
    #: Exactly one of these is set — the parser refuses both and neither.
    value: str | None = None
    secret_ref: SecretRef | None = None


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _secret_ref(raw: Any) -> SecretRef:
    doc = _obj(raw, "env.secret_ref")
    connection_id = doc.get("connection_id")
    if not isinstance(connection_id, str) or not _UUID_RE.match(connection_id):
        raise ProcessConfigError(
            f"env.secret_ref.connection_id must be a connection UUID, got {connection_id!r}"
        )
    return SecretRef(
        connection_id=connection_id, key=_non_blank(doc.get("key"), "env.secret_ref.key")
    )


def parse_process_env(raw: Any) -> tuple[EnvEntry, ...]:
    """Parse the §5.6 env envelope. Resolution of ``secret_ref`` entries is a
    LAUNCH-time concern (ADR 0013: only into the run container's environment,
    never the worker's own) — this reader only reads the pointers."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProcessConfigError(f"env must be an array of entries, got {raw!r}")

    entries: list[EnvEntry] = []
    seen: set[str] = set()
    for item in raw:
        doc = _obj(item, "env entry")
        name = doc.get("name")
        if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
            raise ProcessConfigError(
                f"env entry name must be a POSIX environment-variable name, got {name!r}"
            )
        if name in seen:
            raise ProcessConfigError(
                f"duplicate env name {name!r} — the resolved environment would be ambiguous"
            )
        seen.add(name)

        has_value = "value" in doc and doc["value"] is not None
        has_ref = "secret_ref" in doc and doc["secret_ref"] is not None
        if has_value and has_ref:
            raise ProcessConfigError(
                f"env entry {name!r} carries both a value and a secret_ref"
            )
        if not has_value and not has_ref:
            raise ProcessConfigError(
                f"env entry {name!r} needs either a value or a secret_ref"
            )

        if has_value:
            value = doc["value"]
            if not isinstance(value, str):
                raise ProcessConfigError(
                    f"env entry {name!r} value must be a string, got {value!r}"
                )
            entries.append(EnvEntry(name=name, value=value))
        else:
            entries.append(EnvEntry(name=name, secret_ref=_secret_ref(doc["secret_ref"])))

    return tuple(entries)


# ---------------------------------------------------------------------------
# expectation (process_sources.expectation)
# ---------------------------------------------------------------------------


def parse_process_expectation(raw: dict[str, Any] | None) -> int | None:
    """``run_within_seconds`` for a process source, or ``None`` when no
    expectation is declared (a quiet source can be entirely normal). Mirrors
    ``parse_ingest_expectation`` — see ``pipeline/flow/expectation.py``. A
    breach raises ``process_stalled`` (spec §8)."""
    if raw is None:
        return None
    doc = _obj(raw, "expectation")
    value = doc.get("run_within_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProcessConfigError(
            f"run_within_seconds must be a number of seconds, got {value!r}"
        )
    seconds = int(value)
    if seconds < 1:
        raise ProcessConfigError(f"run_within_seconds must be >= 1, got {value!r}")
    return seconds
