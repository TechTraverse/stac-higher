"""Registry v2 client for the scanner (container-images spec §6.3, step 2).

Stdlib only. Resolves a tag to its manifest (or index) digest, picks the
policy platform's manifest, sums its layer sizes and reads the image config,
all before any layer blob is fetched, so an oversized image is refused for
the cost of a few small requests.

Every response is untrusted: each manifest and the config blob are verified
against the digest that named them, bodies are capped, and only HTTPS is
spoken. A redirect (registries send blob reads to a CDN) never carries the
registry's Authorization header to another host. No message ever names a
credential.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
MANIFEST_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
ACCEPT = ", ".join(sorted(INDEX_TYPES | MANIFEST_TYPES))
DOCKER_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
DOCKER_HUB_API_HOST = "registry-1.docker.io"
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 30
USER_AGENT = "stac-higher-image-scanner"

_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_CHALLENGE_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')
_LABEL_RE = re.compile(r"[a-z0-9-]+")


class RegistryError(Exception):
    """The registry refused, failed, or answered something unusable."""


class ImageTooLarge(RegistryError):
    """The layers sum above the policy's ``max_image_size_mb``."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Response:
    status: int
    #: lower-cased header names
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, str, Mapping[str, str]], Response]


@dataclass(frozen=True)
class Resolved:
    digest: str
    platform_digest: str
    size_bytes: int
    config: dict[str, Any]
    platform: dict[str, str]


def split_reference(reference: str) -> tuple[str, str]:
    host, _, repository = reference.partition("/")
    if not host or not repository:
        raise RegistryError(f"{reference!r} is not a normalized image reference")
    return host, repository


def api_host(host: str) -> str:
    """Docker Hub's short names are served by registry-1.docker.io."""
    return DOCKER_HUB_API_HOST if host in DOCKER_HUB_HOSTS else host


def registry_allowed(host: str, patterns) -> bool:
    """``*`` is exactly one DNS label; the host is case-folded; a port must
    match literally. The same rule as ``pipeline.images.policy`` (a test pins
    them equal), copied because the scanner ships without the pipeline."""
    labels = host.lower().split(".")
    for pattern in patterns:
        want = pattern.split(".")
        if len(want) == len(labels) and all(
            (_LABEL_RE.fullmatch(have) is not None) if w == "*" else w == have
            for w, have in zip(want, labels, strict=True)
        ):
            return True
    return False


def parse_platform(value: str) -> tuple[str, str, str | None]:
    parts = value.split("/")
    if len(parts) not in (2, 3) or not all(parts):
        raise RegistryError(f"platform must be os/architecture[/variant], got {value!r}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def sha256_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def pick_platform(index: dict[str, Any], os_: str, arch: str, variant: str | None) -> str:
    for entry in index.get("manifests") or []:
        if not isinstance(entry, dict):
            continue
        plat = entry.get("platform") if isinstance(entry.get("platform"), dict) else {}
        if (
            plat.get("os") == os_
            and plat.get("architecture") == arch
            and (variant is None or plat.get("variant") == variant)
        ):
            digest = entry.get("digest")
            if isinstance(digest, str) and _DIGEST_RE.fullmatch(digest):
                return digest
    wanted = f"{os_}/{arch}" + (f"/{variant}" if variant else "")
    raise RegistryError(f"the image index has no {wanted} manifest")


def image_config(blob: dict[str, Any]) -> dict[str, Any]:
    """The spec §3.2 config record: USER, ENTRYPOINT, CMD, as information."""
    cfg = blob.get("config") if isinstance(blob.get("config"), dict) else {}

    def str_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        return [v for v in value if isinstance(v, str)]

    user = cfg.get("User")
    return {
        "user": user if isinstance(user, str) else "",
        "entrypoint": str_list(cfg.get("Entrypoint")),
        "cmd": str_list(cfg.get("Cmd")),
    }


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a registry's redirect (blob reads go to a CDN) without handing
    the registry's token to the CDN, and never to plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https":
            raise urllib.error.HTTPError(
                newurl, code, "refusing a redirect that is not https", headers, fp
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and target.netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect())


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str]
) -> Response:  # pragma: no cover - network
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise RegistryError("refusing a registry URL that is not https")
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT, **headers}
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as resp:
            body = b"" if method == "HEAD" else resp.read(MAX_DOCUMENT_BYTES + 1)
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as err:
        body = err.read(64 * 1024) if err.fp is not None else b""
        found = err.headers.items() if err.headers is not None else []
        return Response(err.code, {k.lower(): v for k, v in found}, body)
    except (urllib.error.URLError, OSError) as err:
        raise RegistryError(f"{parts.hostname} unreachable: {type(err).__name__}") from err


class RegistryClient:
    """One repository on one registry, read-only (pull scope)."""

    def __init__(
        self,
        reference: str,
        credentials: Credentials | None = None,
        *,
        transport: Transport = urllib_transport,
    ) -> None:
        self.host, self.repository = split_reference(reference)
        self.base = f"https://{api_host(self.host)}/v2/{self.repository}"
        self.credentials = credentials
        self._transport = transport
        self._authorization: str | None = None

    def _basic(self) -> str | None:
        if self.credentials is None:
            return None
        raw = f"{self.credentials.username}:{self.credentials.password}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _authorize(self, challenge: str) -> str:
        scheme, _, params = challenge.strip().partition(" ")
        if scheme.lower() == "basic":
            basic = self._basic()
            if basic is None:
                raise RegistryError(f"{self.host} requires credentials and none were given")
            return basic
        if scheme.lower() != "bearer":
            raise RegistryError(f"{self.host} sent an unsupported auth challenge")
        fields = {k.lower(): v for k, v in _CHALLENGE_PARAM_RE.findall(params)}
        realm = fields.get("realm", "")
        if urllib.parse.urlsplit(realm).scheme != "https":
            raise RegistryError(f"{self.host} names a token endpoint that is not https")
        query = {"scope": f"repository:{self.repository}:pull"}
        if fields.get("service"):
            query["service"] = fields["service"]
        url = f"{realm}{'&' if '?' in realm else '?'}{urllib.parse.urlencode(query)}"
        headers: dict[str, str] = {}
        basic = self._basic()
        if basic is not None:
            headers["Authorization"] = basic
        resp = self._transport("GET", url, headers)
        if resp.status != 200:
            raise RegistryError(f"the token endpoint of {self.host} returned {resp.status}")
        try:
            doc = json.loads(resp.body)
        except ValueError as err:
            raise RegistryError(f"the token endpoint of {self.host} sent malformed JSON") from err
        token = (doc.get("token") or doc.get("access_token")) if isinstance(doc, dict) else None
        if not isinstance(token, str) or not token:
            raise RegistryError(f"the token endpoint of {self.host} returned no token")
        return f"Bearer {token}"

    def _send(self, method: str, url: str, accept: str | None) -> Response:
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if self._authorization:
            headers["Authorization"] = self._authorization
        resp = self._transport(method, url, headers)
        if resp.status != 401:
            return resp
        self._authorization = self._authorize(resp.headers.get("www-authenticate", ""))
        headers["Authorization"] = self._authorization
        return self._transport(method, url, headers)

    def _document(self, path: str, digest: str, accept: str | None) -> dict[str, Any]:
        resp = self._send("GET", f"{self.base}/{path}/{digest}", accept)
        if resp.status != 200:
            raise RegistryError(f"GET {path}/{digest} on {self.host} returned {resp.status}")
        if len(resp.body) > MAX_DOCUMENT_BYTES:
            raise RegistryError(f"{path}/{digest} on {self.host} is too large")
        if sha256_digest(resp.body) != digest:
            raise RegistryError(f"{path}/{digest} on {self.host} does not match its digest")
        try:
            doc = json.loads(resp.body)
        except ValueError as err:
            raise RegistryError(f"{path}/{digest} on {self.host} is not JSON") from err
        if not isinstance(doc, dict):
            raise RegistryError(f"{path}/{digest} on {self.host} is not a JSON object")
        return doc

    def head_tag(self, tag: str) -> str:
        """Tag -> digest with a HEAD, which Docker Hub does not count as a pull."""
        url = f"{self.base}/manifests/{urllib.parse.quote(tag, safe='')}"
        resp = self._send("HEAD", url, ACCEPT)
        if resp.status != 200:
            raise RegistryError(f"HEAD manifests/{tag} on {self.host} returned {resp.status}")
        digest = resp.headers.get("docker-content-digest", "")
        if not _DIGEST_RE.fullmatch(digest):
            raise RegistryError(f"{self.host} reported no sha256 digest for tag {tag!r}")
        return digest

    def resolve(self, tag: str, platform: str, max_bytes: int) -> Resolved:
        want_os, want_arch, want_variant = parse_platform(platform)
        digest = self.head_tag(tag)
        top = self._document("manifests", digest, ACCEPT)
        media_type = top.get("mediaType")
        # A registry is untrusted (module docstring): `in INDEX_TYPES` hashes
        # the value, so an unhashable `mediaType` (a list, an object) would
        # otherwise crash with a TypeError instead of the ordinary refusal
        # below. isinstance guards that; anything not a string is simply not
        # an index (final-review fix wave item 6a).
        if (isinstance(media_type, str) and media_type in INDEX_TYPES) or isinstance(
            top.get("manifests"), list
        ):
            platform_digest = pick_platform(top, want_os, want_arch, want_variant)
            manifest = self._document("manifests", platform_digest, ACCEPT)
        else:
            platform_digest, manifest = digest, top
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise RegistryError(f"manifest {platform_digest} lists no layers")
        size = 0
        for layer in layers:
            layer_size = layer.get("size") if isinstance(layer, dict) else None
            if isinstance(layer_size, bool) or not isinstance(layer_size, int) or layer_size < 0:
                raise RegistryError(f"manifest {platform_digest} has a layer without a size")
            size += layer_size
        if size > max_bytes:
            raise ImageTooLarge(
                f"image_too_large: {size} bytes of layers exceed the policy's {max_bytes}"
            )
        config_ref = manifest.get("config")
        config_digest = config_ref.get("digest") if isinstance(config_ref, dict) else None
        if not isinstance(config_digest, str) or not _DIGEST_RE.fullmatch(config_digest):
            raise RegistryError(f"manifest {platform_digest} names no config digest")
        blob = self._document("blobs", config_digest, None)
        if blob.get("os") != want_os or blob.get("architecture") != want_arch:
            raise RegistryError(
                f"the image is {blob.get('os')}/{blob.get('architecture')}, "
                f"the policy requires {platform}"
            )
        return Resolved(
            digest=digest,
            platform_digest=platform_digest,
            size_bytes=size,
            config=image_config(blob),
            platform={"os": want_os, "architecture": want_arch},
        )
