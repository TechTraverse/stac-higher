"""Repository seam over the event outbox + delivery associations + pgstac items.

Mirrors pipeline.ingest.repo: a DispatchRepo ABC the loop depends on (unit-tested
against an in-memory fake) plus a psycopg PgDispatchRepo for production. Pg
methods open a short-lived AsyncConnection and are ``# pragma: no cover`` — the
SQL is exercised by the live dispatch verification (Task 9), not unit tests.

Ownership (ADR 0001/0007): reads stac_higher.item_events + collection_connections
and pgstac items; UPDATEs only item_events.processed_at. Never runs DDL.
"""

from __future__ import annotations

import abc
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


class DispatchRepo(abc.ABC):
    @abc.abstractmethod
    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        """Pending outbox rows in id order (FOR UPDATE SKIP LOCKED in Pg)."""

    @abc.abstractmethod
    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        """Stamp processed_at = now() for the given event ids."""

    @abc.abstractmethod
    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        """Enabled direction='deliver' associations for a collection."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or None if not (yet) present."""


@dataclass
class PgDispatchRepo(DispatchRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, collection_id, item_id, op FROM stac_higher.item_events"
                " WHERE processed_at IS NULL ORDER BY id"
                " FOR UPDATE SKIP LOCKED LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [ItemEvent(id=int(r[0]), collection_id=r[1], item_id=r[2], op=r[3]) for r in rows]

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

    async def list_deliver_associations(  # pragma: no cover
        self, collection_id: str
    ) -> list[DeliverAssociation]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT cc.id, cc.collection_id, cc.config"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.collection_id = %s AND cc.direction = 'deliver'"
                " AND cc.enabled = true AND c.enabled = true",
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
