"""User-initiated backfill: repo seam + chunked bulk enqueue (Slice C, §6.4).

Late-added deliver associations apply to new items only; "backfill existing
items" is an explicit operator action. The app INSERTs a 'queued'
``stac_higher.delivery_backfills`` row (the ADR 0004 bridge pattern); the
pipeline sweep claims it, pages the collection's item ids out of pgstac in
``cursor_item_id`` order, and enqueues one batched ``pipeline.deliver`` job per
chunk — never per-item fan-out (§6.1). Progress (count + cursor) is recorded
after each chunk, so a crashed backfill resumes from its cursor when the sweep
reclaims it after the stale window; the enqueue-then-record order re-enqueues
at most one chunk on crash (at-least-once — the delivery worker's fingerprint
gates make the re-delivery cheap).

Mirrors the other repo seams: a ``BackfillRepo`` ABC (unit-tested against a
fake) plus a psycopg ``PgBackfillRepo``; Pg methods are ``# pragma: no cover``
— exercised by the live verification. Ownership (ADR 0001): reads
``collection_connections``/``connections`` and pgstac item ids; UPDATEs only
``delivery_backfills``. Never runs DDL.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any

from pipeline.dispatcher.loop import EnqueueDeliveries

logger = logging.getLogger(__name__)

#: items per batched deliver job (bounds one job's fan-out).
CHUNK_SIZE = 200
#: a 'running' row untouched this long is presumed crashed and reclaimable.
STALE_RUNNING_SECONDS = 900


@dataclass(frozen=True)
class BackfillJob:
    """One claimed delivery_backfills row plus its association's RAW state —
    the runnability policy over that state lives in ``blocked_reason``, a pure
    function above the Pg seam (dumb adapter, tested logic on top)."""

    id: str
    association_id: str
    collection_id: str
    items_enqueued: int = 0
    cursor_item_id: str | None = None
    direction: str = "deliver"
    #: association AND its connection enabled.
    enabled: bool = True
    #: association or connection soft-deleted (ADR 0009).
    deleted: bool = False


def blocked_reason(job: BackfillJob) -> str | None:
    """Why this backfill cannot run, or None when runnable. Persisted into
    ``delivery_backfills.error`` by the sweep, so keep the strings stable."""
    if job.deleted:
        return "association deleted"
    if job.direction != "deliver":
        return "not a deliver association"
    if not job.enabled:
        return "association or connection disabled"
    return None


class BackfillRepo(abc.ABC):
    @abc.abstractmethod
    async def claim_open_backfills(
        self, limit: int, stale_running_seconds: int
    ) -> list[BackfillJob]:
        """Atomically claim 'queued' rows — plus 'running' rows untouched for
        ``stale_running_seconds`` (crash resume) — oldest first: flip them to
        'running', stamp started_at/updated_at (FOR UPDATE SKIP LOCKED)."""

    @abc.abstractmethod
    async def list_item_ids(
        self, collection_id: str, after_item_id: str | None, limit: int
    ) -> list[str]:
        """The collection's item ids in id order, strictly after the cursor."""

    @abc.abstractmethod
    async def record_progress(
        self, backfill_id: str, items_enqueued: int, cursor_item_id: str
    ) -> None:
        """Persist the running total + cursor after a chunk is enqueued."""

    @abc.abstractmethod
    async def mark_completed(self, backfill_id: str) -> None: ...

    @abc.abstractmethod
    async def mark_failed(self, backfill_id: str, error: str) -> None: ...


async def run_backfill(
    repo: BackfillRepo,
    enqueue: EnqueueDeliveries,
    job: BackfillJob,
    *,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """Page the collection's items from the job's cursor and enqueue chunked
    deliver jobs until exhausted; returns the final items_enqueued total.
    ``asset_keys: None`` makes the deliver job re-derive keys from each item's
    current assets (the association's config ∩ the item — same as retries)."""
    cursor = job.cursor_item_id
    total = job.items_enqueued
    while True:
        item_ids = await repo.list_item_ids(job.collection_id, cursor, chunk_size)
        if not item_ids:
            break
        batch: dict[str, Any] = {
            "association_id": job.association_id,
            "items": [
                {"item_id": item_id, "asset_keys": None, "item_created_at": None}
                for item_id in item_ids
            ],
        }
        await enqueue([batch])
        cursor = item_ids[-1]
        total += len(item_ids)
        await repo.record_progress(job.id, total, cursor)
    await repo.mark_completed(job.id)
    return total


# The FK (delivery_backfills.association_id → collection_connections, ON
# DELETE RESTRICT) guarantees the join always matches, so the UPDATE..FROM
# cannot drop claimed rows.
_CLAIM_SQL = """
UPDATE stac_higher.delivery_backfills b
   SET status = 'running',
       started_at = COALESCE(b.started_at, now()),
       updated_at = now()
  FROM stac_higher.collection_connections cc
  JOIN stac_higher.connections c ON c.id = cc.connection_id
 WHERE cc.id = b.association_id
   AND b.id IN (
   SELECT id FROM stac_higher.delivery_backfills
    WHERE status = 'queued'
       OR (status = 'running'
           AND updated_at < now() - make_interval(secs => %s))
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT %s)
 RETURNING b.id, b.association_id, b.items_enqueued, b.cursor_item_id,
   cc.collection_id, cc.direction,
   (cc.enabled AND c.enabled),
   (cc.deleted_at IS NOT NULL OR c.deleted_at IS NOT NULL)
"""


@dataclass
class PgBackfillRepo(BackfillRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def claim_open_backfills(  # pragma: no cover
        self, limit: int, stale_running_seconds: int
    ) -> list[BackfillJob]:
        async with await self._connect() as conn:
            cur = await conn.execute(_CLAIM_SQL, (stale_running_seconds, limit))
            rows = await cur.fetchall()
            await conn.commit()
        return [
            BackfillJob(
                id=str(r[0]),
                association_id=str(r[1]),
                items_enqueued=int(r[2]),
                cursor_item_id=r[3],
                collection_id=r[4],
                direction=r[5],
                enabled=bool(r[6]),
                deleted=bool(r[7]),
            )
            for r in rows
        ]

    async def list_item_ids(  # pragma: no cover
        self, collection_id: str, after_item_id: str | None, limit: int
    ) -> list[str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id FROM pgstac.items"
                " WHERE collection = %s AND (%s::text IS NULL OR id > %s)"
                " ORDER BY id LIMIT %s",
                (collection_id, after_item_id, after_item_id, limit),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def record_progress(  # pragma: no cover
        self, backfill_id: str, items_enqueued: int, cursor_item_id: str
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_backfills"
                " SET items_enqueued = %s, cursor_item_id = %s, updated_at = now()"
                " WHERE id = %s",
                (items_enqueued, cursor_item_id, backfill_id),
            )
            await conn.commit()

    async def mark_completed(self, backfill_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_backfills"
                " SET status = 'completed', error = NULL,"
                "     finished_at = now(), updated_at = now()"
                " WHERE id = %s",
                (backfill_id,),
            )
            await conn.commit()

    async def mark_failed(self, backfill_id: str, error: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_backfills"
                " SET status = 'failed', error = %s,"
                "     finished_at = now(), updated_at = now()"
                " WHERE id = %s",
                (error, backfill_id),
            )
            await conn.commit()
