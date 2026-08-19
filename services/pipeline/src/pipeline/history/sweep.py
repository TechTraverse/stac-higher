"""Retention sweeps for the three UNIQUE-keyed history tables (M2-G, ADR 0012).

``delivery_log`` and ``ingest_files`` carry natural UNIQUE keys the upsert
model depends on, and ``connection_checks`` is a short-lived request bridge —
none of the three can be time-partitioned (spec §6), so they age out by sweep
instead. The rules are deliberately conservative:

- ``connection_checks``: rows older than the window are deleted outright
  (ephemeral test requests). Separately, a check stranded ``running`` whose
  connection was soft-deleted is flipped to ``failed`` regardless of age —
  the drain skips deleted connections, so nothing else will ever finish it
  (the fold-in noted in TODO's M1 carry-forwards).
- ``ingest_files``: only rows of SOFT-DELETED associations past the window.
  A live association keeps its whole ledger — it is the DISCOVER dedup
  source; pruning it would re-ingest files that still exist at the source.
- ``delivery_log``: rows of soft-deleted associations past the window, plus
  terminal rows (``delivered``/``dead``) past the window whose item no longer
  exists in pgstac (GC'd or manually deleted — the row is provenance for
  nothing). Non-terminal rows are live state and are never swept here.

Everything is a repo seam so the rules unit-test against a fake; the Pg
methods are ``# pragma: no cover`` per repo convention.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class HistorySweepResult:
    checks_deleted: int
    checks_failed_stranded: int
    ingest_files_deleted: int
    delivery_log_deleted: int


class HistoryRepo(abc.ABC):
    @abc.abstractmethod
    async def delete_old_connection_checks(self, days: int) -> int: ...

    @abc.abstractmethod
    async def fail_stranded_checks(self) -> int:
        """``running`` checks whose connection is soft-deleted → ``failed``."""

    @abc.abstractmethod
    async def delete_dead_association_ingest_files(self, days: int) -> int: ...

    @abc.abstractmethod
    async def delete_dead_association_delivery_log(self, days: int) -> int: ...

    @abc.abstractmethod
    async def delete_itemless_terminal_deliveries(self, days: int) -> int:
        """Terminal rows past the window whose item is gone from pgstac.
        Returns 0 when pgstac is absent (unit/CI DBs)."""


async def history_tick(
    repo: HistoryRepo,
    *,
    checks_days: int,
    history_days: int,
) -> HistorySweepResult:
    stranded = await repo.fail_stranded_checks()
    checks = await repo.delete_old_connection_checks(checks_days)
    ingest = await repo.delete_dead_association_ingest_files(history_days)
    deliveries = await repo.delete_dead_association_delivery_log(history_days)
    deliveries += await repo.delete_itemless_terminal_deliveries(history_days)
    return HistorySweepResult(
        checks_deleted=checks,
        checks_failed_stranded=stranded,
        ingest_files_deleted=ingest,
        delivery_log_deleted=deliveries,
    )


@dataclass
class PgHistoryRepo(HistoryRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def _execute(self, sql: str, params: tuple) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(sql, params)
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def delete_old_connection_checks(self, days: int) -> int:  # pragma: no cover
        return await self._execute(
            "DELETE FROM stac_higher.connection_checks"
            " WHERE requested_at < now() - make_interval(days => %s)",
            (days,),
        )

    async def fail_stranded_checks(self) -> int:  # pragma: no cover
        return await self._execute(
            "UPDATE stac_higher.connection_checks ck"
            " SET status = 'failed', finished_at = now(),"
            "     result = jsonb_build_object('ok', false,"
            "       'message', 'connection deleted while check was running')"
            " FROM stac_higher.connections c"
            " WHERE c.id = ck.connection_id AND c.deleted_at IS NOT NULL"
            " AND ck.status IN ('pending','running')",
            (),
        )

    async def delete_dead_association_ingest_files(  # pragma: no cover
        self, days: int
    ) -> int:
        return await self._execute(
            "DELETE FROM stac_higher.ingest_files f"
            " USING stac_higher.collection_connections cc"
            " WHERE cc.id = f.association_id"
            " AND cc.deleted_at IS NOT NULL"
            " AND cc.deleted_at < now() - make_interval(days => %s)",
            (days,),
        )

    async def delete_dead_association_delivery_log(  # pragma: no cover
        self, days: int
    ) -> int:
        return await self._execute(
            "DELETE FROM stac_higher.delivery_log d"
            " USING stac_higher.collection_connections cc"
            " WHERE cc.id = d.association_id"
            " AND cc.deleted_at IS NOT NULL"
            " AND cc.deleted_at < now() - make_interval(days => %s)",
            (days,),
        )

    async def delete_itemless_terminal_deliveries(  # pragma: no cover
        self, days: int
    ) -> int:
        async with await self._connect() as conn:
            try:
                cur = await conn.execute(
                    "DELETE FROM stac_higher.delivery_log d"
                    " USING stac_higher.collection_connections cc"
                    " WHERE cc.id = d.association_id"
                    " AND d.status IN ('delivered','dead')"
                    " AND COALESCE(d.delivered_at, d.updated_at)"
                    "     < now() - make_interval(days => %s)"
                    " AND NOT EXISTS ("
                    "   SELECT 1 FROM pgstac.items i"
                    "   WHERE i.id = d.item_id AND i.collection = cc.collection_id)",
                    (days,),
                )
                count = cur.rowcount or 0
                await conn.commit()
            except Exception:
                await conn.rollback()
                return 0  # pgstac absent — skip this prong
        return count
