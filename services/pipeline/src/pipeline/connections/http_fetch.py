"""Egress-checked GET of a public HTTPS URL (GOES spec §3.2 — the fallback for
staging a remote input asset when no reference-mode association's adapter
claims its href).

Same posture as the adapters: the host goes through ``resolve_pinned`` first,
so a private/loopback/metadata target is refused before any socket opens.
HTTPS only.

Connection is by HOSTNAME after validation, not by the pinned IP: keeping the
hostname is what lets the TLS layer validate the certificate and send SNI,
and ``http.client`` offers no clean way to dial one address while validating
another. This is the same narrow rebind window ``S3Adapter._pinned_endpoint``
accepts for https endpoints ("keep the hostname so TLS validates;
resolve_pinned already rejected internal addresses"). Redirects are refused
outright — following one would be a second, unvetted egress.
"""

from __future__ import annotations

import http.client
import ssl
from urllib.parse import urlsplit

from pipeline.connections.egress import resolve_pinned

DEFAULT_MAX_BYTES = 2 * 1024**3
_REDIRECTS = (301, 302, 303, 307, 308)
_CHUNK = 1024 * 1024


class PublicFetchError(Exception):
    """The URL could not be fetched (scheme, status, redirect, size)."""


def fetch_public_url(
    url: str,
    allow_hosts: frozenset[str] = frozenset(),
    *,
    timeout: float = 60.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise PublicFetchError(f"only https URLs can be staged: {url!r}")
    host = parts.hostname
    port = parts.port or 443
    # Raises EgressBlocked for a non-public target; the return value is not
    # used for dialling (see the module docstring).
    resolve_pinned(host, allow_hosts)

    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    conn = http.client.HTTPSConnection(
        host, port, timeout=timeout, context=ssl.create_default_context()
    )
    try:
        conn.request("GET", path, headers={"User-Agent": "stac-higher-pipeline"})
        resp = conn.getresponse()
        if resp.status in _REDIRECTS:
            raise PublicFetchError(f"redirects are not followed when staging inputs: {url!r}")
        if resp.status != 200:
            raise PublicFetchError(f"GET {url!r} returned {resp.status}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise PublicFetchError(f"{url!r} exceeds the {max_bytes}-byte staging limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        conn.close()
