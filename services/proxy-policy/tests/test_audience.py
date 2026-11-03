"""Unit tests for the claim-aware auth middleware (AUTH_BEARER_AUDIENCE_CLAIM).

Real RS256 tokens signed with generated keys; the OIDC discovery is faked so no
network is touched. Upstream stac-auth-proxy 1.2.0 matches only the ``aud``
claim; Cognito access tokens carry ``client_id`` instead.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from stac_higher_proxy_policy.audience import (
    AUDIENCE_CLAIM_ENV,
    ClaimAwareEnforceAuthMiddleware,
    resolve_audience_claim,
)

PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = PRIVATE.public_key()


def sign(claims: dict[str, Any], key: Any = PRIVATE) -> str:
    return jwt.encode({"exp": int(time.time()) + 3600, **claims}, key, algorithm="RS256")


class FakeOidcConfig:
    metadata: dict[str, Any] = {}  # noqa: RUF012

    class jwks_client:
        @staticmethod
        def get_signing_key_from_jwt(token: str) -> SimpleNamespace:
            return SimpleNamespace(key=PUBLIC)


async def _noop_app(scope, receive, send):  # pragma: no cover - never reached
    pass


def make_mw(claim: str, audiences: list[str] | None, app: Any = _noop_app, **kwargs: Any):
    kwargs.setdefault("private_endpoints", {})
    kwargs.setdefault("public_endpoints", {})
    kwargs.setdefault("default_public", True)
    mw = ClaimAwareEnforceAuthMiddleware(
        app=app,
        oidc_discovery_url="http://idp.invalid/.well-known/openid-configuration",
        allowed_jwt_audiences=audiences,
        audience_claim=claim,
        **kwargs,
    )
    mw._oidc_config = FakeOidcConfig()
    return mw


def validate(mw: ClaimAwareEnforceAuthMiddleware, token: str):
    return mw.validate_token(f"Bearer {token}")


def assert_rejected(mw, token: str, detail: str = "Invalid token audience") -> None:
    with pytest.raises(HTTPException) as exc:
        validate(mw, token)
    assert exc.value.status_code == 401
    assert exc.value.detail == detail


COGNITO = {"sub": "u1", "client_id": "app-client", "token_use": "access", "scope": "openid"}


@pytest.mark.parametrize("raw", [None, "", "   ", "aud", " aud "])
def test_resolve_defaults_to_aud(raw):
    environ = {} if raw is None else {AUDIENCE_CLAIM_ENV: raw}
    assert resolve_audience_claim(environ) == "aud"


def test_resolve_accepts_client_id():
    assert resolve_audience_claim({AUDIENCE_CLAIM_ENV: " client_id "}) == "client_id"


@pytest.mark.parametrize("raw", ["client-id", "CLIENT_ID", "sub", "azp"])
def test_resolve_rejects_unknown_claims(raw):
    with pytest.raises(ValueError) as exc:
        resolve_audience_claim({AUDIENCE_CLAIM_ENV: raw})
    msg = str(exc.value)
    assert AUDIENCE_CLAIM_ENV in msg
    assert "'aud'" in msg
    assert "'client_id'" in msg
    assert repr(raw) in msg


def test_cognito_access_token_is_accepted():
    mw = make_mw("client_id", ["app-client"])
    assert validate(mw, sign(COGNITO))["client_id"] == "app-client"


def test_mismatched_client_id_is_rejected():
    assert_rejected(make_mw("client_id", ["app-client"]), sign({**COGNITO, "client_id": "other"}))


def test_missing_client_id_is_rejected():
    claims = {k: v for k, v in COGNITO.items() if k != "client_id"}
    assert_rejected(make_mw("client_id", ["app-client"]), sign(claims))


def test_non_string_client_id_is_rejected():
    assert_rejected(
        make_mw("client_id", ["app-client"]), sign({**COGNITO, "client_id": ["app-client"]})
    )


def test_token_carrying_aud_is_rejected_in_client_id_mode():
    assert_rejected(make_mw("client_id", ["app-client"]), sign({**COGNITO, "aud": "app-client"}))


def test_bad_signature_is_rejected_before_the_claim_check():
    assert_rejected(
        make_mw("client_id", ["app-client"]),
        sign(COGNITO, key=OTHER_PRIVATE),
        detail="Invalid or expired token",
    )


def test_client_id_mode_without_audiences_skips_the_check():
    mw = make_mw("client_id", None)
    assert validate(mw, sign({**COGNITO, "client_id": "anything"}))["client_id"] == "anything"


def test_client_id_mode_moves_audiences_off_pyjwt():
    mw = make_mw("client_id", ["app-client"])
    assert mw.allowed_jwt_audiences is None
    assert mw.allowed_client_ids == ["app-client"]


def test_bare_string_audience_is_a_one_element_list_not_a_substring_match():
    mw = make_mw("client_id", "app-client")  # type: ignore[arg-type]
    assert mw.allowed_client_ids == ["app-client"]
    assert_rejected(mw, sign({**COGNITO, "client_id": "app"}))


def test_unknown_audience_claim_field_fails_construction():
    with pytest.raises(ValueError, match=AUDIENCE_CLAIM_ENV):
        make_mw("sub", ["x"])


def test_aud_mode_accepts_matching_aud():
    mw = make_mw("aud", ["stac-higher"])
    assert validate(mw, sign({"sub": "u1", "aud": "stac-higher"}))["aud"] == "stac-higher"


def test_aud_mode_rejects_wrong_aud():
    assert_rejected(make_mw("aud", ["stac-higher"]), sign({"sub": "u1", "aud": "other"}))


def test_aud_mode_rejects_cognito_access_token():
    # PyJWT raises MissingRequiredClaimError (not InvalidAudienceError) here.
    assert_rejected(
        make_mw("aud", ["stac-higher"]), sign(COGNITO), detail="Invalid or expired token"
    )


def test_asgi_rejection_has_the_upstream_shape():
    async def endpoint(request: Request):
        return PlainTextResponse(request.state.payload["client_id"])

    app = Starlette(routes=[Route("/collections", endpoint, methods=["POST"])])
    wrapped = make_mw(
        "client_id",
        ["app-client"],
        app=app,
        private_endpoints={r"^/collections$": ["POST"]},
        default_public=False,
    )
    client = TestClient(wrapped)

    bad = client.post(
        "/collections", headers={"Authorization": f"Bearer {sign({**COGNITO, 'client_id': 'x'})}"}
    )
    assert bad.status_code == 401
    assert bad.json() == {"detail": "Invalid token audience"}

    good = client.post("/collections", headers={"Authorization": f"Bearer {sign(COGNITO)}"})
    assert good.status_code == 200
    assert good.text == "app-client"
