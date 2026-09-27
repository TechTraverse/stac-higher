"""Tag drift (C-4, container-images spec §8.2, §5).

On a rescan the pipeline asks the registry, with one ``HEAD`` on the tag the
image was added with (``tag_at_add``), which manifest digest the tag names
NOW. A tag that moved is informational (spec §8.2): runs keep the scanned
digest, and re-adding the reference makes a new image row. A HEAD is free on
Docker Hub (ISSUES I-125).

This is the pipeline touching the registry (spec §5: drift HEADs go through
``resolve_pinned``), which keeps the rescan scanner free of any registry
credential (C-2 Decision 25). Every host dialled, the registry and a Bearer
token realm, is resolved through ``resolve_pinned`` first. Only HTTPS is
spoken, redirects are not followed, and no message or log line names a
credential or a token. A failure is recorded, never fatal: ``tag_drift`` then
says ``current_digest: null``.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import re
import ssl
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.connections.registry import (
    HttpResponse,
    parse_bearer_challenge,
    registry_api_host,
)
from pipeline.images.reference import registry_host
from pipeline.images.repo import ImageRow
from pipeline.process.executor import RegistryAuth

logger = logging.getLogger(__name__)

ACCEPT = ", ".join(
    sorted(
        {
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        }
    )
)
TIMEOUT_SECONDS = 15.0
_MAX_BODY = 64 * 1024
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")

Request = Callable[[str, str, Mapping[str, str]], HttpResponse]


class DriftCheckFailed(Exception):
    """The registry could not say where the tag points now."""


def _https_request(
    method: str, url: str, headers: Mapping[str, str]
) -> HttpResponse:  # pragma: no cover - network
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname or "",
        parts.port or 443,
        timeout=TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn.request(method, path, headers={"User-Agent": "stac-higher-pipeline", **headers})
        resp = conn.getresponse()
        body = b"" if method == "HEAD" else resp.read(_MAX_BODY)
        return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, body)
    finally:
        conn.close()


def _hostname(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def _basic(auth: RegistryAuth | None) -> str | None:
    if auth is None:
        return None
    raw = f"{auth.username}:{auth.password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _bearer(
    challenge: dict[str, str],
    repository: str,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    request: Request,
) -> str:
    realm_url = challenge.get("realm", "")
    realm = urlsplit(realm_url)
    if realm.scheme != "https" or not realm.hostname:
        raise DriftCheckFailed("the registry names a token endpoint that is not https")
    resolve_pinned(realm.hostname, allow_hosts)
    query = {"scope": f"repository:{repository}:pull"}
    if challenge.get("service"):
        query["service"] = challenge["service"]
    url = f"{realm_url}{'&' if realm.query else '?'}{urlencode(query)}"
    basic = _basic(auth)
    resp = request("GET", url, {"Authorization": basic} if basic else {})
    if resp.status != 200:
        raise DriftCheckFailed(f"the token endpoint returned {resp.status}")
    try:
        doc = json.loads(resp.body)
    except ValueError as err:
        raise DriftCheckFailed("the token endpoint sent malformed JSON") from err
    value = (doc.get("token") or doc.get("access_token")) if isinstance(doc, dict) else None
    if not isinstance(value, str) or not value:
        raise DriftCheckFailed("the token endpoint returned no token")
    return f"Bearer {value}"


def head_tag_digest(
    reference: str,
    tag: str,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    *,
    request: Request = _https_request,
) -> str:
    """The digest ``reference:tag`` names now. Raises on anything else."""
    host = registry_host(reference)
    repository = reference.split("/", 1)[1]
    api = registry_api_host(host)
    url = f"https://{api}/v2/{repository}/manifests/{quote(tag, safe='')}"
    resolve_pinned(_hostname(api), allow_hosts)
    headers = {"Accept": ACCEPT}
    resp = request("HEAD", url, headers)
    if resp.status == 401:
        header = resp.headers.get("www-authenticate", "")
        challenge = parse_bearer_challenge(header)
        if challenge is not None:
            authorization = _bearer(challenge, repository, auth, allow_hosts, request)
        elif header.strip().lower().startswith("basic") and auth is not None:
            authorization = _basic(auth) or ""
        else:
            raise DriftCheckFailed(f"{host} requires credentials the pipeline does not have")
        resp = request("HEAD", url, {**headers, "Authorization": authorization})
    if resp.status != 200:
        raise DriftCheckFailed(f"HEAD manifests/{tag} on {host} returned {resp.status}")
    digest = resp.headers.get("docker-content-digest", "")
    if not _DIGEST_RE.fullmatch(digest):
        raise DriftCheckFailed(f"{host} reported no sha256 digest for tag {tag!r}")
    return digest


def tag_drift(
    image: ImageRow,
    auth: RegistryAuth | None,
    allow_hosts: Iterable[str],
    *,
    request: Request = _https_request,
) -> dict[str, Any]:
    """The §6.4 ``tag_drift`` record for one rescan. Never raises."""
    try:
        current = head_tag_digest(
            image.reference, image.tag_at_add, auth, allow_hosts, request=request
        )
    except (
        DriftCheckFailed,
        EgressBlocked,
        OSError,
        http.client.HTTPException,
        ValueError,
    ) as err:
        logger.warning(
            "tag drift check failed",
            extra={"image_id": image.id, "error_type": type(err).__name__},
        )
        return {"current_digest": None, "drifted": False}
    return {"current_digest": current, "drifted": current != image.digest}
