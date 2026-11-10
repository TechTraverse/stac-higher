"""Cache-Control for tiles (spec §8.3, plan Decision 5).

Virtual chunks get no server-side cache, so the browser's cache is the cache.
A tile pinned to an exact ``t`` never changes: the step's source file is
immutable and every rendering parameter is in the URL. A ``nearest::`` (or any
``method::``) selector is not pinned — once its step is trimmed it resolves to
another one — and neither is an unpinned tile, which renders the latest step.
A failed response is never stored, so a ``t`` the server has not loaded yet
is retried after the next reload — and neither is any ``render_errors`` tile,
since that option turns an error into a 200 image.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=60"
NO_STORE = "no-store"

_TILE = re.compile(r"^/datasets/[^/]+/tiles/[^/]+/\d+/\d+/\d+$")


#: pydantic's falsy bool spellings, which xpublish-tiles' ``render_errors`` is parsed with
_FALSY = frozenset({"0", "f", "n", "no", "off", "false"})


def is_tile_path(path: str) -> bool:
    return _TILE.match(path) is not None


def collection_id(path: str) -> str | None:
    """The cube collection id of a tile path."""
    return path.split("/")[2] if is_tile_path(path) else None


def cache_control(
    path: str, query: Mapping[str, str], status: int, append_dim: str = "t"
) -> str | None:
    """``append_dim`` is the dataset's own (``cube_sinks.config.append_dim``):
    xpublish-tiles takes a dimension's selector under that name only."""
    if not is_tile_path(path):
        return None
    if status != 200:
        return NO_STORE
    # xpublish-tiles' render_errors draws an error as a 200 PNG, which can't be
    # told from a real tile here. Only an explicit falsy value (pydantic's
    # spelling) is safe; anything else may be truthy, so never store it.
    if "render_errors" in query and query["render_errors"].strip().lower() not in _FALSY:
        return NO_STORE
    t = query.get(append_dim)
    return IMMUTABLE if t and "::" not in t else SHORT
