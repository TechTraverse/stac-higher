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

**Three runtime kinds since C-1** (container-images spec §3, ADR 0021):
``inline_python``, ``inline_python_on_image`` and ``container``. The
snapshot ``image`` of kinds 2-3 is parsed here. Whether that image may
deploy is the app's DB-backed gate, and whether it may LAUNCH is the launch
path's (``launch.resolve_run_image``, the C-2 digest check).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.images.reference import is_image_digest, is_image_reference
from pipeline.process.hardware import (
    DEFAULT_HARDWARE_CPU,
    DEFAULT_HARDWARE_GPU_COUNT,
    DEFAULT_HARDWARE_PROFILE,
)

#: Trigger kinds the §5.6 shape admits.
TRIGGER_KINDS = ("item_event", "cron")
#: Runtime kinds the shape admits (C-1, container-images spec §3).
RUNTIME_KINDS = ("inline_python", "inline_python_on_image", "container")
#: Kinds that run on a scanned USER image, named by an immutable snapshot.
USER_IMAGE_KINDS = frozenset({"inline_python_on_image", "container"})
#: ``command`` (kind 3) overrides the image's Cmd; never Entrypoint or User.
MAX_COMMAND_ENTRIES = 64
BACKOFF = ("exponential", "fixed")

#: Mirrors ``processRuntimeSchema``'s bounds. The memory floor leaves room for
#: the interpreter itself; the timeout ceiling bounds how long a wedged run can
#: hold a container slot (ADR 0013 — a run is not a service).
MIN_MEMORY_MB = 128
MAX_TIMEOUT_SECONDS = 86_400

DEFAULT_MEMORY_MB = 512
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3

#: Network profile levels (GOES spec §4), ORDERED lowest first — the
#: PROCESS_NETWORK_MAX cap compares positions. Slice 1 realises only
#: ``isolated``; the others are carried so no revision changes meaning when
#: the egress proxy (spec §11) lands.
NETWORK_LEVELS = ("isolated", "inputs", "hosts", "open")
DEFAULT_NETWORK_LEVEL = "isolated"
#: Platform runtime image ALIASES (X-queue spec §8). An alias names one of the
#: platform-built images — never a user image (that is ``runtime.image``, the
#: scanned snapshot of kinds 2-3). The
#: launch path resolves an alias through settings (``PROCESS_RUNTIME_IMAGE``,
#: ``PROCESS_RUNTIME_IMAGE_STACTOOLS``) and a run whose alias has no image in
#: this deployment dies with a reason. Mirrors ``PROCESS_RUNTIME_IMAGE_ALIASES``
#: in ``app/src/lib/processes/schemas.ts``. Every revision stored before the
#: field existed reads as ``default``.
RUNTIME_IMAGE_ALIASES = ("default", "stactools")
DEFAULT_RUNTIME_IMAGE_ALIAS = "default"
#: A `hosts` entry is a bare hostname: no scheme, no port, no whitespace.
_HOST_FORBIDDEN = set("/: \t\n")

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


def _enum(raw: Any, allowed: Sequence[str], field_name: str, default: str | None = None) -> str:
    """Validate ``raw`` against ``allowed``; ``None`` yields ``default``, and a
    field with no default is required. Same rule as the ingest/delivery
    parsers' ``_enum`` — kept local until there is a shared reader module."""
    if raw is None and default is not None:
        return default
    if not isinstance(raw, str) or raw not in allowed:
        raise ProcessConfigError(f"{field_name} must be one of {allowed}, got {raw!r}")
    return raw


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
    kind = _enum(doc.get("kind"), TRIGGER_KINDS, "trigger.kind")

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
    #: Kinds 2-3 only: the immutable snapshot of a ``container_images`` row.
    #: ``None`` for inline_python.
    image_id: str | None = None
    image_reference: str | None = None
    image_digest: str | None = None
    #: Kind 3 only: overrides the image's Cmd. ``None`` means the image's own.
    command: tuple[str, ...] | None = None
    memory_mb: int = DEFAULT_MEMORY_MB
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff: str = "exponential"
    #: GOES spec §4 — one of NETWORK_LEVELS; `hosts` is non-empty iff the
    #: level is "hosts".
    network_level: str = DEFAULT_NETWORK_LEVEL
    network_hosts: tuple[str, ...] = ()
    #: X-queue spec §8: one of RUNTIME_IMAGE_ALIASES for inline_python;
    #: ``None`` for kinds 2-3, which run by digest.
    runtime_image: str | None = DEFAULT_RUNTIME_IMAGE_ALIAS
    #: K-1 (process-compute spec §4) — the `hardware` block, flattened like
    #: `network`: the profile id and the counts; bounds are checked at launch
    #: against the deployment's profile set (`check_hardware_bounds`).
    hardware_profile: str = DEFAULT_HARDWARE_PROFILE
    hardware_cpu: float = DEFAULT_HARDWARE_CPU
    hardware_gpu_count: int = DEFAULT_HARDWARE_GPU_COUNT


def _parse_network(raw: Any) -> tuple[str, tuple[str, ...]]:
    if raw is None:
        return DEFAULT_NETWORK_LEVEL, ()
    doc = _obj(raw, "runtime.network")
    level = _enum(
        doc.get("level"), NETWORK_LEVELS, "runtime.network.level", default=DEFAULT_NETWORK_LEVEL
    )
    hosts_raw = doc.get("hosts", [])
    if hosts_raw is None:
        hosts_raw = []
    if not isinstance(hosts_raw, list) or not all(isinstance(h, str) for h in hosts_raw):
        raise ProcessConfigError("runtime.network.hosts must be a list of strings")
    hosts = tuple(h.strip() for h in hosts_raw)
    if any(not h or _HOST_FORBIDDEN & set(h) for h in hosts):
        raise ProcessConfigError("runtime.network.hosts entries must be bare hostnames")
    if level == "hosts" and not hosts:
        raise ProcessConfigError("runtime.network.level 'hosts' requires a non-empty hosts list")
    if level != "hosts" and hosts:
        raise ProcessConfigError("runtime.network.hosts is only allowed with level 'hosts'")
    return level, hosts


_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _parse_hardware(raw: Any) -> tuple[str, float, int]:
    """Shape only — the profile set is not known here; `check_hardware_bounds`
    at launch does the rest. Absent reads as the defaults (every stored
    revision predates the block)."""
    if raw is None:
        return DEFAULT_HARDWARE_PROFILE, DEFAULT_HARDWARE_CPU, DEFAULT_HARDWARE_GPU_COUNT
    doc = _obj(raw, "runtime.hardware")
    profile = doc.get("profile")
    if not isinstance(profile, str) or not _PROFILE_ID_RE.match(profile):
        raise ProcessConfigError(f"runtime.hardware.profile must be a profile id, got {profile!r}")
    cpu_raw = doc.get("cpu")
    if isinstance(cpu_raw, bool) or not isinstance(cpu_raw, (int, float)) or cpu_raw <= 0:
        raise ProcessConfigError(f"runtime.hardware.cpu must be a positive number, got {cpu_raw!r}")
    gpu_raw = doc.get("gpu_count")
    if gpu_raw is not None and isinstance(gpu_raw, float) and not gpu_raw.is_integer():
        raise ProcessConfigError(f"runtime.hardware.gpu_count must be an integer, got {gpu_raw!r}")
    gpu_count = _int_in_range(
        gpu_raw, "runtime.hardware.gpu_count", default=DEFAULT_HARDWARE_GPU_COUNT, minimum=0
    )
    return profile, float(cpu_raw), gpu_count


def _parse_image_snapshot(raw: Any) -> tuple[str, str, str]:
    doc = _obj(raw, "runtime.image")
    image_id = doc.get("id")
    if not isinstance(image_id, str) or not _UUID_RE.match(image_id):
        raise ProcessConfigError(
            f"runtime.image.id must be a container image id, got {image_id!r}"
        )
    reference = doc.get("reference")
    if not isinstance(reference, str) or not is_image_reference(reference):
        raise ProcessConfigError(
            f"runtime.image.reference must be a normalized repository, got {reference!r}"
        )
    digest = doc.get("digest")
    if not isinstance(digest, str) or not is_image_digest(digest):
        raise ProcessConfigError(
            f"runtime.image.digest must be a sha256 digest, got {digest!r}"
        )
    return image_id, reference, digest


def _parse_command(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(arg, str) for arg in raw):
        raise ProcessConfigError("runtime.command must be a list of strings")
    if not raw:
        raise ProcessConfigError("runtime.command must be non-empty when present")
    if len(raw) > MAX_COMMAND_ENTRIES:
        raise ProcessConfigError(
            f"runtime.command carries at most {MAX_COMMAND_ENTRIES} entries"
        )
    if any(not arg.strip() for arg in raw):
        raise ProcessConfigError("runtime.command entries must be non-blank")
    return tuple(raw)


def parse_process_runtime(raw: Any) -> ProcessRuntime:
    doc = _obj(raw, "runtime")
    kind = _enum(doc.get("kind"), RUNTIME_KINDS, "runtime.kind")

    image_id = image_reference = image_digest = None
    command: tuple[str, ...] | None = None
    runtime_image: str | None
    if kind == "inline_python":
        if doc.get("image") is not None:
            raise ProcessConfigError(
                "runtime.image must be null for inline_python; use inline_python_on_image "
                "to run code on your own image"
            )
        runtime_image = _enum(
            doc.get("runtime_image"),
            RUNTIME_IMAGE_ALIASES,
            "runtime.runtime_image",
            default=DEFAULT_RUNTIME_IMAGE_ALIAS,
        )
    else:
        image_id, image_reference, image_digest = _parse_image_snapshot(doc.get("image"))
        if doc.get("runtime_image") is not None:
            raise ProcessConfigError(
                f"runtime.runtime_image must be null for {kind}: a platform alias and a user "
                "image cannot both name what runs"
            )
        runtime_image = None
        if kind == "container":
            command = _parse_command(doc.get("command"))

    retry_raw = doc.get("retry")
    if retry_raw is None:
        retry_raw = {}
    retry = _obj(retry_raw, "runtime.retry")
    network_level, network_hosts = _parse_network(doc.get("network"))
    hardware_profile, hardware_cpu, hardware_gpu_count = _parse_hardware(doc.get("hardware"))

    return ProcessRuntime(
        kind=kind,
        image_id=image_id,
        image_reference=image_reference,
        image_digest=image_digest,
        command=command,
        network_level=network_level,
        network_hosts=network_hosts,
        hardware_profile=hardware_profile,
        hardware_cpu=hardware_cpu,
        hardware_gpu_count=hardware_gpu_count,
        runtime_image=runtime_image,
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
        backoff=_enum(
            retry.get("backoff"), BACKOFF, "runtime.retry.backoff", default="exponential"
        ),
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


def _env_entry(item: Any) -> EnvEntry:
    """One entry: a POSIX name plus EXACTLY one of a literal value or a
    secret_ref. Both is refused rather than resolved by precedence — a
    plaintext secret beside a reference is a leak a precedence rule would
    quietly preserve."""
    doc = _obj(item, "env entry")
    name = doc.get("name")
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
        raise ProcessConfigError(
            f"env entry name must be a POSIX environment-variable name, got {name!r}"
        )

    value = doc.get("value")
    ref = doc.get("secret_ref")
    if value is not None and ref is not None:
        raise ProcessConfigError(f"env entry {name!r} carries both a value and a secret_ref")
    if value is None and ref is None:
        raise ProcessConfigError(f"env entry {name!r} needs either a value or a secret_ref")

    if ref is not None:
        return EnvEntry(name=name, secret_ref=_secret_ref(ref))
    if not isinstance(value, str):
        raise ProcessConfigError(f"env entry {name!r} value must be a string, got {value!r}")
    return EnvEntry(name=name, value=value)


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
        entry = _env_entry(item)
        if entry.name in seen:
            raise ProcessConfigError(
                f"duplicate env name {entry.name!r} — the resolved environment would be ambiguous"
            )
        seen.add(entry.name)
        entries.append(entry)

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
    if doc.get("run_within_seconds") is None:
        return None
    return _int_in_range(doc["run_within_seconds"], "run_within_seconds", default=0, minimum=1)
