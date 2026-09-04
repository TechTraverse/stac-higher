"""The built-in extractor registry (X queue design spec §5) — the Python half.

``tests/contract-fixtures/builtin-extractors.json`` is the single source of
truth for the curated stactools packages an operator can pick instead of
writing an extractor. The app validates it with
``app/src/lib/extractors/schemas.ts`` (strict — a typo in the registry fails
CI at review time); this reader is LENIENT in the established direction, so a
registry that grows a key cannot brick a pipeline image built before it.

The pipeline needs the registry for two things the app does not: dispatching
to the right adapter inside the stactools runtime image (X-2) and refusing a
run whose ``builtin_id`` is no longer in the registry (X-3/§9). Both come
later; this slice is the reader and the pin check.

**Packaging is deliberately not decided here.** ``parse_builtin_extractors``
takes a document. How the file reaches each image — a COPY, a mount, an env
override — is X-2's and X-4's call; neither build context includes ``tests/``
today, and guessing now would bake the wrong answer into the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pipeline.process.config import MAX_TIMEOUT_SECONDS, MIN_MEMORY_MB, NETWORK_LEVELS

#: Whether an entry's package builds an item from ONE staged file or needs the
#: whole group the ingest ``grouping.rule`` assembled (spec §3.6).
SUPPORTS = ("single_file", "grouped")
#: Whether the product has an anonymous public source. ``credentialed`` entries
#: ship import-smoked and unit-tested but with no live gate (I-105).
ACCESS = ("anonymous", "credentialed")

_PACKAGE_RE = re.compile(r"^stactools-[a-z0-9]+(?:-[a-z0-9]+)*$")
#: A CONCRETE pin, not a range — the image installs ``package==version`` and
#: the pin check compares the two literally.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}(?:(?:a|b|rc|post|dev)\d+)?$")
_ADAPTER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_PACKAGE_PREFIX = "stactools-"


class BuiltinRegistryError(ValueError):
    """A registry document the reader cannot use."""


@dataclass(frozen=True)
class BuiltinExtractor:
    id: str
    label: str
    package: str
    version: str
    adapter: str
    supports: str
    products: tuple[str, ...]
    access: str
    memory_mb: int
    timeout_seconds: int
    network_level: str
    extensions: tuple[str, ...] = field(default=())

    @property
    def module(self) -> str:
        """The package's import name — ``stactools-goes-glm`` →
        ``stactools.goes_glm``. The image's build-time smoke test imports
        exactly this for every entry (spec §6), so the derivation is part of
        the contract, not a convenience."""
        return f"stactools.{self.package[len(_PACKAGE_PREFIX):].replace('-', '_')}"

    @property
    def pin(self) -> str:
        """The ``package==version`` the runtime image must install."""
        return f"{self.package}=={self.version}"


def _obj(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BuiltinRegistryError(f"{where} must be an object")
    return raw


def _string(raw: Any, where: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BuiltinRegistryError(f"{where} must be a non-empty string")
    value = raw.strip()
    if pattern is not None and not pattern.match(value):
        raise BuiltinRegistryError(f"{where} is not in the expected form: {value!r}")
    return value


def _enum(raw: Any, allowed: tuple[str, ...], where: str) -> str:
    value = _string(raw, where)
    if value not in allowed:
        raise BuiltinRegistryError(f"{where} must be one of {', '.join(allowed)}")
    return value


def _strings(raw: Any, where: str, *, minimum: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise BuiltinRegistryError(f"{where} must be an array")
    values = tuple(_string(item, f"{where}[]") for item in raw)
    if len(values) < minimum:
        raise BuiltinRegistryError(f"{where} needs at least {minimum} entry")
    return values


def _int_in_range(raw: Any, where: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise BuiltinRegistryError(f"{where} must be a number")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BuiltinRegistryError(f"{where} must be a number") from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise BuiltinRegistryError(f"{where} must be {bound}")
    return value


def parse_builtin_extractor(raw: Any) -> BuiltinExtractor:
    doc = _obj(raw, "extractor")
    runtime = _obj(doc.get("runtime"), "extractor.runtime")
    network = _obj(runtime.get("network"), "extractor.runtime.network")
    return BuiltinExtractor(
        id=_string(doc.get("id"), "extractor.id", _ID_RE),
        label=_string(doc.get("label"), "extractor.label"),
        package=_string(doc.get("package"), "extractor.package", _PACKAGE_RE),
        version=_string(doc.get("version"), "extractor.version", _VERSION_RE),
        adapter=_string(doc.get("adapter"), "extractor.adapter", _ADAPTER_RE),
        supports=_enum(doc.get("supports"), SUPPORTS, "extractor.supports"),
        products=_strings(doc.get("products"), "extractor.products", minimum=1),
        access=_enum(doc.get("access"), ACCESS, "extractor.access"),
        memory_mb=_int_in_range(
            runtime.get("memory_mb"), "extractor.runtime.memory_mb", minimum=MIN_MEMORY_MB
        ),
        timeout_seconds=_int_in_range(
            runtime.get("timeout_seconds"),
            "extractor.runtime.timeout_seconds",
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        network_level=_enum(
            network.get("level"), NETWORK_LEVELS, "extractor.runtime.network.level"
        ),
        extensions=_strings(doc.get("extensions") or [], "extractor.extensions", minimum=0),
    )


def parse_builtin_extractors(raw: Any) -> tuple[BuiltinExtractor, ...]:
    """The registry document, keyed nowhere — order is the picker's order."""
    doc = _obj(raw, "registry")
    entries_raw = doc.get("extractors")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise BuiltinRegistryError("registry.extractors must be a non-empty array")
    entries = tuple(parse_builtin_extractor(entry) for entry in entries_raw)
    for attr in ("id", "adapter"):
        seen: set[str] = set()
        for entry in entries:
            value = getattr(entry, attr)
            if value in seen:
                raise BuiltinRegistryError(f"duplicate {attr} {value!r}")
            seen.add(value)
    return entries


# ---------------------------------------------------------------------------
# The image pin check (spec §5): the registry and Dockerfile.stactools cannot
# disagree about what is installed, or a run would dispatch to an adapter whose
# package is a different version than the registry claims.
# ---------------------------------------------------------------------------

#: A pip requirement line inside the Dockerfile — `stactools-goes==0.1.8`,
#: with or without a trailing line continuation or quoting.
_PIN_RE = re.compile(r"(stactools-[a-z0-9-]+)==([A-Za-z0-9.]+)")


def dockerfile_pins(text: str) -> dict[str, str]:
    """Every ``stactools-*==version`` pin in a Dockerfile, package → version.

    Comment lines are ignored so a commented-out pin cannot satisfy the check.
    """
    pins: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for package, version in _PIN_RE.findall(stripped):
            pins[package] = version
    return pins


def pin_drift(
    entries: tuple[BuiltinExtractor, ...] | list[BuiltinExtractor], dockerfile: str
) -> list[str]:
    """Differences between the registry and a Dockerfile's pins, as messages.

    An empty list means the image installs exactly what the registry claims.
    Extra ``stactools-*`` pins in the Dockerfile count as drift too: a package
    in the image with no registry entry is one the adapters cannot reach and
    the smoke test does not cover.
    """
    pins = dockerfile_pins(dockerfile)
    problems: list[str] = []
    for entry in entries:
        actual = pins.pop(entry.package, None)
        if actual is None:
            problems.append(f"{entry.package} is in the registry but not pinned in the image")
        elif actual != entry.version:
            problems.append(
                f"{entry.package} is pinned {actual} in the image, {entry.version} in the registry"
            )
    problems.extend(
        f"{package}=={version} is pinned in the image but has no registry entry"
        for package, version in sorted(pins.items())
    )
    return problems
