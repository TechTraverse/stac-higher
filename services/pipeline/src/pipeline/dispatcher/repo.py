"""Repository seam over the event outbox + delivery associations + pgstac items.

Mirrors pipeline.ingest.repo: a DispatchRepo ABC the loop depends on (unit-tested
against an in-memory fake) plus a psycopg PgDispatchRepo for production. Pg
methods open a short-lived AsyncConnection and are ``# pragma: no cover`` — the
SQL is exercised by the live dispatch verification (Task 9), not unit tests.

Ownership (ADR 0001/0007): reads stac_higher.item_events + collection_connections
+ collection_settings (gc_grace_days, for the §7.3 delete-event GC mark) and
pgstac items; UPDATEs only item_events.claimed_at/processed_at. Never runs DDL.
(The asset_gc INSERT itself goes through gc.repo.PgGcRepo — wired in
jobs/dispatch.py — so the mark SQL lives in exactly one place.)
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation


@dataclass(frozen=True)
class ItemEvent:
    id: int
    collection_id: str
    item_id: str
    op: str
    occurred_at: dt.datetime | None = None
    #: prior visibility deferrals (I-38): how many claims found the item not
    #: yet visible and released this event for retry.
    dispatch_attempts: int = 0


#: A claimed-but-unprocessed row older than this is presumed crashed and is
#: reclaimable (I-40): the claim is atomic (claimed_at stamped in the claiming
#: statement) so overlapping dispatch runs cannot double-claim inside the
#: window, while the crash direction stays safe — rows are redelivered, never
#: lost.
STALE_CLAIM_SECONDS = 600

#: mirror of the app's collection_settings default (migrate.ts: DEFAULT 30) —
#: used when a collection has no settings row.
DEFAULT_GC_GRACE_DAYS = 30


class DispatchRepo(abc.ABC):
    @abc.abstractmethod
    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        """Atomically claim pending outbox rows in id order: stamp claimed_at
        on unclaimed (or stale-claimed) unprocessed rows and return them
        (I-40 — FOR UPDATE SKIP LOCKED + claimed_at in one statement in Pg)."""

    @abc.abstractmethod
    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        """Stamp processed_at = now() for the given event ids."""

    @abc.abstractmethod
    async def release_for_retry(
        self, event_ids: Sequence[int], retry_delay_seconds: int
    ) -> None:
        """Release claimed events whose item was not yet visible (I-38): clear
        the claim, count the attempt, and push ``next_dispatch_at`` out by the
        cool-off so a drain-until-empty loop cannot burn the retry budget in
        one wake."""

    @abc.abstractmethod
    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        """Enabled direction='deliver' associations for a collection."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or None if not (yet) present."""

    @abc.abstractmethod
    async def get_gc_grace_days(self, collection_id: str) -> int:
        """The collection's ``gc_grace_days`` from collection_settings, or
        :data:`DEFAULT_GC_GRACE_DAYS` when no settings row exists (feeds the
        §7.3 delete-event GC mark's ``collect_after``)."""


@dataclass
class PgDispatchRepo(DispatchRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.item_events e"
                " SET claimed_at = now()"
                " WHERE e.id IN ("
                "   SELECT id FROM stac_higher.item_events"
                "    WHERE processed_at IS NULL"
                "      AND (claimed_at IS NULL"
                "           OR claimed_at < now() - make_interval(secs => %s))"
                # I-38: a visibility-deferred event is out of the claim window
                # until its cool-off passes.
                "      AND (next_dispatch_at IS NULL OR next_dispatch_at <= now())"
                "    ORDER BY id"
                "    FOR UPDATE SKIP LOCKED"
                "    LIMIT %s)"
                " RETURNING e.id, e.collection_id, e.item_id, e.op, e.occurred_at,"
                "           e.dispatch_attempts",
                (STALE_CLAIM_SECONDS, limit),
            )
            rows = await cur.fetchall()
            await conn.commit()
        # RETURNING order is not guaranteed — restore outbox (id) order.
        rows = sorted(rows, key=lambda r: int(r[0]))
        return [
            ItemEvent(
                id=int(r[0]),
                collection_id=r[1],
                item_id=r[2],
                op=r[3],
                occurred_at=r[4],
                dispatch_attempts=int(r[5]),
            )
            for r in rows
        ]

    async def mark_processed(self, event_ids: Sequence[int]) -> None:  # pragma: no cover
        if not event_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.item_events SET processed_at = now()"
                " WHERE id = ANY(%s)",
                (list(event_ids),),
            )
            await conn.commit()

    async def release_for_retry(  # pragma: no cover
        self, event_ids: Sequence[int], retry_delay_seconds: int
    ) -> None:
        if not event_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.item_events"
                " SET claimed_at = NULL,"
                "     dispatch_attempts = dispatch_attempts + 1,"
                "     next_dispatch_at = now() + make_interval(secs => %s)"
                " WHERE id = ANY(%s)",
                (retry_delay_seconds, list(event_ids)),
            )
            await conn.commit()

    async def list_deliver_associations(  # pragma: no cover
        self, collection_id: str
    ) -> list[DeliverAssociation]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT cc.id, cc.collection_id, cc.config"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.collection_id = %s AND cc.direction = 'deliver'"
                " AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL",
                (collection_id,),
            )
            rows = await cur.fetchall()
        return [
            DeliverAssociation(id=str(r[0]), collection_id=r[1], config=dict(r[2]) if r[2] else {})
            for r in rows
        ]

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT pgstac.get_item(%s, %s)", (item_id, collection_id)
            )
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None

    async def get_gc_grace_days(self, collection_id: str) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT gc_grace_days FROM stac_higher.collection_settings"
                " WHERE collection_id = %s",
                (collection_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else DEFAULT_GC_GRACE_DAYS
