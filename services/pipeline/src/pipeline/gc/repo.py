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
from typing import Any


@dataclass(frozen=True)
class RetentionCollection:
    """One collection the retention sweep must evaluate."""

    collection_id: str
    #: None for archived collections (every item expires regardless of age).
    retention_days: int | None
    gc_grace_days: int
    archived: bool = False
    #: W-2: keep the newest N by item datetime, expire the rest. None for no
    #: cap — and ALWAYS None for archived collections (the archive path
    #: expires everything; a cap would preserve N items the operator asked to
    #: empty). That override lives in ``list_gc_collections``, in one place.
    retention_max_items: int | None = None


@dataclass(frozen=True)
class DueMark:
    """An asset_gc row whose grace has passed."""

    id: str
    object_key: str  # key PREFIX under the platform bucket


class GcRepo(abc.ABC):
    @abc.abstractmethod
    async def list_gc_collections(self) -> list[RetentionCollection]:
        """Collections with work for the sweep: ``retention_days`` or
        ``retention_max_items`` declared, or ``archived`` (ADR 0009: archive =
        delete the data, keep the record). Nothing else is ever touched — a
        platform nobody configured deletes nothing (spec §5.3)."""

    @abc.abstractmethod
    async def list_expired_items(
        self,
        collection_id: str,
        retention_days: int | None,
        limit: int,
        *,
        retention_max_items: int | None = None,
    ) -> list[str]:
        """Item ids this collection's retention rules have expired.

        Two rules, UNIONed (W-2): ``pgstac.items.datetime`` older than
        now - retention_days, and beyond the newest ``retention_max_items``
        by datetime. Both None means ALL item ids — that is how
        ``list_gc_collections`` expresses the archive path. Batched by
        ``limit``; the periodic sweep drains large backlogs across ticks."""

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
                "SELECT collection_id, retention_days, gc_grace_days, archived,"
                "       retention_max_items"
                "  FROM stac_higher.collection_settings"
                " WHERE retention_days IS NOT NULL OR archived = true"
                "    OR retention_max_items IS NOT NULL"
            )
            rows = await cur.fetchall()
        return [
            RetentionCollection(
                collection_id=r[0],
                # Archive expires everything — age and count are irrelevant
                # (ADR 0009). Both overrides here, so the sweep and the expiry
                # query below cannot disagree about what archived means.
                retention_days=None if r[3] else r[1],
                gc_grace_days=int(r[2]),
                archived=bool(r[3]),
                retention_max_items=None if r[3] else r[4],
            )
            for r in rows
        ]

    async def list_expired_items(  # pragma: no cover
        self,
        collection_id: str,
        retention_days: int | None,
        limit: int,
        *,
        retention_max_items: int | None = None,
    ) -> list[str]:
        # The SQL is assembled from the branches that apply rather than
        # parameterised over NULLs, because `OFFSET NULL` is an error, not a
        # no-op.
        async with await self._connect() as conn:
            # pgstac may be absent on a dev/unit DB — treat as no items.
            try:
                if retention_days is None and retention_max_items is None:
                    # archived (or a settings row with no rule at all)
                    cur = await conn.execute(
                        "SELECT id FROM pgstac.items WHERE collection = %s ORDER BY id LIMIT %s",
                        (collection_id, limit),
                    )
                else:
                    branches: list[str] = []
                    params: list[Any] = []
                    if retention_days is not None:
                        branches.append(
                            "SELECT id FROM pgstac.items"
                            " WHERE collection = %s"
                            "   AND datetime < now() - make_interval(days => %s)"
                        )
                        params += [collection_id, retention_days]
                    if retention_max_items is not None:
                        # Everything past the newest N. The id tiebreak makes
                        # OFFSET deterministic when datetimes collide — without
                        # it the sweep could mark a different arbitrary subset
                        # each tick.
                        branches.append(
                            "SELECT id FROM ("
                            "  SELECT id FROM pgstac.items WHERE collection = %s"
                            "   ORDER BY datetime DESC, id DESC OFFSET %s"
                            ") beyond_cap"
                        )
                        params += [collection_id, retention_max_items]
                    sql = " UNION ".join(branches) + " ORDER BY id LIMIT %s"
                    params.append(limit)
                    cur = await conn.execute(sql, tuple(params))
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
                await conn.execute("SELECT pgstac.delete_item(%s, %s)", (item_id, collection_id))
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
                "UPDATE stac_higher.asset_gc SET collected_at = now(), error = NULL WHERE id = %s",
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
