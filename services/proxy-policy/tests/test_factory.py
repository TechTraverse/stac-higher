"""Unit tests for ExternallyWritableItemsFilter (ADR 0015).

No database, no Docker: the set loader is injected. Every emitted expression
is validated with the same ``cql2`` engine stac-auth-proxy embeds, and match
semantics are asserted the way ``Cql2ValidateTransactionMiddleware`` uses them
(``Expr(...).matches(item_body)``).

Contexts mirror ``Cql2BuildFilterMiddleware.__call__`` (upstream 1.2.0):
``{"req": {path, method, query_params, path_params, headers}, "payload": ...}``
with lowercase header names (Starlette) — though the factory tolerates any
casing.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from cql2 import Expr

from stac_higher_proxy_policy import ExternallyWritableItemsFilter
from stac_higher_proxy_policy.factory import (
    CACHE_TTL_ENV,
    CONSTANT_FALSE,
    CONSTANT_TRUE,
    DATABASE_URL_ENV,
    ROLES_CLAIM_ENV,
    SECRET_ENV,
    WRITE_ROLES_ENV,
)

SECRET = "test-bff-shared-secret"

ITEMS = "/collections/flagged/items"
ITEM = "/collections/flagged/items/item-1"
BULK = "/collections/flagged/bulk_items"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate from the developer's real environment."""
    for var in (SECRET_ENV, DATABASE_URL_ENV, ROLES_CLAIM_ENV, WRITE_ROLES_ENV, CACHE_TTL_ENV):
        monkeypatch.delenv(var, raising=False)


def make_filter(allowed: set[str] | None = None, **kwargs: Any) -> ExternallyWritableItemsFilter:
    if "set_loader" not in kwargs:

        async def loader() -> set[str]:
            return allowed if allowed is not None else {"flagged"}

        kwargs["set_loader"] = loader
    kwargs.setdefault("bff_shared_secret", SECRET)
    return ExternallyWritableItemsFilter(**kwargs)


def ctx(
    method: str,
    path: str,
    *,
    roles: list[str] | None = None,
    headers: dict[str, str] | None = None,
    payload: Any = "unset",
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "req": {
            "path": path,
            "method": method,
            "query_params": {},
            "path_params": {},
            "headers": headers or {},
        }
    }
    if payload != "unset":
        context["payload"] = payload
    elif roles is not None:
        context["payload"] = {"realm_access": {"roles": roles}}
    else:
        context["payload"] = None  # anonymous read: EnforceAuth sets payload=None
    return context


def item_body(collection: str, item_id: str = "item-1") -> dict[str, Any]:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": collection,
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": {"datetime": "2026-01-01T00:00:00Z"},
        "links": [],
        "assets": {},
    }


def assert_valid_cql2(expr: str | dict) -> Expr:
    parsed = Expr(expr)
    parsed.validate()
    return parsed


# ---------------------------------------------------------------------------
# Reads are unrestricted — including the POST /search carve-out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ITEMS),
        ("GET", ITEM),
        ("GET", "/search"),
        ("POST", "/search"),  # a read served by POST — the explicit carve-out
        ("HEAD", ITEMS),
        ("OPTIONS", ITEMS),
    ],
)
async def test_reads_are_unrestricted(method: str, path: str):
    result = await make_filter()(ctx(method, path))
    assert result == CONSTANT_TRUE
    assert_valid_cql2(result)


async def test_collection_level_paths_are_untouched():
    # The proxy never routes these here (collections_filter_path wins and has
    # no filter), but even if it did, this factory stays out of the way —
    # collection transactions remain PRIVATE_ENDPOINTS-token-gated (ADR 0002).
    result = await make_filter()(ctx("POST", "/collections", roles=["member"]))
    assert result == CONSTANT_TRUE


# ---------------------------------------------------------------------------
# Role floor: operator/admin required for direct item writes
# ---------------------------------------------------------------------------


async def test_member_write_is_denied():
    result = await make_filter()(ctx("POST", ITEMS, roles=["member"]))
    assert result == CONSTANT_FALSE
    assert not assert_valid_cql2(result).matches(item_body("flagged"))


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"realm_access": {}}, {"realm_access": {"roles": "operator"}}],
)
async def test_missing_or_malformed_roles_claim_is_denied(payload: Any):
    result = await make_filter()(ctx("POST", ITEMS, payload=payload))
    assert result == CONSTANT_FALSE


@pytest.mark.parametrize("role", ["operator", "admin"])
async def test_write_roles_pass_the_role_floor(role: str):
    result = await make_filter()(ctx("POST", ITEMS, roles=["member", role]))
    assert isinstance(result, dict)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_all_write_methods_take_the_write_branch(method: str):
    path = ITEMS if method == "POST" else ITEM
    result = await make_filter()(ctx(method, path, roles=["member"]))
    assert result == CONSTANT_FALSE


async def test_custom_roles_claim_path():
    f = make_filter(roles_claim="resource_access.stac.roles")
    granted = await f(
        ctx("POST", ITEMS, payload={"resource_access": {"stac": {"roles": ["operator"]}}})
    )
    assert isinstance(granted, dict)
    denied = await f(ctx("POST", ITEMS, roles=["operator"]))  # default-shaped claim
    assert denied == CONSTANT_FALSE


async def test_write_roles_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(WRITE_ROLES_ENV, "publisher")
    f = make_filter(allowed={"flagged"})
    assert isinstance(await f(ctx("POST", ITEMS, roles=["publisher"])), dict)
    assert await f(ctx("POST", ITEMS, roles=["operator"])) == CONSTANT_FALSE


# ---------------------------------------------------------------------------
# The externally-writable set constraint
# ---------------------------------------------------------------------------


async def test_operator_write_is_scoped_to_the_externally_writable_set():
    result = await make_filter(allowed={"flagged", "also-flagged"})(
        ctx("POST", ITEMS, roles=["operator"])
    )
    assert result == {
        "op": "in",
        "args": [{"property": "collection"}, ["also-flagged", "flagged"]],
    }
    expr = assert_valid_cql2(result)
    assert expr.matches(item_body("flagged"))
    assert not expr.matches(item_body("unflagged"))


async def test_empty_set_renders_constant_false_never_in_empty():
    result = await make_filter(allowed=set())(ctx("POST", ITEMS, roles=["operator"]))
    assert result == CONSTANT_FALSE
    assert not assert_valid_cql2(result).matches(item_body("anything"))


# ---------------------------------------------------------------------------
# BFF shared-secret exemption
# ---------------------------------------------------------------------------


async def test_bff_header_exempts_item_writes():
    result = await make_filter(allowed=set())(
        ctx("POST", ITEMS, roles=["member"], headers={"x-bff-auth": SECRET})
    )
    assert result == CONSTANT_TRUE


async def test_bff_header_lookup_is_case_insensitive():
    result = await make_filter()(
        ctx("PUT", ITEM, roles=["member"], headers={"X-BFF-Auth": SECRET})
    )
    assert result == CONSTANT_TRUE


async def test_wrong_bff_secret_falls_through_to_policy():
    result = await make_filter()(
        ctx("POST", ITEMS, roles=["member"], headers={"x-bff-auth": "wrong-secret"})
    )
    assert result == CONSTANT_FALSE


async def test_bff_header_value_is_never_logged(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.DEBUG):
        f = make_filter()
        await f(ctx("POST", ITEMS, roles=["member"], headers={"x-bff-auth": "sneaky-attempt"}))
        await f(ctx("POST", BULK, roles=["operator"], headers={"x-bff-auth": "sneaky-attempt"}))
    assert "sneaky-attempt" not in caplog.text
    assert SECRET not in caplog.text


# ---------------------------------------------------------------------------
# bulk_items: constant false without the BFF header
# ---------------------------------------------------------------------------


async def test_bulk_items_denied_even_for_admin():
    result = await make_filter()(ctx("POST", BULK, roles=["operator", "admin"]))
    assert result == CONSTANT_FALSE


async def test_bulk_items_allowed_with_bff_header():
    result = await make_filter()(ctx("POST", BULK, headers={"x-bff-auth": SECRET}))
    assert result == CONSTANT_TRUE


async def test_bulk_items_get_is_a_read():
    # Only writes are gated; a (hypothetical) GET on the bulk path is not one.
    result = await make_filter()(ctx("GET", BULK))
    assert result == CONSTANT_TRUE


# ---------------------------------------------------------------------------
# Fail-fast configuration (proxy startup dies, not first request)
# ---------------------------------------------------------------------------


def test_missing_secret_fails_construction():
    with pytest.raises(RuntimeError, match=SECRET_ENV):

        async def loader() -> set[str]:
            return set()

        ExternallyWritableItemsFilter(set_loader=loader)


def test_missing_database_url_fails_construction():
    with pytest.raises(RuntimeError, match=DATABASE_URL_ENV):
        ExternallyWritableItemsFilter(bff_shared_secret=SECRET)


def test_env_fallbacks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(SECRET_ENV, SECRET)
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://ro@db:5432/postgis")
    f = ExternallyWritableItemsFilter()
    assert f.bff_shared_secret == SECRET
    assert f.database_url == "postgresql://ro@db:5432/postgis"
    assert f.roles_claim == "realm_access.roles"
    assert tuple(f.write_roles or ()) == ("operator", "admin")
    assert f.cache_ttl == 15.0


# ---------------------------------------------------------------------------
# TTL cache + failure posture
# ---------------------------------------------------------------------------


class CountingLoader:
    def __init__(self, result: set[str] | None = None, fail: bool = False):
        self.calls = 0
        self.result = result if result is not None else {"flagged"}
        self.fail = fail

    async def __call__(self) -> set[str]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("db down (test)")
        return self.result


async def test_set_is_cached_within_ttl():
    loader = CountingLoader()
    f = make_filter(set_loader=loader, cache_ttl=60.0)
    for _ in range(3):
        await f(ctx("POST", ITEMS, roles=["operator"]))
    assert loader.calls == 1


async def test_set_is_reloaded_after_ttl_expiry():
    loader = CountingLoader()
    f = make_filter(set_loader=loader, cache_ttl=0.0)
    await f(ctx("POST", ITEMS, roles=["operator"]))
    await f(ctx("POST", ITEMS, roles=["operator"]))
    assert loader.calls == 2


async def test_refresh_failure_serves_stale_set():
    loader = CountingLoader()
    f = make_filter(set_loader=loader, cache_ttl=0.0)
    first = await f(ctx("POST", ITEMS, roles=["operator"]))
    assert isinstance(first, dict)
    loader.fail = True
    second = await f(ctx("POST", ITEMS, roles=["operator"]))
    assert second == first  # stale set still enforced
    assert loader.calls == 2


async def test_refresh_failure_with_no_cache_fails_closed():
    loader = CountingLoader(fail=True)
    f = make_filter(set_loader=loader, cache_ttl=60.0)
    result = await f(ctx("POST", ITEMS, roles=["operator"]))
    assert result == CONSTANT_FALSE


# ---------------------------------------------------------------------------
# Upstream-contract pin: matches() raises on a missing property
# ---------------------------------------------------------------------------


async def test_in_filter_raises_on_item_body_without_collection():
    # Pins the documented sharp edge (factory docstring): the validate
    # middleware would surface this as a 500-shaped rejection, so direct
    # writers must include "collection" in the item body. If a cql2 upgrade
    # changes this to a clean False, this pin flips and the docs can soften.
    result = await make_filter()(ctx("POST", ITEMS, roles=["operator"]))
    expr = assert_valid_cql2(result)
    body = item_body("flagged")
    del body["collection"]
    # cql2 raises a bare Exception here (Rust-binding error surface).
    with pytest.raises(Exception, match="Could not reduce expression to boolean"):
        expr.matches(body)
