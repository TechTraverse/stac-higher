"""Persistence for the daily flow-stats rollup (M5-E).

Split from `daily.py` so the delta arithmetic stays unit-testable without a
database, and `# pragma: no cover` for the SQL like the other repos.
"""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass
from typing import Any

from pipeline.flow.daily import SUBJECT_ASSOCIATION, SUBJECT_PROCESS, DailyRow


@dataclass(frozen=True)
class SubjectStats:
    subject_kind: str
    subject_id: str
    flow_stats: dict[str, Any]


class DailyStatsRepo(abc.ABC):
    @abc.abstractmethod
    async def list_subjects(self) -> list[SubjectStats]:
        """Every live association and process source, with its cumulative
        `flow_stats`."""

    @abc.abstractmethod
    async def cumulative_before(
        self, day: dt.date
    ) -> dict[tuple[str, str], dict[str, int]]:
        """Each subject's running total as of the end of the previous day,
        summed from the rows already written."""

    @abc.abstractmethod
    async def upsert_rows(self, rows: list[DailyRow]) -> int:
        """Write one row per subject per day, idempotently — re-running the
        job for the same day must correct the row, not duplicate it."""

    @abc.abstractmethod
    async def prune(self, before: dt.date) -> int:
        """Drop buckets older than the retention window (a bounded DELETE;
        the row count is subjects x days, so no partitioning is warranted)."""


@dataclass
class PgDailyStatsRepo(DailyStatsRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def list_subjects(self) -> list[SubjectStats]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, flow_stats FROM stac_higher.collection_connections"
                " WHERE deleted_at IS NULL"
            )
            associations = await cur.fetchall()
            cur = await conn.execute(
                "SELECT s.id, s.flow_stats FROM stac_higher.process_sources s"
                "  JOIN stac_higher.processes p ON p.id = s.process_id"
                " WHERE p.deleted_at IS NULL"
            )
            sources = await cur.fetchall()
        return [
            SubjectStats(SUBJECT_ASSOCIATION, str(r[0]), dict(r[1] or {}))
            for r in associations
        ] + [
            SubjectStats(SUBJECT_PROCESS, str(r[0]), dict(r[1] or {})) for r in sources
        ]

    async def cumulative_before(  # pragma: no cover
        self, day: dt.date
    ) -> dict[tuple[str, str], dict[str, int]]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT subject_kind, subject_id,"
                "       sum(files)::int, sum(items)::int, sum(bytes)::bigint,"
                "       sum(delivered)::int, sum(failed)::int, sum(dead)::int,"
                "       sum(runs)::int"
                "  FROM stac_higher.flow_stats_daily"
                " WHERE day < %s"
                " GROUP BY subject_kind, subject_id",
                (day,),
            )
            rows = await cur.fetchall()
        return {
            (r[0], str(r[1])): {
                "files": int(r[2] or 0),
                "items": int(r[3] or 0),
                "bytes": int(r[4] or 0),
                "delivered": int(r[5] or 0),
                "failed": int(r[6] or 0),
                "dead": int(r[7] or 0),
                "runs": int(r[8] or 0),
            }
            for r in rows
        }

    async def upsert_rows(self, rows: list[DailyRow]) -> int:  # pragma: no cover
        if not rows:
            return 0
        async with await self._connect() as conn:
            for row in rows:
                await conn.execute(
                    "INSERT INTO stac_higher.flow_stats_daily"
                    " (subject_kind, subject_id, day, files, items, bytes,"
                    "  delivered, failed, dead, runs)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (subject_kind, subject_id, day)"
                    " DO UPDATE SET files = EXCLUDED.files, items = EXCLUDED.items,"
                    "   bytes = EXCLUDED.bytes, delivered = EXCLUDED.delivered,"
                    "   failed = EXCLUDED.failed, dead = EXCLUDED.dead,"
                    "   runs = EXCLUDED.runs, updated_at = now()",
                    (
                        row.subject_kind,
                        row.subject_id,
                        row.day,
                        row.counters["files"],
                        row.counters["items"],
                        row.counters["bytes"],
                        row.counters["delivered"],
                        row.counters["failed"],
                        row.counters["dead"],
                        row.counters["runs"],
                    ),
                )
            await conn.commit()
        return len(rows)

    async def prune(self, before: dt.date) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "DELETE FROM stac_higher.flow_stats_daily WHERE day < %s", (before,)
            )
            await conn.commit()
        return cur.rowcount or 0


async def rollup_tick(
    repo: DailyStatsRepo,
    *,
    day: dt.date,
    retention_days: int,
) -> tuple[int, int]:
    """Snapshot every subject's day and prune beyond the window."""
    from pipeline.flow.daily import delta_row

    previous = await repo.cumulative_before(day)
    rows = [
        delta_row(
            s.subject_kind,
            s.subject_id,
            day,
            s.flow_stats,
            previous.get((s.subject_kind, s.subject_id)),
        )
        for s in await repo.list_subjects()
    ]
    written = await repo.upsert_rows(rows)
    pruned = await repo.prune(day - dt.timedelta(days=retention_days))
    return written, pruned


__all__ = [
    "DailyStatsRepo",
    "PgDailyStatsRepo",
    "SubjectStats",
    "rollup_tick",
]
