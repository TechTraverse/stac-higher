"""App factory for the derived stac-auth-proxy image (R-3).

Builds upstream's app, then swaps its single ``EnforceAuthMiddleware`` entry
for the claim-aware subclass so ``AUTH_BEARER_AUDIENCE_CLAIM`` takes effect.
The setting is resolved eagerly: a bad value stops the proxy at startup.
"""

from __future__ import annotations

import os

import stac_auth_proxy
from fastapi import FastAPI
from stac_auth_proxy import Settings
from stac_auth_proxy.middleware import EnforceAuthMiddleware
from starlette.middleware import Middleware

from stac_higher_proxy_policy.audience import (
    ClaimAwareEnforceAuthMiddleware,
    resolve_audience_claim,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    claim = resolve_audience_claim(os.environ)
    app = stac_auth_proxy.create_app(settings)
    indexes = [i for i, m in enumerate(app.user_middleware) if m.cls is EnforceAuthMiddleware]
    if len(indexes) != 1:
        raise RuntimeError(
            "upstream stac-auth-proxy no longer registers exactly one EnforceAuthMiddleware; "
            "re-check the derived image against the new version (ADR 0015)"
        )
    entry = app.user_middleware[indexes[0]]
    # Starlette builds the middleware stack lazily on the first request, so an
    # in-place swap here is picked up. One code path in both claim modes.
    app.user_middleware[indexes[0]] = Middleware(
        ClaimAwareEnforceAuthMiddleware, *entry.args, **entry.kwargs, audience_claim=claim
    )
    return app
