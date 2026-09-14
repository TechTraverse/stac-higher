"""Process-wide async connection pools (M3-B, spec §3; scoping notes M3-S-D).

Every pipeline repo used to open a fresh ``psycopg.AsyncConnection`` per
statement — 4.19 ms measured, ~14 per ingested item, ~420 connections/s at the
M3 budget (≈1.8 core-seconds of connect per wall-clock second) plus a backend
fork each. Invisible at concurrency 1 behind the pgstac call; first-order the
moment M3-D raises concurrency, and a ``max_connections`` risk with it. This
module is the one place a pooled connection comes from.

Lazy by design: a pool is built with ``open=False`` and opened under a lock on
first use, so a DB-gated test or a CLI script that constructs a repo gets a
working pool with no wiring, and a process that never touches Postgres opens
nothing.

Every pooled connection runs ``configure_pgstac_session_async`` once, when it
is created — spec §4.2's stated home for M3-A's two pgstac session GUCs. They
are harmless on the non-pgstac statements the repos issue.

NOT pooled, deliberately:

- Procrastinate's ``PsycopgConnector`` owns its own psycopg pool;
- the dispatch listener holds a dedicated autocommit connection for LISTEN;
- ``ProcrastinateQueue.setup()`` runs before anything else exists, and
  ``check_connection()`` IS the health probe — it must reach the database, not
  report a pool's cached liveness.

ADR 0001: nothing here runs DDL.
"""

from __future__ import annotations

import asyncio
import logging

from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from pipeline.config import Settings
from pipeline.db.pgstac_session import configure_pgstac_session_async

logger = logging.getLogger(__name__)

#: DSN -> open pool. Process-wide: one pool per distinct database, shared by
#: every repo instance, because repos are constructed per job (they are frozen
#: dataclasses holding only a DSN) and must not each own connections.
_pools: dict[str, AsyncConnectionPool] = {}
_lock = asyncio.Lock()

#: Bounds the warm-up wait a caller can be held for under `_lock` (below) —
#: `_lock` is module-global, so under M3-D's concurrency, N callers racing a
#: cold pool would otherwise each queue behind psycopg_pool's 30 s default
#: timeout, stalling N x 30 s instead of N x 10 s. A pool that cannot warm up
#: within this bound is discarded (see `get_async_pool`); the next caller
#: retries against a fresh pool rather than inheriting a wedged one.
POOL_OPEN_TIMEOUT_SECONDS = 10.0


def pool_name(database_url: str) -> str:
    """A DSN identity safe to log and to serve on ``/health``.

    ``get_stats()`` is published under this string, so it carries neither the
    password nor the user — ``/health`` is unauthenticated inside the compose
    network.
    """
    parts = conninfo_to_dict(database_url)
    host = parts.get("host") or "localhost"
    port = parts.get("port") or "5432"
    dbname = parts.get("dbname") or "?"
    return f"{host}:{port}/{dbname}"


async def get_async_pool(database_url: str) -> AsyncConnectionPool:
    """The open pool for ``database_url``, creating and opening it on first use."""
    existing = _pools.get(database_url)
    if existing is not None:
        return existing

    async with _lock:
        # Re-check: another coroutine may have won the race to the lock.
        existing = _pools.get(database_url)
        if existing is not None:
            return existing

        settings = Settings.from_env()
        pool = AsyncConnectionPool(
            database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            open=False,
            # Runs once per NEW connection (not per checkout): spec §4.2.
            configure=configure_pgstac_session_async,
            name=pool_name(database_url),
        )
        # wait=True: block until min_size connections exist and have each run
        # `configure` — psycopg_pool's default (wait=False) only *schedules*
        # the min-size fill and returns immediately, so the very first
        # checkout can race that background fill and open an extra, unplanned
        # backend (M3-B review finding, live-reproduced: 3 backends for 5
        # sequential checkouts against a min_size=2 pool). S-D's "one backend
        # per repo call" proof depends on the pool being genuinely warm before
        # anyone can check a connection out of it. Bounded by
        # POOL_OPEN_TIMEOUT_SECONDS rather than psycopg_pool's 30 s default,
        # since `_lock` is module-global and every other caller queues behind
        # this one. As a side benefit, a `configure` hook that raises (e.g. a
        # bad GUC) now fails earlier and in one place: `wait()` raises
        # `PoolTimeout("pool initialization incomplete after N sec")` here,
        # at pool-open time, with the underlying cause logged by psycopg_pool
        # itself (WARNING level) — instead of surfacing later, on whichever
        # caller's checkout happens to race it.
        try:
            await pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT_SECONDS)
        except BaseException:
            # `wait()` already closes the pool on timeout, but close() is
            # idempotent and cancellation can land before that runs — either
            # way, a pool that never made it into `_pools` must not leak its
            # worker tasks. Registration only happens after this succeeds, so
            # the next caller retries against a fresh pool instead of finding
            # a half-open one in the registry.
            await pool.close()
            raise
        _pools[database_url] = pool
        logger.info(
            "db pool opened",
            extra={
                "pool": pool.name,
                "min_size": settings.db_pool_min,
                "max_size": settings.db_pool_max,
            },
        )
        return pool


async def close_pools() -> None:
    """Close every pool and clear the registry. Idempotent; safe with none open."""
    async with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        await pool.close()
        logger.info("db pool closed", extra={"pool": pool.name})


def pool_stats() -> dict[str, dict[str, int]]:
    """``psycopg_pool`` stats per pool, keyed by the redacted DSN identity.

    Served on ``/health``. ``pool_min``/``pool_max``/``pool_size``/
    ``pool_available``/``requests_waiting`` are always present; the cumulative
    counters (``connections_num``, ``requests_num``, ``requests_queued``,
    ``usage_ms``, …) appear only once they are non-zero, which is why callers
    must not index them blindly.
    """
    return {pool.name: pool.get_stats() for pool in _pools.values()}
