"""The registry as the RUNTIME image reads it.

``tests/contract-fixtures/builtin-extractors.json`` reaches the image as a
COPY from a named build context (``docker-bake.hcl``'s ``fixtures``) and its
path is published in ``STAC_HIGHER_BUILTIN_REGISTRY``; outside the image the
reader falls back to the repo checkout so the pipeline's pytest suite exercises
the same code. This reader is deliberately smaller than
``pipeline.process.builtin`` — the run needs an id → adapter map and the
import-name derivation, nothing else — and lenient in the same direction:
unknown keys are ignored. The derivation of ``module`` is part of the
contract (the pipeline suite asserts both readers agree on every entry).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_VAR = "STAC_HIGHER_BUILTIN_REGISTRY"
IMAGE_PATH = Path("/opt/stac-higher/builtin-extractors.json")
_PACKAGE_PREFIX = "stactools-"


class RegistryError(ValueError):
    """The registry could not be found or an entry is unusable."""


@dataclass(frozen=True)
class BuiltinExtractor:
    id: str
    package: str
    version: str
    adapter: str
    supports: str

    @property
    def module(self) -> str:
        """``stactools-goes-glm`` → ``stactools.goes_glm`` — the string the
        build-time smoke test imports, derived exactly as the pipeline's
        reader derives it."""
        return f"stactools.{self.package[len(_PACKAGE_PREFIX) :].replace('-', '_')}"

    @property
    def adapter_module(self) -> str:
        return f"stac_higher_stactools.adapters.{self.adapter}"


def registry_path() -> Path:
    """Where the registry is: the env override, the image path, then the
    repo checkout (for the pipeline's test suite)."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    if IMAGE_PATH.exists():
        return IMAGE_PATH
    repo = Path(__file__).resolve().parents[3]
    checkout = repo / "tests" / "contract-fixtures" / "builtin-extractors.json"
    if checkout.exists():
        return checkout
    raise RegistryError(
        f"no built-in extractor registry: set {ENV_VAR} or build the image with the "
        "`fixtures` build context (services/process-runtime/docker-bake.hcl)"
    )


def _string(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise RegistryError(f"{where} must be a non-empty string")
    return raw.strip()


def parse_registry(document: Any) -> tuple[BuiltinExtractor, ...]:
    if not isinstance(document, dict) or not isinstance(document.get("extractors"), list):
        raise RegistryError("registry.extractors must be an array")
    entries: list[BuiltinExtractor] = []
    for raw in document["extractors"]:
        if not isinstance(raw, dict):
            raise RegistryError("registry.extractors[] must be objects")
        package = _string(raw.get("package"), "extractor.package")
        if not package.startswith(_PACKAGE_PREFIX):
            raise RegistryError(f"extractor.package must start with {_PACKAGE_PREFIX!r}")
        entries.append(
            BuiltinExtractor(
                id=_string(raw.get("id"), "extractor.id"),
                package=package,
                version=_string(raw.get("version"), "extractor.version"),
                adapter=_string(raw.get("adapter"), "extractor.adapter"),
                supports=_string(raw.get("supports"), "extractor.supports"),
            )
        )
    ids = [entry.id for entry in entries]
    if len(set(ids)) != len(ids):
        raise RegistryError("registry has duplicate ids")
    return tuple(entries)


def load_registry(path: Path | None = None) -> tuple[BuiltinExtractor, ...]:
    where = path or registry_path()
    try:
        document = json.loads(Path(where).read_text())
    except (OSError, ValueError) as exc:
        raise RegistryError(f"could not read the registry at {where}: {exc}") from exc
    return parse_registry(document)


def find_entry(builtin_id: str, entries: tuple[BuiltinExtractor, ...]) -> BuiltinExtractor:
    for entry in entries:
        if entry.id == builtin_id:
            return entry
    known = ", ".join(entry.id for entry in entries)
    raise RegistryError(f"{builtin_id!r} is not a built-in extractor (registry has: {known})")
