"""Claim-aware token audience check for the derived stac-auth-proxy image (R-3).

Upstream ``EnforceAuthMiddleware`` (pinned v1.2.0) hands ``ALLOWED_JWT_AUDIENCES``
to PyJWT, which matches the ``aud`` claim only and demands it when configured.
Cognito *access* tokens carry ``client_id`` instead of ``aud``, so every one is
rejected. ``AUTH_BEARER_AUDIENCE_CLAIM`` selects the claim:

- ``aud`` (default, also when unset or blank): upstream behaviour, unchanged.
- ``client_id``: ``ALLOWED_JWT_AUDIENCES`` holds the allowed client ids; the
  token's ``client_id`` must be a string equal to one of them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException, status
from stac_auth_proxy.middleware import EnforceAuthMiddleware

logger = logging.getLogger(__name__)

AUDIENCE_CLAIM_ENV = "AUTH_BEARER_AUDIENCE_CLAIM"
AudienceClaim = Literal["aud", "client_id"]


def resolve_audience_claim(environ: Mapping[str, str]) -> AudienceClaim:
    """Return the configured claim; unset or blank means ``aud``."""
    raw = environ.get(AUDIENCE_CLAIM_ENV, "")
    value = raw.strip()
    if not value:
        return "aud"
    if value == "aud" or value == "client_id":
        return value
    raise ValueError(
        f"{AUDIENCE_CLAIM_ENV} must be 'aud' or 'client_id' (or unset for 'aud'); got {raw!r}"
    )


@dataclass
class ClaimAwareEnforceAuthMiddleware(EnforceAuthMiddleware):
    """``EnforceAuthMiddleware`` that can match ``client_id`` instead of ``aud``."""

    audience_claim: str = "aud"
    allowed_client_ids: Sequence[str] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.audience_claim = resolve_audience_claim({AUDIENCE_CLAIM_ENV: self.audience_claim})
        if self.audience_claim == "client_id":
            # PyJWT must not see the list, or it would demand an ``aud`` claim.
            self.allowed_client_ids = self.allowed_jwt_audiences
            self.allowed_jwt_audiences = None

    def validate_token(
        self,
        auth_header: str | None,
        auto_error: bool = True,
        required_scopes: Sequence[str] | None = None,
    ) -> dict[str, Any] | None:
        payload = super().validate_token(auth_header, auto_error, required_scopes)
        if self.audience_claim == "aud" or payload is None or not self.allowed_client_ids:
            return payload
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or client_id not in self.allowed_client_ids:
            logger.warning("Token client_id not in ALLOWED_JWT_AUDIENCES")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token audience",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
