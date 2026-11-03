"""Cache-Control for tiles (spec §8.3, plan Decision 5).

Virtual chunks get no server-side cache, so the browser's cache is the cache.
A tile pinned to an exact ``t`` never changes: the step's source file is
immutable and every rendering parameter is in the URL. A ``nearest::`` (or any
``method::``) selector is not pinned — once its step is trimmed it resolves to
another one — and neither is an unpinned tile, which renders the latest step.
A failed response is never stored, so a ``t`` the server has not loaded yet
is retried after the next reload.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=60"
NO_STORE = "no-store"

_TILE = re.compile(r"^/datasets/[^/]+/tiles/[^/]+/\d+/\d+/\d+$")


def is_tile_path(path: str) -> bool:
    return _TILE.match(path) is not None


def cache_control(path: str, query: Mapping[str, str], status: int) -> str | None:
    if not is_tile_path(path):
        return None
    if status != 200:
        return NO_STORE
    t = query.get("t")
    return IMMUTABLE if t and "::" not in t else SHORT
