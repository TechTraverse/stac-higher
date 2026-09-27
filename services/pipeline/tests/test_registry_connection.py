"""The ``registry`` connection protocol's check probe (C-1, container-images spec §5).

The transport is injected: no test touches the network, and every host is
allow-listed so ``resolve_pinned`` never consults DNS.
"""

from __future__ import annotations

import json

import pytest

from pipeline.connections import registry
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.registry import (
    HttpResponse,
    RegistryConfig,
    check_registry,
    parse_bearer_challenge,
    registry_api_host,
)
from pipeline.connections.registry import (
    test_registry_connection as probe_registry_connection,
)
from pipeline.connections.repo import ConnectionRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
CREDS = {"username": "bot", "password": "s3cr3t-token"}
ALLOW = frozenset({"ghcr.io", "registry-1.docker.io", "auth.docker.io"})


class FakeHttp:
    def __init__(self, *responses: HttpResponse):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        return self.responses.pop(0)


def _ok(status=200, headers=None):
    return HttpResponse(status=status, headers=headers or {}, body=b"")


def test_docker_hub_short_names_probe_the_api_host():
    assert registry_api_host("docker.io") == "registry-1.docker.io"
    assert registry_api_host("index.docker.io") == "registry-1.docker.io"
    assert registry_api_host("ghcr.io") == "ghcr.io"


def test_bearer_challenge_parsing():
    assert parse_bearer_challenge(
        'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
    ) == {"realm": "https://auth.docker.io/token", "service": "registry.docker.io"}
    assert parse_bearer_challenge('Basic realm="x"') is None
    assert parse_bearer_challenge(None) is None


def test_v2_answering_200_with_basic_auth_is_ok():
    http = FakeHttp(_ok(200))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is True
    url, headers = http.calls[0]
    assert url == "https://ghcr.io/v2/"
    assert headers["Authorization"].startswith("Basic ")


def test_bearer_registries_are_ok_when_the_token_endpoint_accepts_the_credentials():
    challenge = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'
    http = FakeHttp(_ok(401, {"www-authenticate": challenge}), _ok(200))
    result = check_registry(RegistryConfig("docker.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is True
    assert http.calls[0][0] == "https://registry-1.docker.io/v2/"
    assert http.calls[1][0] == "https://auth.docker.io/token?service=registry.docker.io"


def test_a_rejected_token_request_fails():
    http = FakeHttp(
        _ok(401, {"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="x"'}),
        _ok(401),
    )
    result = check_registry(RegistryConfig("docker.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert "rejected the credentials" in result["message"]


def test_a_401_without_a_bearer_challenge_is_a_rejection():
    http = FakeHttp(_ok(401, {"www-authenticate": 'Basic realm="x"'}))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert "rejected the credentials" in result["message"]


def test_an_http_token_realm_is_refused():
    http = FakeHttp(_ok(401, {"www-authenticate": 'Bearer realm="http://ghcr.io/token"'}))
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=http)
    assert result["ok"] is False
    assert len(http.calls) == 1


def test_an_unexpected_status_fails_naming_it():
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=FakeHttp(_ok(503)))
    assert result["ok"] is False and "503" in result["message"]


def test_egress_is_checked_before_any_request():
    http = FakeHttp()
    result = check_registry(RegistryConfig("127.0.0.1:5000"), CREDS, frozenset(), http_get=http)
    assert result["ok"] is False
    assert http.calls == []


def test_missing_credentials_fail_without_a_request():
    http = FakeHttp()
    result = check_registry(RegistryConfig("ghcr.io"), {"username": "bot"}, ALLOW, http_get=http)
    assert result["ok"] is False
    assert http.calls == []


def test_a_transport_error_is_a_failure_not_a_crash():
    def boom(url, headers):
        raise OSError("connection refused")

    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=boom)
    assert result["ok"] is False and "unreachable" in result["message"]


@pytest.mark.parametrize(
    "responses",
    [
        (_ok(401, {"www-authenticate": 'Basic realm="x"'}),),
        (_ok(401, {"www-authenticate": 'Bearer realm="https://ghcr.io/token"'}), _ok(403)),
        (_ok(500),),
    ],
)
def test_no_probe_message_ever_carries_the_secret(responses):
    result = check_registry(RegistryConfig("ghcr.io"), CREDS, ALLOW, http_get=FakeHttp(*responses))
    assert CREDS["password"] not in json.dumps(result)
    blocked = check_registry(RegistryConfig("10.0.0.1"), CREDS, frozenset(), http_get=FakeHttp())
    assert CREDS["password"] not in json.dumps(blocked)


@pytest.mark.asyncio
async def test_the_connection_probe_decrypts_and_checks(monkeypatch):
    seen = {}

    def fake_check(config, credentials, allow_hosts, *, http_get=None):
        seen.update(host=config.host, username=credentials["username"])
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr(registry, "check_registry", fake_check)
    row = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "GHCR.IO"},
        credentials=seal(json.dumps(CREDS), KEY), host_key=None,
    )
    assert (await probe_registry_connection(row, KEY, ALLOW))["ok"] is True
    assert seen == {"host": "ghcr.io", "username": "bot"}


@pytest.mark.asyncio
async def test_a_bad_config_or_envelope_is_a_failed_check():
    bad_config = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "https://x"},
        credentials=seal(json.dumps(CREDS), KEY), host_key=None,
    )
    assert (await probe_registry_connection(bad_config, KEY, ALLOW))["ok"] is False
    no_creds = ConnectionRow(
        id="c1", name="n", protocol="registry", config={"host": "ghcr.io"},
        credentials=None, host_key=None,
    )
    assert (await probe_registry_connection(no_creds, KEY, ALLOW))["ok"] is False
