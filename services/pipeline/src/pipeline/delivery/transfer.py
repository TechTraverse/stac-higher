"""Delivery transfer-path helpers (ROADMAP §6.4, Slice B-ii).

Pure and I/O-free: the fingerprint formats stored in
``delivery_log.delivered_assets`` and the S3→S3 server-side-copy gate.
Fingerprints of different kinds (streamed sha256 vs copy-path etag)
intentionally compare unequal — worst case one redundant redeliver,
consistent with at-least-once delivery (ISSUES I-43).
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

_DEFAULT_PORTS = {"http": 80, "https": 443}


def sha256_fingerprint(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def etag_fingerprint(etag: str, size: int) -> str:
    return f"etag:{etag}/{size}"


def is_multipart_etag(etag: str) -> bool:
    """A multipart-upload ETag (``<md5-of-part-md5s>-<parts>``) — NOT a content
    md5, so it cannot back an md5 checksum sidecar (ISSUES I-48)."""
    return "-" in etag


def _normalized(endpoint: str) -> tuple[str, str, int | None]:
    parsed = urlparse(endpoint)
    scheme = parsed.scheme or "https"
    return scheme, (parsed.hostname or "").lower(), parsed.port or _DEFAULT_PORTS.get(scheme)


def can_server_side_copy(
    protocol: str,
    connection_endpoint: str | None,
    platform_endpoint: str | None,
) -> bool:
    """True when the destination can CopyObject straight from the canonical
    bucket: an s3 connection on the platform's own endpoint (spec decision 4),
    or both sides on real AWS (no custom endpoint — CopyObject spans buckets
    there). The worker still falls back to streaming if the copy itself fails
    (e.g. the destination credentials cannot read the canonical bucket).
    """
    if protocol != "s3":
        return False
    if connection_endpoint is None and platform_endpoint is None:
        return True
    if connection_endpoint is None or platform_endpoint is None:
        return False
    try:
        return _normalized(connection_endpoint) == _normalized(platform_endpoint)
    except ValueError:  # malformed endpoint (e.g. non-numeric port) — never copy
        return False
