"""Repository seam over the cube sink tables (virtual cube spec §4, §5).

A ``CubeRepo`` ABC the dispatcher and the cube jobs depend on (unit-tested
against ``tests/_cube_fake.py``) plus ``PgCubeRepo`` for production, whose SQL
is exercised by the DB-gated ``test_integration_cubes_repo.py``.

Ownership (ADR 0001): the app owns the DDL (migration 032). The pipeline
reads ``cube_sinks`` and writes ``cube_appends`` rows, plus the
pipeline-owned ``cube_sinks`` columns ``source_prefixes``,
``last_snapshot_id``, ``last_appended_at`` and ``last_error`` (Z-4). It
never writes ``cube_sinks.updated_at``: that column is the app's
optimistic-lock version (#98).
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class CubeSink:
    """One ``cube_sinks`` row, as the append reads it."""

    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool
    #: raw jsonb; ``cubes.config.parse_cube_sink_config`` reads it
    config: dict[str, Any]
    source_prefixes: tuple[str, ...]
    last_snapshot_id: str | None
    #: ``updated_at::text``: the app's optimistic-lock version (#98)
    version: str


@dataclass(frozen=True)
class PendingRow:
    id: int
    item_id: str
    item_datetime: dt.datetime
    #: attempts AFTER this take's bump (the crash-loop count, #90)
    attempts: int


@dataclass(frozen=True)
class RowOutcome:
    """A terminal ledger write: ``appended`` / ``skipped`` / ``failed``."""

    id: int
    status: str
    reason: str | None = None
    snapshot_id: str | None = None


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
    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:
        """The sink row, or ``None`` when it is gone."""

    @abc.abstractmethod
    async def take_pending(self, cube_sink_id: str, limit: int) -> list[PendingRow]:
        """Up to ``limit`` pending rows in ``(item_datetime, id)`` order, each
        with ``attempts`` bumped by one BEFORE the job parses anything, so a
        row that keeps killing its worker is counted (#90 comment)."""

    @abc.abstractmethod
    async def release_rows(self, cube_sink_id: str, row_ids: Sequence[int]) -> int:
        """Undo ``take_pending``'s bump on rows still ``pending``: the job
        failed with an exception (an outage or a bug), which is not a crash
        loop. Returns the rows changed."""

    @abc.abstractmethod
    async def finish_rows(self, cube_sink_id: str, outcomes: Sequence[RowOutcome]) -> int:
        """Apply terminal outcomes to rows still ``pending``; a double run's
        second writer changes nothing. Returns the rows changed."""

    @abc.abstractmethod
    async def has_pending(self, cube_sink_id: str) -> bool:
        """Whether the sink still holds a ``pending`` row."""

    @abc.abstractmethod
    async def record_commit(
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        """Record the repository tip, its container prefixes and the time, and
        clear ``last_error``. With ``first_commit_version`` set, apply only
        while the row still has that app version and no snapshot: either this
        or a concurrent PUT loses, never both (#90). Never writes
        ``updated_at``. Returns whether the row changed."""

    @abc.abstractmethod
    async def record_error(self, cube_sink_id: str, message: str | None) -> None:
        """Set ``last_error``, or clear it with ``None`` (never ``updated_at``)."""


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

    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, source_collection_id, cube_collection_id, enabled,"
                "       config, source_prefixes, last_snapshot_id, updated_at::text"
                "  FROM stac_higher.cube_sinks WHERE id = %s",
                (cube_sink_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return CubeSink(
            id=row[0],
            source_collection_id=row[1],
            cube_collection_id=row[2],
            enabled=bool(row[3]),
            config=dict(row[4] or {}),
            source_prefixes=tuple(row[5] or ()),
            last_snapshot_id=row[6],
            version=row[7],
        )

    async def take_pending(  # pragma: no cover
        self, cube_sink_id: str, limit: int
    ) -> list[PendingRow]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH picked AS ("
                "  SELECT id FROM stac_higher.cube_appends"
                "   WHERE cube_sink_id = %s AND status = 'pending'"
                "   ORDER BY item_datetime, id LIMIT %s FOR UPDATE"
                ")"
                " UPDATE stac_higher.cube_appends a"
                "    SET attempts = a.attempts + 1, updated_at = now()"
                "   FROM picked WHERE a.id = picked.id"
                " RETURNING a.id, a.item_id, a.item_datetime, a.attempts",
                (cube_sink_id, limit),
            )
            rows = await cur.fetchall()
            await conn.commit()
        taken = [PendingRow(id=r[0], item_id=r[1], item_datetime=r[2], attempts=r[3]) for r in rows]
        return sorted(taken, key=lambda r: (r.item_datetime, r.id))

    async def release_rows(  # pragma: no cover
        self, cube_sink_id: str, row_ids: Sequence[int]
    ) -> int:
        if not row_ids:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends"
                "   SET attempts = GREATEST(attempts - 1, 0), updated_at = now()"
                " WHERE cube_sink_id = %s AND id = ANY(%s::bigint[]) AND status = 'pending'",
                (cube_sink_id, list(row_ids)),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed

    async def finish_rows(  # pragma: no cover
        self, cube_sink_id: str, outcomes: Sequence[RowOutcome]
    ) -> int:
        if not outcomes:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends a"
                "   SET status = u.status, reason = u.reason,"
                "       snapshot_id = u.snapshot, updated_at = now()"
                "  FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::text[])"
                "       AS u(id, status, reason, snapshot)"
                " WHERE a.id = u.id AND a.cube_sink_id = %s AND a.status = 'pending'",
                (
                    [o.id for o in outcomes],
                    [o.status for o in outcomes],
                    [o.reason for o in outcomes],
                    [o.snapshot_id for o in outcomes],
                    cube_sink_id,
                ),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed

    async def has_pending(self, cube_sink_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM stac_higher.cube_appends"
                "  WHERE cube_sink_id = %s AND status = 'pending')",
                (cube_sink_id,),
            )
            row = await cur.fetchone()
        return bool(row[0])

    async def record_commit(  # pragma: no cover
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_sinks"
                "   SET last_snapshot_id = %s, last_appended_at = %s,"
                "       source_prefixes = %s::text[], last_error = NULL"
                " WHERE id = %s"
                "   AND (%s::text IS NULL"
                "        OR (updated_at = %s::timestamptz AND last_snapshot_id IS NULL))",
                (
                    snapshot_id,
                    appended_at,
                    list(source_prefixes),
                    cube_sink_id,
                    first_commit_version,
                    first_commit_version,
                ),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed > 0

    async def record_error(  # pragma: no cover
        self, cube_sink_id: str, message: str | None
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.cube_sinks SET last_error = %s WHERE id = %s",
                (message, cube_sink_id),
            )
            await conn.commit()
