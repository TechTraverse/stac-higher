"""Canonical object-storage key layout (ROADMAP §5.3).

The Python analog of ``app/src/lib/storage/keys.ts``. FETCH writes ingested asset
bytes under the canonical prefix so the app's asset route
(``GET /api/assets/{collection}/{item}/{filename}``) resolves them offline:

    assets/{collection}/{item_id}/{filename}

Segments are validated the same way the app's builder validates them — a
traversal attempt (``..``, ``/``, empty) is a hard error, never a silently
mangled key.
"""

from __future__ import annotations

from urllib.parse import quote

CANONICAL_PREFIX = "assets"


class InvalidKeySegment(ValueError):
    """A path segment is empty or contains a separator / traversal token."""


def _safe_segment(value: str, *, field: str) -> str:
    if not value or value in (".", ".."):
        raise InvalidKeySegment(f"{field} must be a non-empty, non-traversal segment")
    if "/" in value or "\\" in value:
        raise InvalidKeySegment(f"{field} must not contain a path separator: {value!r}")
    return value


def canonical_asset_key(collection: str, item_id: str, filename: str) -> str:
    """Build ``assets/{collection}/{item_id}/{filename}``, validating each segment."""
    collection = _safe_segment(collection, field="collection")
    item_id = _safe_segment(item_id, field="item_id")
    filename = _safe_segment(filename, field="filename")
    return f"{CANONICAL_PREFIX}/{collection}/{item_id}/{filename}"


def asset_href(
    collection: str, item_id: str, filename: str, *, base: str = "/api/assets"
) -> str:
    """Root-relative `/api/assets/{collection}/{item_id}/{filename}` href, each
    segment URL-encoded. Mirrors the app's `assetHref` so pipeline-created items
    resolve through the same asset route as manually uploaded ones."""
    seg = lambda s: quote(s, safe="")  # noqa: E731
    return f"{base.rstrip('/')}/{seg(collection)}/{seg(item_id)}/{seg(filename)}"
