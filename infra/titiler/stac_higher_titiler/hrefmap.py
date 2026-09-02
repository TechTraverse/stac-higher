"""Map platform-canonical asset hrefs to the objects behind them.

Items carry app-relative ``/api/assets/{collection}/{item}/{filename}`` hrefs
(ADR 0005: bytes are reachable only through the app). GDAL cannot open those,
so the tile server — which already holds credentials for the platform bucket —
rewrites them to ``s3://{bucket}/assets/{collection}/{item}/{filename}``.

The rewrite is a pure string substitution over the deterministic key layout
(``pipeline/storage/keys.py`` / ``app/src/lib/storage/keys.ts``). Anything that
is not exactly a canonical href passes through untouched: absolute URLs,
``s3://`` hrefs, ``vrt://`` wrappers, malformed paths. Never raise here — a
reader failure downstream is a clearer error than a mapper exception.
"""

from __future__ import annotations

from urllib.parse import unquote

_FILE_SCHEME = "file://"
_CANONICAL_PREFIX = "assets"


def map_canonical_href(href: object, *, bucket: str, base: str = "/api/assets") -> object:
    if not isinstance(href, str) or not href:
        return href
    candidate = href.removeprefix(_FILE_SCHEME)
    prefix = base.rstrip("/") + "/"
    if not candidate.startswith(prefix):
        return href
    rest = candidate[len(prefix) :].split("?", 1)[0]
    parts = rest.split("/")
    if len(parts) != 3:
        return href
    segments = [unquote(p) for p in parts]
    for seg in segments:
        if not seg or seg in (".", "..") or "/" in seg or "\\" in seg:
            return href
    collection, item_id, filename = segments
    return f"s3://{bucket}/{_CANONICAL_PREFIX}/{collection}/{item_id}/{filename}"
