"""Tests for the derived proxy's app factory (R-3)."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

import pytest
from stac_auth_proxy import Settings
from stac_auth_proxy.middleware import EnforceAuthMiddleware

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


def test_dev_pin_matches_the_dockerfile_base():
    dockerfile = Path(__file__).resolve().parents[3] / "infra/proxy-policy/Dockerfile"
    match = re.search(
        r"FROM ghcr\.io/developmentseed/stac-auth-proxy:v([^@\s]+)@", dockerfile.read_text()
    )
    assert match, "Dockerfile FROM line not recognised"
    assert match.group(1) == importlib.metadata.version("stac-auth-proxy")
