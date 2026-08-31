"""stac-auth-proxy items-filter factory enforcing ``externally_writable`` (ADR 0015).

Loaded by stac-auth-proxy (pinned v1.2.0) via ``ITEMS_FILTER_CLS`` in the
auth-enforced compose overlay only. Per request, ``Cql2BuildFilterMiddleware``
calls the instance with::

    {
        "req": {"path", "method", "query_params", "path_params", "headers"},
        **scope["state"],   # notably "payload": the validated JWT claims
    }

and the returned CQL2 expression (text or CQL2-JSON dict) is validated and
placed on request state. For reads it is appended to the query string /
response-validated; for transaction requests ``Cql2ValidateTransactionMiddleware``
evaluates it against the item body (``matches``) and rejects mismatches.

Policy (spec §5.2 / ADR 0015):

- **Reads are unrestricted** (constant true) — including ``POST /search``,
  which is a read served by POST: the write branch keys on transaction-shaped
  item paths, never on method alone. This factory is deliberately write-only
  policy; it is the single seam where I-1's read filter would later land.
- **Item writes** (POST/PUT/PATCH/DELETE on ``/collections/{id}/items[/...]``):

  1. requests carrying the BFF shared-secret header (``X-BFF-Auth``) are
     unrestricted — app-mediated writes are RBAC-gated and audited app-side
     (ADR 0008) and must reach every collection. The secret is MANDATORY:
     construction fails (and therefore proxy startup fails) when
     ``CATALOG_BFF_SHARED_SECRET`` is unset. Comparison is constant-time and
     the header value is never logged.
  2. otherwise the token's roles claim (default ``realm_access.roles``) must
     include ``operator`` or ``admin``, and the write is constrained to
     ``collection IN (<externally-writable set>)`` — the set read from
     ``stac_higher.collection_settings`` over a read-only connection with a
     short TTL cache (~15 s). An empty set renders constant false, never an
     invalid ``IN ()``.

- **``bulk_items`` is denied** (constant false) without the BFF header — bulk
  push is unsupported in Phase 7; the bulk body is never parsed here.
- Collection-level transactions (``POST /collections`` …) never reach this
  factory: the proxy routes them via ``collections_filter_path``, which has no
  configured filter — they stay PRIVATE_ENDPOINTS-token-gated (ADR 0002).

Failure posture for the DB set: serve the last-known-good set when a refresh
fails; with no cached set at all, fail CLOSED (constant false) rather than
letting writes through unvalidated.

NOTE (source-verified, upstream 1.2.0): ``Expr.matches`` raises when the body
lacks a referenced property, so a direct-push item body that omits
``"collection"`` is rejected 500-shaped rather than 403 — direct writers must
include ``collection`` in the item body (the STAC Transaction spec puts it
there anyway). Pinned by the integration legs at P7-Z.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Constant-true/false as REAL CQL2 comparison expressions, not the bare
# boolean literals: the proxy serializes the factory's expression onto reads
# (`filter=<text>` on GET, cql2-json into POST /search bodies), and
# stac-fastapi's pydantic models reject a bare `true` (`filter` must be a
# dict in json form) — observed live at P7-Z. `1 = 1` / `1 <> 1` parse under
# cql2, convert to valid cql2-json, and pgstac evaluates them.
CONSTANT_TRUE = "1 = 1"
CONSTANT_FALSE = "1 <> 1"

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Transaction-shaped ITEM paths only (never /search, never /collections).
# Kept in lockstep with the overlay's ITEMS_FILTER_PATH override, which must
# also route bulk_items here (the upstream default items_filter_path does not).
ITEMS_TRANSACTION_PATH = re.compile(
    r"^/collections/(?P<collection_id>[^/]+)/(?P<kind>items|bulk_items)(?:/(?P<item_id>[^/]+))?$"
)

BFF_HEADER = "x-bff-auth"

SECRET_ENV = "CATALOG_BFF_SHARED_SECRET"
DATABASE_URL_ENV = "POLICY_DATABASE_URL"
ROLES_CLAIM_ENV = "POLICY_ROLES_CLAIM"
WRITE_ROLES_ENV = "POLICY_WRITE_ROLES"
CACHE_TTL_ENV = "POLICY_CACHE_TTL_SECONDS"

DEFAULT_ROLES_CLAIM = "realm_access.roles"
DEFAULT_WRITE_ROLES = ("operator", "admin")
DEFAULT_CACHE_TTL_SECONDS = 15.0

SetLoader = Callable[[], Awaitable[Collection[str]]]


def _claim_path(payload: Any, path: str) -> Any:
    """Resolve a dotted claim path against the JWT payload; None when absent."""
    value = payload
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


@dataclass
class ExternallyWritableItemsFilter:
    """Async CQL2 filter builder for stac-auth-proxy's ``items_filter`` seam.

    Explicit kwargs (via ``ITEMS_FILTER_KWARGS``) take precedence over env
    vars. ``set_loader`` exists for unit tests; production resolves it from
    ``POLICY_DATABASE_URL`` at construction time.
    """

    database_url: str | None = None
    bff_shared_secret: str | None = None
    roles_claim: str | None = None
    write_roles: Sequence[str] | None = None
    cache_ttl: float | None = None
    set_loader: SetLoader | None = None

    _cache: frozenset[str] | None = field(default=None, init=False, repr=False)
    _cache_at: float = field(default=0.0, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        # Mandatory shared secret: fail fast at startup (the proxy instantiates
        # this while configuring middleware), never at first request. "When
        # configured" semantics would silently break every UI write to
        # non-externally-writable collections on a misconfigured deployment.
        secret = self.bff_shared_secret or os.environ.get(SECRET_ENV) or ""
        if not secret:
            raise RuntimeError(
                f"{SECRET_ENV} is required for the proxy write policy (ADR 0015); "
                "refusing to start without the BFF shared secret"
            )
        self.bff_shared_secret = secret

        if self.set_loader is None:
            dsn = self.database_url or os.environ.get(DATABASE_URL_ENV) or ""
            if not dsn:
                raise RuntimeError(
                    f"{DATABASE_URL_ENV} is required for the proxy write policy (ADR 0015); "
                    "it must point at the app database (read-only role suffices)"
                )
            self.database_url = dsn
            from . import db  # deferred so unit tests never need psycopg loadable

            def _load() -> Awaitable[Collection[str]]:
                return db.load_externally_writable_set(dsn)

            self.set_loader = _load

        if self.roles_claim is None:
            self.roles_claim = os.environ.get(ROLES_CLAIM_ENV, DEFAULT_ROLES_CLAIM)
        if self.write_roles is None:
            raw = os.environ.get(WRITE_ROLES_ENV)
            self.write_roles = (
                tuple(part.strip() for part in raw.split(",") if part.strip())
                if raw
                else DEFAULT_WRITE_ROLES
            )
        if self.cache_ttl is None:
            self.cache_ttl = float(os.environ.get(CACHE_TTL_ENV, str(DEFAULT_CACHE_TTL_SECONDS)))

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        """Build the CQL2 expression for one request context."""
        req = context.get("req") or {}
        method = str(req.get("method") or "").upper()
        path = str(req.get("path") or "")

        match = ITEMS_TRANSACTION_PATH.match(path)
        if method not in WRITE_METHODS or match is None:
            # Reads (GET/HEAD/OPTIONS anywhere, and POST /search — a read
            # served by POST) are unrestricted. Non-item paths that the proxy
            # might ever route here are likewise untouched.
            return CONSTANT_TRUE

        if self._bff_exempt(req.get("headers") or {}):
            return CONSTANT_TRUE

        if match.group("kind") == "bulk_items":
            # Bulk push is unsupported in Phase 7: deny, never parse the body.
            logger.info("denying bulk_items write without BFF exemption (%s %s)", method, path)
            return CONSTANT_FALSE

        roles = _claim_path(context.get("payload"), self.roles_claim)
        if not isinstance(roles, list | tuple) or not any(
            role in self.write_roles for role in roles
        ):
            logger.debug("denying item write: token lacks a write role (%s %s)", method, path)
            return CONSTANT_FALSE

        allowed = await self._externally_writable_set()
        if not allowed:
            # Never render IN () — an empty set is a constant-false policy.
            return CONSTANT_FALSE
        return {"op": "in", "args": [{"property": "collection"}, sorted(allowed)]}

    def _bff_exempt(self, headers: dict[str, Any]) -> bool:
        """Constant-time check of the BFF shared-secret header.

        Starlette lowercases header names, but the lookup is case-insensitive
        anyway. The presented value is NEVER logged. A wrong value simply
        falls through to the role/flag policy — no oracle beyond the boolean.
        """
        presented: str | None = None
        for key, value in headers.items():
            if str(key).lower() == BFF_HEADER:
                presented = str(value)
                break
        if presented is None:
            return False
        assert self.bff_shared_secret is not None  # __post_init__ guarantees
        return hmac.compare_digest(presented.encode(), self.bff_shared_secret.encode())

    async def _externally_writable_set(self) -> frozenset[str]:
        """The externally-writable collection ids, TTL-cached (~15 s)."""
        cached = self._fresh_cache()
        if cached is not None:
            return cached
        assert self.set_loader is not None  # __post_init__ guarantees
        async with self._lock:
            cached = self._fresh_cache()
            if cached is not None:
                return cached
            try:
                loaded = await self.set_loader()
            except Exception:
                if self._cache is not None:
                    # Serve stale (do NOT bump the timestamp: keep retrying).
                    logger.warning(
                        "externally-writable set refresh failed; serving stale set",
                        exc_info=True,
                    )
                    return self._cache
                logger.warning(
                    "externally-writable set refresh failed with nothing cached; failing closed",
                    exc_info=True,
                )
                return frozenset()
            self._cache = frozenset(str(collection_id) for collection_id in loaded)
            self._cache_at = time.monotonic()
            return self._cache

    def _fresh_cache(self) -> frozenset[str] | None:
        if self._cache is not None and (time.monotonic() - self._cache_at) < (self.cache_ttl or 0):
            return self._cache
        return None
