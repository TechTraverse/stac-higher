"""The scanner's registry v2 client (container-images spec §6.3 step 2).

Every response is untrusted: manifests and the config blob must match the
digest that named them, only HTTPS is spoken, and the registry's
Authorization header never follows a redirect to another host."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from stac_higher_scanner.registry import (
    Credentials,
    ImageTooLarge,
    RegistryClient,
    RegistryError,
    Response,
    _SafeRedirect,
    registry_allowed,
    sha256_digest,
)


def _doc(obj) -> tuple[bytes, str]:
    body = json.dumps(obj).encode()
    return body, sha256_digest(body)


CONFIG_BODY, CONFIG_DIGEST = _doc(
    {
        "os": "linux",
        "architecture": "amd64",
        "config": {"User": "root", "Entrypoint": ["/entry.sh"], "Cmd": None},
    }
)
MANIFEST_BODY, MANIFEST_DIGEST = _doc(
    {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": CONFIG_DIGEST, "size": len(CONFIG_BODY)},
        "layers": [
            {"digest": "sha256:" + "1" * 64, "size": 100},
            {"digest": "sha256:" + "2" * 64, "size": 50},
        ],
    }
)
INDEX_BODY, INDEX_DIGEST = _doc(
    {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:" + "9" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
            {
                "digest": "sha256:" + "8" * 64,
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
            {"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
)


class Registry:
    """A fake registry behind an anonymous-or-Basic Bearer token flow."""

    def __init__(self, tag_digest, docs, *, realm="https://auth.example/token"):
        self.tag_digest = tag_digest
        self.docs = docs
        self.realm = realm
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        if url.startswith(self.realm):
            return Response(200, {}, json.dumps({"token": "T0K"}).encode())
        if headers.get("Authorization") != "Bearer T0K":
            challenge = f'Bearer realm="{self.realm}",service="registry.example"'
            return Response(401, {"www-authenticate": challenge}, b"")
        path = url.split("/v2/example/tool/", 1)[1]
        if method == "HEAD" and path == "manifests/1.0":
            return Response(200, {"docker-content-digest": self.tag_digest}, b"")
        _, _, digest = path.partition("/")
        body = self.docs.get(digest)
        return Response(200, {}, body) if body is not None else Response(404, {}, b"")


DOCS = {INDEX_DIGEST: INDEX_BODY, MANIFEST_DIGEST: MANIFEST_BODY, CONFIG_DIGEST: CONFIG_BODY}


def client(registry, credentials=None) -> RegistryClient:
    return RegistryClient("ghcr.io/example/tool", credentials, transport=registry)


def test_an_index_resolves_to_the_policy_platform_manifest():
    resolved = client(Registry(INDEX_DIGEST, DOCS)).resolve("1.0", "linux/amd64", 10_000)
    assert resolved.digest == INDEX_DIGEST
    assert resolved.platform_digest == MANIFEST_DIGEST
    assert resolved.size_bytes == 150
    assert resolved.platform == {"os": "linux", "architecture": "amd64"}
    assert resolved.config == {"user": "root", "entrypoint": ["/entry.sh"], "cmd": None}


def test_a_single_platform_manifest_is_its_own_platform_digest():
    resolved = client(Registry(MANIFEST_DIGEST, DOCS)).resolve("1.0", "linux/amd64", 10_000)
    assert resolved.digest == resolved.platform_digest == MANIFEST_DIGEST


def test_an_image_above_the_size_cap_is_refused_before_any_layer_is_read():
    registry = Registry(INDEX_DIGEST, DOCS)
    with pytest.raises(ImageTooLarge, match="image_too_large"):
        client(registry).resolve("1.0", "linux/amd64", 149)
    # Nothing past the manifests was requested: no config blob, no layer.
    assert not any("/blobs/" in url for _m, url, _h in registry.calls)


def test_a_document_that_does_not_match_its_digest_is_refused():
    tampered = dict(DOCS)
    tampered[MANIFEST_DIGEST] = MANIFEST_BODY + b" "
    with pytest.raises(RegistryError, match="does not match its digest"):
        client(Registry(INDEX_DIGEST, tampered)).resolve("1.0", "linux/amd64", 10_000)


def test_no_manifest_for_the_policy_platform_is_refused():
    with pytest.raises(RegistryError, match="linux/s390x"):
        client(Registry(INDEX_DIGEST, DOCS)).resolve("1.0", "linux/s390x", 10_000)


def test_the_token_request_asks_for_pull_only_and_sends_basic_credentials():
    registry = Registry(INDEX_DIGEST, DOCS)
    client(registry, Credentials("robot", "s3cret")).resolve("1.0", "linux/amd64", 10_000)
    token_calls = [(u, h) for _m, u, h in registry.calls if u.startswith(registry.realm)]
    assert len(token_calls) == 1  # the token is reused
    url, headers = token_calls[0]
    assert "scope=repository%3Aexample%2Ftool%3Apull" in url
    assert "service=registry.example" in url
    assert headers["Authorization"].startswith("Basic ")


def test_an_anonymous_token_request_carries_no_authorization():
    registry = Registry(INDEX_DIGEST, DOCS)
    client(registry).resolve("1.0", "linux/amd64", 10_000)
    _u, headers = next((u, h) for _m, u, h in registry.calls if u.startswith(registry.realm))
    assert "Authorization" not in headers


def test_a_token_realm_that_is_not_https_is_refused():
    registry = Registry(INDEX_DIGEST, DOCS, realm="http://auth.example/token")
    with pytest.raises(RegistryError, match="not https"):
        client(registry, Credentials("robot", "s3cret")).resolve("1.0", "linux/amd64", 10_000)


def test_docker_hub_references_talk_to_registry_1():
    assert RegistryClient("docker.io/library/python").base == (
        "https://registry-1.docker.io/v2/library/python"
    )


def test_credentials_never_print():
    assert "s3cret" not in repr(Credentials("robot", "s3cret"))


def _redirect(newurl):
    request = urllib.request.Request(
        "https://ghcr.io/v2/example/tool/blobs/sha256:x",
        headers={"Authorization": "Bearer T0K"},
    )
    return _SafeRedirect().redirect_request(request, None, 307, "Temporary Redirect", {}, newurl)


def test_a_redirect_to_another_host_drops_the_registry_token():
    assert _redirect("https://cdn.example/blob").get_header("Authorization") is None


def test_a_redirect_on_the_same_host_keeps_it():
    assert _redirect("https://ghcr.io/v2/other").get_header("Authorization") == "Bearer T0K"


def test_a_redirect_to_http_is_refused():
    with pytest.raises(urllib.error.HTTPError):
        _redirect("http://cdn.example/blob")


def test_a_non_string_media_type_is_refused_cleanly_not_a_type_error():
    """Item 6a: `top.get("mediaType") in INDEX_TYPES` hashes the value --
    an untrusted registry answering with an unhashable `mediaType` (a list
    or an object) must not crash the scanner with a TypeError; it is simply
    not an index, and the ordinary manifest validation refuses it with a
    clear message."""
    bogus_body, bogus_digest = _doc({"mediaType": ["not", "a", "string"]})
    docs = {bogus_digest: bogus_body}
    with pytest.raises(RegistryError, match="lists no layers"):
        client(Registry(bogus_digest, docs)).resolve("1.0", "linux/amd64", 10_000)


def test_registry_patterns_match_the_pipeline_rule():
    from pipeline.images.policy import registry_allowed as pipeline_rule

    patterns = ("docker.io", "ghcr.io", "*.dkr.ecr.*.amazonaws.com")
    for host in (
        "docker.io",
        "GHCR.io",
        "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com",
        "evil.io",
        "a.b.dkr.ecr.x.amazonaws.com",
        "registry.example.com:5000",
    ):
        assert registry_allowed(host, patterns) == pipeline_rule(host, patterns), host
