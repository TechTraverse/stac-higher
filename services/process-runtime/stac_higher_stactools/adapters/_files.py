"""Helpers shared by the adapters: picking the file a package wants out of a
group, and rebuilding a directory layout the ingest flattened."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class AdapterInputError(ValueError):
    """The staged group does not contain what the package needs."""


def pick(paths: Mapping[str, Path], suffixes: Iterable[str], what: str) -> Path:
    """The single staged file whose name ends with one of ``suffixes``
    (case-insensitive). Exactly one, or the group is not what the registry
    entry promised."""
    wanted = tuple(s.lower() for s in suffixes)
    hits = [p for p in paths.values() if p.name.lower().endswith(wanted)]
    if len(hits) != 1:
        names = ", ".join(sorted(p.name for p in paths.values())) or "nothing"
        raise AdapterInputError(
            f"expected exactly one {what} in the group, found {len(hits)} ({names})"
        )
    return hits[0]


def pick_optional(paths: Mapping[str, Path], suffixes: Iterable[str]) -> Path | None:
    wanted = tuple(s.lower() for s in suffixes)
    hits = [p for p in paths.values() if p.name.lower().endswith(wanted)]
    return hits[0] if len(hits) == 1 else None


def href_of(draft: Mapping[str, Any], key: str) -> str:
    assets = draft.get("assets") or {}
    asset = assets.get(key) if isinstance(assets, Mapping) else None
    href = asset.get("href") if isinstance(asset, Mapping) else None
    return href if isinstance(href, str) else ""


def hrefs_of(draft: Mapping[str, Any]) -> list[str]:
    assets = draft.get("assets") or {}
    if not isinstance(assets, Mapping):
        return []
    return [
        a["href"]
        for a in assets.values()
        if isinstance(a, Mapping) and isinstance(a.get("href"), str)
    ]


def path_segment(hrefs: Iterable[str], pattern: str) -> re.Match[str] | None:
    """The first href whose PATH matches ``pattern`` (searched, not anchored)."""
    compiled = re.compile(pattern)
    for href in hrefs:
        path = href.split("?", 1)[0]
        match = compiled.search(path)
        if match:
            return match
    return None


def rebuild_tree(
    paths: Mapping[str, Path], draft: Mapping[str, Any], marker: str, into: Path
) -> Path:
    """Recreate a product directory (a ``.SAFE``) the ingest flattened.

    The staged files carry only their basenames; the RELATIVE path inside
    the product survives in the draft's asset hrefs (a reference-mode
    association keeps the source URL). Every staged file is linked at
    ``into/{root}/{relative}``, where ``root`` is the href segment ending
    with ``marker`` and ``relative`` what follows it. Returns the root.
    Fails clearly when an href does not carry the marker — a canonical-mode
    ingest, whose hrefs are ``/api/assets/...`` — because no layout can be
    recovered from a flat list.
    """
    root: Path | None = None
    for key, local in paths.items():
        href = href_of(draft, key).split("?", 1)[0]
        lowered = href.lower()
        at = lowered.find(marker.lower() + "/")
        if at < 0:
            raise AdapterInputError(
                f"asset {key!r} href {href!r} carries no {marker} directory; the product "
                "layout cannot be rebuilt from a flat group (use a reference-mode association)"
            )
        head = href[: at + len(marker)]
        root_name = head.rsplit("/", 1)[-1]
        relative = href[at + len(marker) + 1 :]
        if not relative or ".." in relative.split("/"):
            raise AdapterInputError(
                f"asset {key!r} has an unusable path inside {root_name}: {relative!r}"
            )
        candidate = into / root_name
        if root is not None and candidate != root:
            raise AdapterInputError(f"the group spans two products: {root.name} and {root_name}")
        root = candidate
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.symlink(local, target)
    if root is None:
        raise AdapterInputError("the group is empty")
    return root
