"""pgstac data-access seam (ROADMAP §6.1 ITEMIZE).

`PgstacWriter` is the ABC ITEMIZE depends on (so it unit-tests against a fake).
`PgPgstacWriter` implements both operations ITEMIZE needs from pgstac: the item
upsert, and the collection-extent read backing the ISSUE I-27 geometry
fallback. Upsert wraps pypgstac's synchronous `Loader.load_items(...,
Methods.upsert)` in `asyncio.to_thread`, over a small process-wide pool whose
connections carry `pgstac.use_queue` + `pgstac.update_collection_extent` as
SESSION GUCs (M3-A, spec §4.2) — partition statistics are queued rather than
recomputed inline on every write; `pipeline.pgstac_queue_drain` runs the queue.
ADR 0001: upsert writes item DATA only (temp `ON COMMIT DROP` staging tables +
pgstac's own `upsert_item` functions — no DDL, no migrations). A missing
collection is a permanent error surfaced as `CollectionMissing` (→ group
failed); anything else propagates so the job retries.
"""

from __future__ import annotations

import abc
import asyncio
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg_pool import ConnectionPool

from pipeline.db.pgstac_session import configure_pgstac_session


class CollectionMissing(Exception):
    """The item's `collection` does not exist in pgstac (create it first)."""


class PgstacWriter(abc.ABC):
    """The pgstac data-access seam ITEMIZE depends on: item upsert plus the
    collection-extent read used by the ISSUE I-27 geometry fallback."""

    @abc.abstractmethod
    async def upsert_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        """Upsert STAC item dicts into pgstac, replacing by id."""

    @abc.abstractmethod
    async def get_collection_bbox(self, collection_id: str) -> list[float] | None:
        """The collection's overall extent bbox (``extent.spatial.bbox[0]``),
        or ``None`` if the collection/extent is absent. Backs the ISSUE I-27
        opt-in collection-extent geometry fallback (Slice B4a)."""


#: How many upserts may run at once against pgstac. Upserts run in
#: `asyncio.to_thread`, so this — not the worker's concurrency — is the cap.
#: Measured cost is ~4 ms per single-item upsert with `use_queue` on (S-A §1),
#: so four is ample headroom for M3-D's concurrency of 12; raise it with
#: evidence, not by default.
WRITER_POOL_MAX = 4

_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


def writer_pool(dsn: str) -> ConnectionPool:
    """The process-wide pool the pgstac writer draws on, one per DSN.

    Every connection it opens carries the two pgstac session GUCs (M3-A, spec
    §4.2) through the `configure` hook. Opened on first use — construction of a
    `PgPgstacWriter` stays connection-free, as it always has been.

    Writer-only: pypgstac sets `autocommit = True` on every connection it
    checks out and the pool does not reset that, so nothing else may borrow
    from this pool.
    """
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=WRITER_POOL_MAX,
                configure=configure_pgstac_session,
                open=True,
                name="pgstac-writer",
            )
            _POOLS[dsn] = pool
        return pool


def close_writer_pools() -> None:
    """Close every writer pool (service shutdown; test isolation)."""
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


@dataclass
class PgPgstacWriter(PgstacWriter):
    dsn: str

    async def upsert_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        try:
            await asyncio.to_thread(self._upsert_sync, list(items))
        except CollectionMissing:
            raise
        except Exception as exc:
            if "is not present in the database" in str(exc):
                raise CollectionMissing(str(exc)) from exc
            raise

    def _upsert_sync(self, items: list[Mapping[str, Any]]) -> None:
        from pypgstac.load import Loader, Methods

        with self._open_pgstac() as db:
            Loader(db=db).load_items(items, insert_mode=Methods.upsert)

    def _open_pgstac(self):
        """A `PgstacDB` over the writer pool.

        `use_queue=True` is belt and braces: the pool's `configure` hook has
        already set both GUCs on the connection pypgstac is about to check out.
        pypgstac returns the connection to the pool on `__exit__`.
        """
        from pypgstac.db import PgstacDB

        return PgstacDB(pool=writer_pool(self.dsn), use_queue=True)

    async def get_collection_bbox(  # pragma: no cover - thin pool wrapper
        self, collection_id: str
    ) -> list[float] | None:
        # M3-B: the async pool, same as the repos. (The UPSERT path keeps its
        # own SYNC pool from M3-A — pypgstac is synchronous and runs in
        # asyncio.to_thread.)
        from pipeline.db.pool import get_async_pool

        pool = await get_async_pool(self.dsn)
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT content->'extent'->'spatial'->'bbox'->0"
                " FROM pgstac.collections WHERE id = %s",
                (collection_id,),
            )
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return [float(v) for v in row[0]]
