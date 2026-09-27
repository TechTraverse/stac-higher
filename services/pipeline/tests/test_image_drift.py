"""Tag drift (container-images spec §8.2, §5): one HEAD on the tag the image
was added with, through the egress policy, never fatal, never leaking the
credential."""

from __future__ import annotations

import logging

import pytest

from pipeline.connections.egress import EgressBlocked
from pipeline.connections.registry import HttpResponse
from pipeline.images import drift as drift_mod
from pipeline.images.drift import DriftCheckFailed, head_tag_digest, tag_drift
from pipeline.images.repo import ImageRow
from pipeline.process.executor import RegistryAuth

SCANNED = "sha256:" + "a" * 64
MOVED = "sha256:" + "b" * 64
HUB_CHALLENGE = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
IMAGE = ImageRow(
    id="7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
    reference="docker.io/library/python",
    tag_at_add="3.12-slim",
    status="approved",
    digest=SCANNED,
)


@pytest.fixture
def pinned(monkeypatch):
    """No DNS in unit tests: record which hosts would have been resolved."""
    hosts: list[str] = []

    def fake_resolve(host, allow_hosts=()):
        hosts.append(host)
        return []

    monkeypatch.setattr(drift_mod, "resolve_pinned", fake_resolve)
    return hosts


class Registry:
    """A scripted registry: answers in order, records every request."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        return self.responses.pop(0)


def ok(digest: str = MOVED) -> HttpResponse:
    return HttpResponse(200, {"docker-content-digest": digest}, b"")


def challenge(header: str = HUB_CHALLENGE) -> HttpResponse:
    return HttpResponse(401, {"www-authenticate": header}, b"")


def token() -> HttpResponse:
    return HttpResponse(200, {}, b'{"token": "t0k3n"}')


def test_anonymous_docker_hub_head_follows_the_bearer_challenge(pinned):
    registry = Registry(challenge(), token(), ok())
    digest = head_tag_digest(
        IMAGE.reference, IMAGE.tag_at_add, None, frozenset(), request=registry
    )
    assert digest == MOVED
    (m1, u1, h1), (m2, u2, h2), (m3, _u3, h3) = registry.calls
    assert (m1, u1) == (
        "HEAD", "https://registry-1.docker.io/v2/library/python/manifests/3.12-slim"
    )
    assert "Authorization" not in h1
    assert m2 == "GET" and u2.startswith("https://auth.docker.io/token?")
    assert "scope=repository%3Alibrary%2Fpython%3Apull" in u2
    assert "service=registry.docker.io" in u2
    assert "Authorization" not in h2  # anonymous: no Basic header to the realm
    assert (m3, h3["Authorization"]) == ("HEAD", "Bearer t0k3n")
    assert "application/vnd.oci.image.index.v1+json" in h3["Accept"]
    assert pinned == ["registry-1.docker.io", "auth.docker.io"]


def test_a_credential_goes_only_to_the_token_realm_as_basic(pinned):
    registry = Registry(challenge(), token(), ok())
    head_tag_digest(
        IMAGE.reference, IMAGE.tag_at_add, RegistryAuth("robot", "pat"), frozenset(),
        request=registry,
    )
    assert registry.calls[1][2]["Authorization"].startswith("Basic ")
    assert registry.calls[2][2]["Authorization"] == "Bearer t0k3n"


def test_a_basic_challenge_uses_the_credential(pinned):
    registry = Registry(challenge('Basic realm="r"'), ok())
    head_tag_digest(
        "ghcr.io/org/tool", "1.0", RegistryAuth("robot", "pat"), frozenset(), request=registry
    )
    assert registry.calls[1][2]["Authorization"].startswith("Basic ")


def test_a_basic_challenge_without_a_credential_fails(pinned):
    with pytest.raises(DriftCheckFailed):
        head_tag_digest(
            "ghcr.io/org/tool", "1.0", None, frozenset(),
            request=Registry(challenge('Basic realm="r"')),
        )


def test_a_token_realm_that_is_not_https_is_refused(pinned):
    registry = Registry(challenge('Bearer realm="http://evil.example/token"'))
    with pytest.raises(DriftCheckFailed, match="not https"):
        head_tag_digest(IMAGE.reference, IMAGE.tag_at_add, None, frozenset(), request=registry)
    assert len(registry.calls) == 1


def test_a_missing_or_malformed_digest_header_fails(pinned):
    with pytest.raises(DriftCheckFailed):
        head_tag_digest(
            "ghcr.io/org/tool", "1.0", None, frozenset(),
            request=Registry(HttpResponse(200, {"docker-content-digest": "md5:x"}, b"")),
        )


def test_tag_drift_says_whether_the_tag_moved(pinned):
    moved = tag_drift(IMAGE, None, frozenset(), request=Registry(ok(MOVED)))
    assert moved == {"current_digest": MOVED, "drifted": True}
    same = tag_drift(IMAGE, None, frozenset(), request=Registry(ok(SCANNED)))
    assert same == {"current_digest": SCANNED, "drifted": False}


@pytest.mark.parametrize(
    "failure",
    [
        HttpResponse(404, {}, b""),
        HttpResponse(500, {}, b""),
    ],
)
def test_a_registry_failure_is_recorded_not_raised(pinned, failure):
    assert tag_drift(IMAGE, None, frozenset(), request=Registry(failure)) == {
        "current_digest": None,
        "drifted": False,
    }


def test_an_unreachable_registry_is_recorded_not_raised(pinned):
    def boom(method, url, headers):
        raise OSError("connection refused")

    assert tag_drift(IMAGE, None, frozenset(), request=boom)["current_digest"] is None


def test_an_egress_refusal_is_recorded_not_raised(monkeypatch):
    def refuse(host, allow_hosts=()):
        raise EgressBlocked(f"{host} resolves to a private address")

    monkeypatch.setattr(drift_mod, "resolve_pinned", refuse)
    assert tag_drift(IMAGE, None, frozenset(), request=Registry())["current_digest"] is None


def test_no_log_line_carries_the_credential(pinned, caplog):
    """A token IS minted before the failure (a 404 on the retried HEAD), so
    this exercises the real leak surface: neither the password nor the
    minted token may appear in the formatted log text or in any record's
    extra fields."""
    caplog.set_level(logging.DEBUG)
    registry = Registry(challenge(), token(), HttpResponse(404, {}, b""))
    result = tag_drift(IMAGE, RegistryAuth("robot", "s3cr3t-pat"), frozenset(), request=registry)
    assert result["current_digest"] is None
    assert "s3cr3t-pat" not in caplog.text
    assert "t0k3n" not in caplog.text
    for record in caplog.records:
        for value in vars(record).values():
            assert "s3cr3t-pat" not in str(value)
            assert "t0k3n" not in str(value)


def test_a_deeply_nested_token_body_is_recorded_not_raised(pinned):
    """RecursionError (a RuntimeError, not a ValueError) from json.loads on a
    hostile, deeply nested body must not escape tag_drift."""
    registry = Registry(challenge(), HttpResponse(200, {}, b"[" * 60_000))
    assert tag_drift(IMAGE, None, frozenset(), request=registry) == {
        "current_digest": None,
        "drifted": False,
    }


def test_a_reference_with_no_repository_path_fails_head_tag_digest(pinned):
    with pytest.raises(DriftCheckFailed):
        head_tag_digest("justahost", "latest", None, frozenset(), request=Registry())


def test_a_reference_with_no_repository_path_is_recorded_not_raised(pinned):
    image = ImageRow(
        id=IMAGE.id, reference="justahost", tag_at_add="latest", status="approved", digest=None
    )
    assert tag_drift(image, None, frozenset(), request=Registry())["current_digest"] is None


def test_a_malformed_token_realm_url_is_recorded_not_raised(pinned):
    registry = Registry(challenge('Bearer realm="https://[::1"'))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_a_token_realm_with_an_out_of_range_port_is_recorded_not_raised(pinned):
    registry = Registry(challenge('Bearer realm="https://auth.example:99999/token"'))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_malformed_token_json_is_recorded_not_raised(pinned):
    registry = Registry(challenge(), HttpResponse(200, {}, b"not json"))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_token_json_without_a_token_field_is_recorded_not_raised(pinned):
    registry = Registry(challenge(), HttpResponse(200, {}, b"{}"))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_a_bearer_challenge_with_no_realm_is_recorded_not_raised(pinned):
    registry = Registry(challenge('Bearer service="registry.docker.io"'))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_a_non_200_token_response_is_recorded_not_raised(pinned):
    registry = Registry(challenge(), HttpResponse(403, {}, b""))
    assert tag_drift(IMAGE, None, frozenset(), request=registry)["current_digest"] is None


def test_a_fragment_on_the_token_realm_is_stripped_before_the_query(pinned):
    registry = Registry(
        challenge('Bearer realm="https://auth.docker.io/token#frag"'), token(), ok()
    )
    head_tag_digest(IMAGE.reference, IMAGE.tag_at_add, None, frozenset(), request=registry)
    token_url = registry.calls[1][1]
    assert "#" not in token_url
    assert "scope=repository%3Alibrary%2Fpython%3Apull" in token_url
