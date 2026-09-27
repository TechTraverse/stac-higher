"""The ``registry`` connection protocol (C-1, container-images spec section 5, ADR 0021).

A registry connection is group-owned image pull credentials: config ``{host}``,
credentials ``{username, password}`` sealed in the existing envelope. It
reuses the connections form, group ownership and the ``connection_checks``
drain. Its check probe is the registry v2 handshake: ``GET /v2/`` with Basic
auth. A Bearer challenge is followed to its token endpoint with the same
Basic credentials, and a token (200) means the credentials work.

The host grammar is not reinvented here: ``parse_registry_config`` validates
against ``pipeline.images.reference.IMAGE_HOST_RE``, the same host fragment
the image reference grammar uses, so every host a credential can be
configured for is a host an image reference can name.

Egress: every host dialled (the registry and a token realm) goes through
``resolve_pinned`` first, and connections are HTTPS only. Like
``http_fetch.py``, the request goes by HOSTNAME after validation so TLS can
verify the certificate. Redirects are not followed. Neither the password nor
the token ever appears in a result message or a log line.
"""

from __future__ import annotations

import asyncio
import base64
import http.client
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from pipeline.connections.adapters.base import TestResult
from pipeline.connections.build import AdapterBuildError, decrypt_credentials
from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.connections.repo import ConnectionRow
from pipeline.images.reference import IMAGE_HOST_RE

REGISTRY_PROTOCOL = "registry"
DOCKER_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
DOCKER_HUB_API_HOST = "registry-1.docker.io"
PROBE_TIMEOUT_SECONDS = 15.0
_MAX_BODY = 64 * 1024

_CHALLENGE_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


class RegistryConfigError(ValueError):
    """A registry connection's config is not a usable ``{host}``."""


@dataclass(frozen=True)
class RegistryConfig:
    host: str


def parse_registry_config(raw: Any) -> RegistryConfig:
    if not isinstance(raw, dict):
        raise RegistryConfigError("registry config must be an object")
    host = raw.get("host")
    if not isinstance(host, str) or not host.strip():
        raise RegistryConfigError("registry config needs a host")
    host = host.strip().lower()
    if not IMAGE_HOST_RE.fullmatch(host):
        raise RegistryConfigError(
            "registry host must be a bare hostname with an optional port, no scheme or path"
        )
    return RegistryConfig(host=host)


def registry_api_host(host: str) -> str:
    """Docker Hub's short names are served by registry-1.docker.io."""
    return DOCKER_HUB_API_HOST if host in DOCKER_HUB_HOSTS else host


def parse_bearer_challenge(header: str | None) -> dict[str, str] | None:
    if not header:
        return None
    scheme, _, params = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return {key.lower(): value for key, value in _CHALLENGE_PARAM_RE.findall(params)}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    #: lower-cased header names
    headers: Mapping[str, str]
    body: bytes


HttpGet = Callable[[str, Mapping[str, str]], HttpResponse]


def _https_get(url: str, headers: Mapping[str, str]) -> HttpResponse:  # pragma: no cover - network
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname or "",
        parts.port or 443,
        timeout=PROBE_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn.request("GET", path, headers={"User-Agent": "stac-higher-pipeline", **headers})
        resp = conn.getresponse()
        body = resp.read(_MAX_BODY)
        return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, body)
    finally:
        conn.close()


def _host_only(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def check_registry(
    config: RegistryConfig,
    credentials: Mapping[str, Any],
    allow_hosts: frozenset[str],
    *,
    http_get: HttpGet = _https_get,
) -> TestResult:
    """Blocking. Never raises for an expected failure; never echoes a secret."""
    username = credentials.get("username")
    password = credentials.get("password")
    if not (isinstance(username, str) and username and isinstance(password, str) and password):
        return {"ok": False, "message": "registry credentials need a username and a password"}
    api_host = registry_api_host(config.host)
    started = time.monotonic()
    basic = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    rejected: TestResult = {"ok": False, "message": f"{config.host} rejected the credentials"}

    # The /v2/ request. A hostile or misconfigured host can make urlsplit/the
    # transport raise ValueError (an invalid URL) as well as the expected
    # egress/network failures; none of those may escape as an exception, and
    # none of the messages below ever carry the credentials.
    try:
        resolve_pinned(_host_only(api_host), allow_hosts)
        first = http_get(f"https://{api_host}/v2/", {"Authorization": basic})
    except EgressBlocked as exc:
        return {"ok": False, "message": str(exc)}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": False, "message": f"{config.host} unreachable: {type(exc).__name__}"}
    except ValueError:
        return {"ok": False, "message": f"{api_host} sent a malformed response"}

    if first.status == 200:
        return _ok(config, started)
    if first.status != 401:
        return {"ok": False, "message": f"GET /v2/ on {api_host} returned {first.status}"}
    challenge = parse_bearer_challenge(first.headers.get("www-authenticate"))
    if not challenge or not challenge.get("realm"):
        return rejected

    # The bearer challenge names its own token endpoint, so it is untrusted
    # input: a hostile registry can hand back a realm that is not a valid URL
    # at all (bad IPv6 host, an out-of-range port, a non-ASCII path); each of
    # those raises ValueError, either from urlsplit here or from the
    # transport when it turns the realm into a request. Caught the same way
    # as above, with a message naming no secret.
    try:
        realm = urlsplit(challenge["realm"])
        if realm.scheme != "https" or not realm.hostname:
            return {
                "ok": False,
                "message": f"{config.host} names a token endpoint that is not https",
            }
        resolve_pinned(realm.hostname, allow_hosts)
        query = {"service": challenge["service"]} if challenge.get("service") else {}
        token_url = challenge["realm"]
        if query:
            token_url = f"{token_url}{'&' if realm.query else '?'}{urlencode(query)}"
        token = http_get(token_url, {"Authorization": basic})
    except EgressBlocked as exc:
        return {"ok": False, "message": str(exc)}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": False, "message": f"{config.host} unreachable: {type(exc).__name__}"}
    except ValueError:
        return {"ok": False, "message": f"{config.host} sent a malformed token challenge"}

    if token.status == 200:
        return _ok(config, started)
    if token.status in (401, 403):
        return rejected
    return {
        "ok": False,
        "message": f"the token endpoint of {config.host} returned {token.status}",
    }


def _ok(config: RegistryConfig, started: float) -> TestResult:
    return {
        "ok": True,
        "message": f"authenticated to {config.host}",
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


async def test_registry_connection(
    connection: ConnectionRow,
    master_key: bytes,
    allow_hosts: frozenset[str],
    *,
    http_get: HttpGet = _https_get,
) -> TestResult:
    """The probe seam for ``protocol == "registry"`` (see ``probe.run_adapter_test``)."""
    try:
        config = parse_registry_config(connection.config)
        credentials = decrypt_credentials(connection, master_key)
    except (RegistryConfigError, AdapterBuildError) as exc:
        return {"ok": False, "message": str(exc)}
    return await asyncio.to_thread(
        check_registry, config, credentials, allow_hosts, http_get=http_get
    )


test_registry_connection.__test__ = False  # not a pytest test, despite its name
