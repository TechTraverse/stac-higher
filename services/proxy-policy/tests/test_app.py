"""Tests for the derived proxy's app factory (R-3)."""

from __future__ import annotations

import importlib.metadata
import logging
import re
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from stac_auth_proxy import Settings
from stac_auth_proxy.middleware import EnforceAuthMiddleware
from starlette.testclient import TestClient

from stac_higher_proxy_policy import app as app_module
from stac_higher_proxy_policy.app import create_app
from stac_higher_proxy_policy.audience import AUDIENCE_CLAIM_ENV, ClaimAwareEnforceAuthMiddleware


@pytest.fixture(autouse=True)
def _clear_claim_env(monkeypatch):
    monkeypatch.delenv(AUDIENCE_CLAIM_ENV, raising=False)


def _settings() -> Settings:
    return Settings(
        upstream_url="http://upstream.invalid",
        oidc_discovery_url="http://idp.invalid/.well-known/openid-configuration",
        allowed_jwt_audiences="app-client",
    )


def test_create_app_swaps_in_the_claim_aware_middleware(monkeypatch):
    monkeypatch.setenv(AUDIENCE_CLAIM_ENV, "client_id")
    app = create_app(_settings())
    claim_aware = [m for m in app.user_middleware if m.cls is ClaimAwareEnforceAuthMiddleware]
    assert len(claim_aware) == 1
    assert not [m for m in app.user_middleware if m.cls is EnforceAuthMiddleware]
    assert claim_aware[0].kwargs["audience_claim"] == "client_id"
    assert claim_aware[0].kwargs["allowed_jwt_audiences"] == ["app-client"]


def test_create_app_defaults_to_aud():
    app = create_app(_settings())
    entry = next(m for m in app.user_middleware if m.cls is ClaimAwareEnforceAuthMiddleware)
    assert entry.kwargs["audience_claim"] == "aud"


def test_create_app_fails_fast_on_a_bad_claim(monkeypatch):
    monkeypatch.setenv(AUDIENCE_CLAIM_ENV, "client-id")
    with pytest.raises(ValueError, match="AUTH_BEARER_AUDIENCE_CLAIM"):
        create_app(_settings())


def test_create_app_raises_when_upstream_middleware_is_missing(monkeypatch):
    class Dummy:
        pass

    monkeypatch.setattr(app_module, "EnforceAuthMiddleware", Dummy)
    with pytest.raises(RuntimeError, match="EnforceAuthMiddleware"):
        create_app(_settings())


def test_client_id_mode_rejects_foreign_tokens_end_to_end(monkeypatch):
    """create_app → TestClient: the swapped middleware answers, not upstream's."""
    monkeypatch.setenv(AUDIENCE_CLAIM_ENV, "client_id")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    oidc = SimpleNamespace(
        metadata={},
        jwks_client=SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
        ),
    )
    # The only network hop on the 401 path is the lazy OIDC discovery fetch.
    monkeypatch.setattr(ClaimAwareEnforceAuthMiddleware, "oidc_config", property(lambda _: oidc))
    client = TestClient(create_app(_settings()))

    def post(claims: dict) -> Any:
        token = jwt.encode(
            {"exp": int(time.time()) + 3600, "sub": "u1", "token_use": "access", **claims},
            key,
            algorithm="RS256",
        )
        return client.post("/collections", json={}, headers={"Authorization": f"Bearer {token}"})

    # Upstream's aud-only check would answer "Invalid or expired token" (no aud
    # claim) for the first and accept the second; both must be audience 401s.
    for claims in ({"client_id": "other-client"}, {"client_id": "app-client", "aud": "app-client"}):
        response = post(claims)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid token audience"}
    assert client.post("/collections", json={}).status_code == 401


def _settings_without_audiences(monkeypatch) -> Settings:
    monkeypatch.delenv("ALLOWED_JWT_AUDIENCES", raising=False)
    return Settings(
        upstream_url="http://upstream.invalid",
        oidc_discovery_url="http://idp.invalid/.well-known/openid-configuration",
    )


def _audience_warnings(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "ALLOWED_JWT_AUDIENCES" in r.getMessage()
    ]


def test_client_id_mode_without_audiences_warns_at_startup(monkeypatch, caplog):
    monkeypatch.setenv(AUDIENCE_CLAIM_ENV, "client_id")
    with caplog.at_level(logging.WARNING, logger="stac_higher_proxy_policy"):
        create_app(_settings_without_audiences(monkeypatch))
    (record,) = _audience_warnings(caplog)
    assert "any client" in record.getMessage()


@pytest.mark.parametrize(
    ("claim", "with_audiences"), [("client_id", True), ("aud", False), ("aud", True)]
)
def test_no_audience_warning_otherwise(monkeypatch, caplog, claim, with_audiences):
    monkeypatch.setenv(AUDIENCE_CLAIM_ENV, claim)
    settings = _settings() if with_audiences else _settings_without_audiences(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="stac_higher_proxy_policy"):
        create_app(settings)
    assert not _audience_warnings(caplog)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# The upstream image is built with `uv sync --frozen` from upstream's own
# uv.lock, so it runs exactly that lock's versions. Values copied from
# https://github.com/developmentseed/stac-auth-proxy/blob/v<tag>/uv.lock.
# A base bump must add the new tag's row (ADR 0015 lockstep).
UPSTREAM_LOCKED = {
    "1.2.0": {"starlette": "1.3.1", "fastapi": "0.137.1", "pyjwt": "2.13.0"},
}


def _dockerfile_base_tag() -> str:
    dockerfile = PACKAGE_ROOT.parents[1] / "infra/proxy-policy/Dockerfile"
    match = re.search(
        r"FROM ghcr\.io/developmentseed/stac-auth-proxy:v([^@\s]+)@", dockerfile.read_text()
    )
    assert match, "Dockerfile FROM line not recognised"
    return match.group(1)


def test_dev_pin_matches_the_dockerfile_base():
    assert _dockerfile_base_tag() == importlib.metadata.version("stac-auth-proxy")


@pytest.mark.parametrize("dist", ["starlette", "fastapi", "pyjwt"])
def test_dev_extra_runs_the_base_images_locked_version(dist):
    tag = _dockerfile_base_tag()
    assert tag in UPSTREAM_LOCKED, f"add upstream v{tag}'s uv.lock versions to UPSTREAM_LOCKED"
    locked = UPSTREAM_LOCKED[tag][dist]
    dev = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]["dev"]
    assert f"{dist}=={locked}" in dev
    assert importlib.metadata.version(dist) == locked
