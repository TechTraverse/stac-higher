"""Repository seam for retention & GC (M2-F, ADR 0011).

Mirrors the other repos: a :class:`GcRepo` ABC the tick logic depends on
(unit-tested against ``FakeGcRepo``) plus a psycopg ``PgGcRepo`` whose methods
are ``# pragma: no cover`` — exercised at the M2-I rehearsal.

Ownership (ADR 0001): reads ``collection_settings``; deletes catalog items via
``pgstac.delete_item`` (the same call the app's connection-deletion path makes
— item DATA, not DDL); INSERT/UPDATEs rows in ``stac_higher.asset_gc``. The
app owns the DDL and writes its own marks (item/collection delete, archive
intent) into the same table.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionCollection:
    """One collection the retention sweep must evaluate."""

    collection_id: str
    #: None for archived collections (every item expires regardless of age).
    retention_days: int | None
    gc_grace_days: int
    archived: bool = False


@dataclass(frozen=True)
class DueMark:
    """An asset_gc row whose grace has passed."""

    id: str
    object_key: str  # key PREFIX under the platform bucket


class GcRepo(abc.ABC):
    @abc.abstractmethod
    async def list_gc_collections(self) -> list[RetentionCollection]:
        """Collections with work for the sweep: ``retention_days`` declared,
        or ``archived`` (ADR 0009: archive = delete the data, keep the
        record). Nothing else is ever touched — a platform nobody configured
        deletes nothing (spec §5.3)."""

    @abc.abstractmethod
    async def list_expired_items(
        self, collection_id: str, retention_days: int | None, limit: int
    ) -> list[str]:
        """Item ids past the retention window (``pgstac.items.datetime`` older
        than now - retention_days), or ALL item ids when ``retention_days`` is
        None (the archive path). Batched by ``limit``; the periodic sweep
        drains large backlogs across ticks."""

    @abc.abstractmethod
    async def mark_asset_prefix(
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        """Insert an ``asset_gc`` mark with ``collect_after = now() +
        grace_days`` — idempotent via the open-key unique index (re-marking an
        already-open prefix is a no-op). Returns True for a NEW mark."""

    @abc.abstractmethod
    async def delete_item(self, item_id: str, collection_id: str) -> bool:
        """``pgstac.delete_item`` — failure-tolerant (an item already gone, or
        a pgstac-less dev DB, must not kill the sweep). True on success."""

    @abc.abstractmethod
    async def list_due_marks(self, limit: int) -> list[DueMark]:
        """Open marks whose ``collect_after`` has passed, oldest first."""

    @abc.abstractmethod
    async def record_collected(self, mark_id: str) -> None:
        """Stamp ``collected_at`` (and clear any prior error)."""

    @abc.abstractmethod
    async def record_collect_error(self, mark_id: str, error: str) -> None:
        """Keep the mark open with the error — retried next tick."""


@dataclass
class PgGcRepo(GcRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def list_gc_collections(self) -> list[RetentionCollection]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT collection_id, retention_days, gc_grace_days, archived"
                " FROM stac_higher.collection_settings"
                " WHERE retention_days IS NOT NULL OR archived = true"
            )
            rows = await cur.fetchall()
        return [
            RetentionCollection(
                collection_id=r[0],
                # Archive expires everything — age is irrelevant (ADR 0009).
                retention_days=None if r[3] else r[1],
                gc_grace_days=int(r[2]),
                archived=bool(r[3]),
            )
            for r in rows
        ]

    async def list_expired_items(  # pragma: no cover
        self, collection_id: str, retention_days: int | None, limit: int
    ) -> list[str]:
        async with await self._connect() as conn:
            # pgstac may be absent on a dev/unit DB — treat as no items.
            try:
                if retention_days is None:
                    cur = await conn.execute(
                        "SELECT id FROM pgstac.items WHERE collection = %s"
                        " ORDER BY id LIMIT %s",
                        (collection_id, limit),
                    )
                else:
                    cur = await conn.execute(
                        "SELECT id FROM pgstac.items WHERE collection = %s"
                        " AND datetime < now() - make_interval(days => %s)"
                        " ORDER BY datetime LIMIT %s",
                        (collection_id, retention_days, limit),
                    )
                rows = await cur.fetchall()
            except Exception:
                return []
        return [str(r[0]) for r in rows]

    async def mark_asset_prefix(  # pragma: no cover
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.asset_gc"
                " (object_key, collection_id, item_id, reason, collect_after)"
                " VALUES (%s, %s, %s, %s, now() + make_interval(days => %s))"
                " ON CONFLICT (object_key) WHERE collected_at IS NULL DO NOTHING",
                (prefix, collection_id, item_id, reason, grace_days),
            )
            created = (cur.rowcount or 0) > 0
            await conn.commit()
        return created

    async def delete_item(self, item_id: str, collection_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            try:
                await conn.execute(
                    "SELECT pgstac.delete_item(%s, %s)", (item_id, collection_id)
                )
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    async def list_due_marks(self, limit: int) -> list[DueMark]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, object_key FROM stac_higher.asset_gc"
                " WHERE collected_at IS NULL AND collect_after <= now()"
                " ORDER BY collect_after LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [DueMark(id=str(r[0]), object_key=r[1]) for r in rows]

    async def record_collected(self, mark_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.asset_gc"
                " SET collected_at = now(), error = NULL WHERE id = %s",
                (mark_id,),
            )
            await conn.commit()

    async def record_collect_error(self, mark_id: str, error: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.asset_gc SET error = %s WHERE id = %s",
                (error, mark_id),
            )
            await conn.commit()
