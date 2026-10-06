"""Repository seam over the cube sink tables (virtual cube spec §4, §5).

A ``CubeRepo`` ABC the dispatcher and the cube jobs depend on (unit-tested
against ``tests/_cube_fake.py``) plus ``PgCubeRepo`` for production, whose SQL
is exercised by the DB-gated ``test_integration_cubes_repo.py``.

Ownership (ADR 0001): the app owns the DDL (migration 032). The pipeline
reads ``cube_sinks`` and writes ``cube_appends`` rows. It never writes
``cube_sinks.updated_at``: that column is the app's optimistic-lock version
(#98), and Z-3 writes nothing to ``cube_sinks`` at all.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

#: The dispatcher's skip reason for an item with neither ``datetime`` nor
#: ``start_datetime`` (spec §5.2; in ``cube-append-status.json``).
REASON_NO_DATETIME = "no_datetime"


@dataclass(frozen=True)
class CubeSinkRef:
    id: str
    cube_collection_id: str


@dataclass(frozen=True)
class LedgerEntry:
    """One ``cube_appends`` row to insert (``ON CONFLICT DO NOTHING``)."""

    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str = "pending"
    reason: str | None = None


class CubeRepo(abc.ABC):
    @abc.abstractmethod
    async def enabled_sinks_for_source(self, collection_id: str) -> list[CubeSinkRef]:
        """Enabled sinks whose source collection is ``collection_id``."""

    @abc.abstractmethod
    async def record_appends(self, entries: Sequence[LedgerEntry]) -> int:
        """Insert ledger rows, skipping any (sink, item) already present and
        any sink that is gone or disabled. Returns the rows inserted."""

    @abc.abstractmethod
    async def sinks_with_stale_pending(self, older_than_seconds: int) -> list[str]:
        """Ids of enabled sinks holding a ``pending`` row created more than
        ``older_than_seconds`` ago (the §5.3 backstop), sorted."""

    @abc.abstractmethod
    async def fail_pending(self, cube_sink_id: str, reason: str) -> int:
        """Mark every ``pending`` row of the sink ``failed`` with ``reason``
        (the Z-3 stub). Returns the rows changed."""


@dataclass
class PgCubeRepo(CubeRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def enabled_sinks_for_source(  # pragma: no cover
        self, collection_id: str
    ) -> list[CubeSinkRef]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, cube_collection_id FROM stac_higher.cube_sinks"
                " WHERE source_collection_id = %s AND enabled"
                " ORDER BY created_at, id",
                (collection_id,),
            )
            rows = await cur.fetchall()
        return [CubeSinkRef(id=r[0], cube_collection_id=r[1]) for r in rows]

    async def record_appends(  # pragma: no cover
        self, entries: Sequence[LedgerEntry]
    ) -> int:
        if not entries:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.cube_appends"
                " (cube_sink_id, item_id, item_datetime, status, reason)"
                " SELECT u.sink, u.item, u.at, u.status, u.reason"
                "   FROM unnest(%s::uuid[], %s::text[], %s::timestamptz[],"
                "               %s::text[], %s::text[])"
                "        AS u(sink, item, at, status, reason)"
                # A sink deleted or disabled since the lookup is skipped, not
                # an FK error that would leave the whole claim unprocessed.
                "   JOIN stac_higher.cube_sinks s ON s.id = u.sink AND s.enabled"
                " ON CONFLICT (cube_sink_id, item_id) DO NOTHING",
                (
                    [e.cube_sink_id for e in entries],
                    [e.item_id for e in entries],
                    [e.item_datetime for e in entries],
                    [e.status for e in entries],
                    [e.reason for e in entries],
                ),
            )
            inserted = cur.rowcount
            await conn.commit()
        return inserted

    async def sinks_with_stale_pending(  # pragma: no cover
        self, older_than_seconds: int
    ) -> list[str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT a.cube_sink_id::text"
                "  FROM stac_higher.cube_appends a"
                "  JOIN stac_higher.cube_sinks s ON s.id = a.cube_sink_id"
                " WHERE s.enabled AND a.status = 'pending'"
                "   AND a.created_at < now() - make_interval(secs => %s)"
                " ORDER BY 1",
                (older_than_seconds,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def fail_pending(  # pragma: no cover
        self, cube_sink_id: str, reason: str
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends"
                " SET status = 'failed', reason = %s,"
                "     attempts = attempts + 1, updated_at = now()"
                " WHERE cube_sink_id = %s AND status = 'pending'",
                (reason, cube_sink_id),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed
