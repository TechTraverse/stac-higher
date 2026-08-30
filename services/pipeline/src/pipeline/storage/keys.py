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

import re
from dataclasses import dataclass
from urllib.parse import quote

CANONICAL_PREFIX = "assets"
STAGING_PREFIX = "staging"


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
    segs = "/".join(quote(s, safe="") for s in (collection, item_id, filename))
    return f"{base.rstrip('/')}/{segs}"


# ---------------------------------------------------------------------------
# Staged-asset hrefs (Phase 7 push ingest, spec §4.2).
#
#   staging://{upload_id}/{filename}
#
# The Python mirror of the app's `stagedHref`/`isStagedHref`/`parseStagedHref`
# (`app/src/lib/storage/keys.ts`). The dispatcher detects with the prefix
# check; finalize's resolver parses strictly (the parsed parts feed
# `staging_key`/`canonical_asset_key` downstream, so anything a mint could not
# have produced is a hard error). Cross-runtime contract — golden fixture:
# tests/contract-fixtures/staged-asset-href.json (grammar-cases style).
# ---------------------------------------------------------------------------

STAGED_HREF_SCHEME = "staging://"

#: identity segments (upload ids) are kept verbatim but must be a single safe
#: path segment — the app's SAFE_SEGMENT regex, mirrored exactly.
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def sanitize_filename(name: str) -> str:
    """The app's ``sanitizeFilename``, mirrored: basename only, everything
    outside ``[A-Za-z0-9._-]`` replaced with ``_``, leading dots stripped (so
    the result can never be ``.`` or ``..``). Raises when nothing usable
    remains."""
    base = re.split(r"[/\\]", name)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    if not cleaned:
        raise InvalidKeySegment(f"filename {name!r} is not usable")
    return cleaned


def staging_key(upload_id: str, filename: str) -> str:
    """Build ``staging/{upload_id}/{filename}`` — the object the paired
    presigned PUT wrote (mirrors the app's ``stagingKey``)."""
    if not _SAFE_IDENTITY.match(upload_id):
        raise InvalidKeySegment(f"upload id is not a safe path segment: {upload_id!r}")
    return f"{STAGING_PREFIX}/{upload_id}/{sanitize_filename(filename)}"


def staging_prefix(upload_id: str) -> str:
    """``staging/{upload_id}/`` — one upload session's entire staging world."""
    if not _SAFE_IDENTITY.match(upload_id):
        raise InvalidKeySegment(f"upload id is not a safe path segment: {upload_id!r}")
    return f"{STAGING_PREFIX}/{upload_id}/"


def is_staged_href(href: object) -> bool:
    """The dispatcher-style detection: a plain prefix check (case-sensitive).
    An undetected href is NOT staged — it passes through as an ordinary
    external asset href, never an error."""
    return isinstance(href, str) and href.startswith(STAGED_HREF_SCHEME)


@dataclass(frozen=True)
class StagedHrefParts:
    upload_id: str
    filename: str


def parse_staged_href(href: str) -> StagedHrefParts:
    """Parse a staged href strictly; raises :class:`InvalidKeySegment` for
    anything a mint could never have produced: a non-``staging://`` scheme, an
    empty or unsafe upload id, a missing filename, or a filename that does not
    round-trip through :func:`sanitize_filename` unchanged (traversal
    segments, directory separators, unsafe characters)."""
    if not is_staged_href(href):
        raise InvalidKeySegment(f"not a staged href: {href!r}")
    rest = href[len(STAGED_HREF_SCHEME):]
    slash = rest.find("/")
    if slash in (-1, 0) or slash == len(rest) - 1:
        raise InvalidKeySegment("staged href must be staging://{upload_id}/{filename}")
    upload_id = rest[:slash]
    if not _SAFE_IDENTITY.match(upload_id):
        raise InvalidKeySegment(f"upload id is not a safe path segment: {upload_id!r}")
    filename = rest[slash + 1:]
    if filename != sanitize_filename(filename):
        raise InvalidKeySegment("staged href filename is not a single sanitized path segment")
    return StagedHrefParts(upload_id=upload_id, filename=filename)
