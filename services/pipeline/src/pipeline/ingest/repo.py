"""Repository seam over ``stac_higher.collection_connections`` (ingest rows) +
``stac_higher.ingest_files`` (the per-file ledger).

Mirrors :mod:`pipeline.connections.repo`: an :class:`IngestRepo` ABC the stage
logic depends on (so DISCOVER/GROUP/FETCH are unit-testable against an in-memory
fake), plus a :class:`PgIngestRepo` psycopg implementation for production. Each
Pg method opens a short-lived ``psycopg.AsyncConnection`` and is marked
``# pragma: no cover`` — the SQL is exercised by the DB integration suite, not
unit tests.

Ownership (ADR 0001): the pipeline READS ``collection_connections`` and
READS/WRITES ``ingest_files``; it NEVER runs DDL. Association CRUD stays the
app's; the pipeline's only ``collection_connections`` write is the
``flow_stats`` telemetry column (M2-A, ``bump_flow_stats``) — never
``updated_at``, which means "user edit" (``associations/storage.ts``).
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.connections.repo import ConnectionRow, _to_connection_row
from pipeline.flow.stats import apply_ingest_activity

# ledger statuses (mirrors the CHECK constraint, migration 005, widened by 027).
STATUS_SEEN = "seen"
STATUS_SETTLED = "settled"
STATUS_FETCHING = "fetching"
STATUS_STORED = "stored"
STATUS_EXTRACTING = "extracting"
STATUS_ITEMIZED = "itemized"
STATUS_FAILED = "failed"


@dataclass
class IngestAssociation:
    """An enabled ``direction = 'ingest'`` association with its connection loaded.

    ``config`` is the raw §5.1 jsonb (parsed by :func:`ingest.config.parse_ingest_config`
    at the stage boundary). ``connection`` is embedded so a stage can build the
    adapter (``build_adapter``) without a second query — the connection id is
    ``connection.id``, not a separate field.
    """

    id: str
    collection_id: str
    config: dict[str, Any]
    connection: ConnectionRow
    enabled: bool = True


@dataclass
class LedgerEntry:
    """One ``ingest_files`` row (the current or a historical version of a file)."""

    id: str
    association_id: str
    source_path: str
    version: int
    size: int | None
    fingerprint: str | None
    checksum: str | None
    status: str
    item_id: str | None
    source_href: str | None = None
    reason: str | None = None
    extract_run_id: str | None = None
    source_mtime: dt.datetime | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class IngestRepo(abc.ABC):
    """DB access the ingest stages depend on."""

    @abc.abstractmethod
    async def list_enabled_ingest_associations(self) -> list[IngestAssociation]:
        """All enabled ``direction = 'ingest'`` associations, connection embedded.

        Backs the scheduler (decide which are due) and the stages (build the
        adapter). Disabled associations and delivery rows are excluded.
        """

    @abc.abstractmethod
    async def get_association(self, association_id: str) -> IngestAssociation | None:
        """Load one enabled ingest association by id, or ``None`` if it is gone
        or has been disabled (a stage that arrives after the user disables the
        association must no-op)."""

    @abc.abstractmethod
    async def get_latest_ledger(
        self, association_id: str, source_path: str
    ) -> LedgerEntry | None:
        """The highest-``version`` ledger row for ``(association, source_path)``,
        or ``None`` if the file has never been seen."""

    @abc.abstractmethod
    async def list_ledger_by_status(
        self, association_id: str, status: str
    ) -> list[LedgerEntry]:
        """All *latest-version* ledger rows for the association in ``status``.

        GROUP reads ``settled`` rows; other stages read their own inbox. Only the
        current version of each ``source_path`` is returned.
        """

    @abc.abstractmethod
    async def insert_ledger_version(
        self,
        association_id: str,
        source_path: str,
        *,
        version: int,
        status: str,
        size: int | None,
        fingerprint: str | None,
        item_id: str | None = None,
        source_mtime: dt.datetime | None = None,
    ) -> str:
        """Insert a new ledger version row; returns its id."""

    @abc.abstractmethod
    async def set_ledger_fields(self, entry_id: str, **fields: Any) -> None:
        """Update a ledger row's mutable columns (``status``, ``size``,
        ``fingerprint``, ``checksum``, ``item_id``, ``source_href``) and bump
        ``updated_at``. Unknown columns are rejected."""

    @abc.abstractmethod
    async def sweep_stuck_fetching(self, older_than_seconds: int) -> int:
        """Crash recovery (ISSUES I-52): reset ``fetching`` rows whose
        updated_at is older than the threshold back to ``settled`` — a worker
        that died mid-FETCH left them stranded, and FETCH is idempotent
        against canonical storage. Returns the number of rows reset."""

    @abc.abstractmethod
    async def sweep_failed_for_retry(
        self, max_retries: int, older_than_seconds: int
    ) -> int:
        """Bounded retry (ISSUES I-52): reset ``failed`` rows with remaining
        retry budget (``retries < max_retries``) and a cooled-off updated_at
        back to ``settled``, incrementing ``retries``. Rows at the cap stay
        ``failed`` (terminal until the Phase 8 operator backfill). Clears
        ``reason`` so the failure text being retried out of does not outlive
        it. Returns the number of rows reset."""

    @abc.abstractmethod
    async def sweep_stuck_stored(
        self, max_retries: int, older_than_seconds: int
    ) -> tuple[int, int]:
        """Stored-stall recovery (ISSUES I-55): a ``stored`` row older than the
        threshold means ITEMIZE never landed despite its queue-level retries
        (worker crash, persistent upsert failure). Rows with retry budget go
        back to ``settled`` (incrementing ``retries``) so the normal
        GROUP → FETCH → ITEMIZE chain re-drives them (each stage is
        idempotent); rows at the cap go to ``failed`` terminal, visible to the
        Phase 8 operator backfill. Returns ``(resettled, dead_ended)``."""

    @abc.abstractmethod
    async def set_ledger_status_many(
        self,
        entry_ids: Sequence[str],
        *,
        status: str,
        item_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Update ``status`` (and ``item_id``) for ALL given ledger rows in a
        single statement — all-or-nothing. Used by ITEMIZE so a group's members
        are marked together: a crash mid-mark must never leave the group split
        across statuses, which would let a retry rebuild the item from a
        subset of members. ``reason`` is the failure text an operator reads
        (G-6); pass None on success so a stale reason does not outlive the
        failure."""

    @abc.abstractmethod
    async def get_ledger_entries(self, entry_ids: Sequence[str]) -> list[LedgerEntry]:
        """The rows for ``entry_ids`` (any status). The extract finalize
        branch re-reads its batch's rows by id — the same idempotent guard
        ITEMIZE applies by source path."""

    @abc.abstractmethod
    async def set_extract_run(self, entry_ids: Sequence[str], run_id: str) -> None:
        """Stamp the extractor run that owns these ``extracting`` rows, so the
        recovery sweep can tell a live run from a vanished one (G-6)."""

    @abc.abstractmethod
    async def fail_extracting_rows(
        self, entry_ids: Sequence[str], *, run_id: str, reason: str
    ) -> int:
        """Fail only the rows this run STILL OWNS: ``status = 'extracting'``
        AND ``extract_run_id = run_id``. Returns the number failed.

        The retry sweep re-settles the same row ids, so a row can be re-driven
        through a NEW extractor run (or already be ``itemized``) while a stale
        run for the old batch is still alive. An unconditional fail by id would
        clobber those (G-6 final review), so ownership is part of the WHERE.
        """

    @abc.abstractmethod
    async def sweep_stuck_extracting(self, older_than_seconds: int) -> int:
        """Extract-stall recovery (G-6): an ``extracting`` row older than the
        threshold whose run was never stamped, or whose run row is gone or no
        longer open (not queued/running/failed), is failed with a reason. The
        failed-retry sweep then re-drives it like any failed file. Returns the
        number of rows failed."""

    @abc.abstractmethod
    async def bump_flow_stats(
        self,
        association_id: str,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        """Fold one settle/itemize outcome into the association's
        ``flow_stats`` rollup (M2-A, §6.6): cumulative ``files``/``bytes``/
        ``items``/``failed`` counters plus ``last_activity_at`` /
        ``last_error_at`` / ``last_latency_seconds`` stamps — the substrate the
        M2-B flow monitor evaluates ``expect_activity_within_seconds``
        against. Never touches ``updated_at``."""


# --------------------------------------------------------------------------- #
# psycopg implementation
# --------------------------------------------------------------------------- #

_ASSOC_COLUMNS = "cc.id, cc.collection_id, cc.config, cc.enabled"
_CONNECTION_COLUMNS = "c.id, c.name, c.protocol, c.config, c.credentials, c.host_key, c.enabled"
_LEDGER_COLUMNS = (
    "id, association_id, source_path, version, size, fingerprint, checksum,"
    " status, item_id, source_href, reason, extract_run_id, source_mtime,"
    " created_at, updated_at"
)
#: mutable ledger columns settable through set_ledger_fields (guards SQL building).
_LEDGER_MUTABLE = frozenset(
    {"status", "size", "fingerprint", "checksum", "item_id", "source_href", "reason"}
)


def _to_ledger_entry(record: Sequence[Any]) -> LedgerEntry:
    (
        lid,
        association_id,
        source_path,
        version,
        size,
        fingerprint,
        checksum,
        status,
        item_id,
        source_href,
        reason,
        extract_run_id,
        source_mtime,
        created_at,
        updated_at,
    ) = record
    return LedgerEntry(
        id=str(lid),
        association_id=str(association_id),
        source_path=source_path,
        version=int(version),
        size=int(size) if size is not None else None,
        fingerprint=fingerprint,
        checksum=checksum,
        status=status,
        item_id=item_id,
        source_href=source_href,
        reason=reason,
        extract_run_id=str(extract_run_id) if extract_run_id else None,
        source_mtime=source_mtime,
        created_at=created_at,
        updated_at=updated_at,
    )


@dataclass
class PgIngestRepo(IngestRepo):
    """psycopg-backed repo. Opens a short-lived connection per operation."""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_enabled_ingest_associations(self) -> list[IngestAssociation]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_ASSOC_COLUMNS}, {_CONNECTION_COLUMNS}"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.direction = 'ingest' AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL"
                " ORDER BY cc.created_at"
            )
            rows = await cur.fetchall()
        return [self._row_to_association(r) for r in rows]

    async def get_association(  # pragma: no cover
        self, association_id: str
    ) -> IngestAssociation | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_ASSOC_COLUMNS}, {_CONNECTION_COLUMNS}"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.id = %s AND cc.direction = 'ingest'"
                " AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL",
                (association_id,),
            )
            row = await cur.fetchone()
        return self._row_to_association(row) if row else None

    @staticmethod
    def _row_to_association(record: Sequence[Any]) -> IngestAssociation:  # pragma: no cover
        assoc, connection = record[:4], record[4:]
        cc_id, collection_id, config, enabled = assoc
        return IngestAssociation(
            id=str(cc_id),
            collection_id=collection_id,
            config=dict(config) if config else {},
            connection=_to_connection_row(connection),
            enabled=bool(enabled),
        )

    async def get_latest_ledger(  # pragma: no cover
        self, association_id: str, source_path: str
    ) -> LedgerEntry | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_LEDGER_COLUMNS} FROM stac_higher.ingest_files"
                " WHERE association_id = %s AND source_path = %s"
                " ORDER BY version DESC LIMIT 1",
                (association_id, source_path),
            )
            row = await cur.fetchone()
        return _to_ledger_entry(row) if row else None

    async def list_ledger_by_status(  # pragma: no cover
        self, association_id: str, status: str
    ) -> list[LedgerEntry]:
        # DISTINCT ON keeps only the current (highest) version per source_path so
        # a superseded historical row can never re-enter a stage.
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_LEDGER_COLUMNS} FROM ("
                f"  SELECT DISTINCT ON (source_path) {_LEDGER_COLUMNS}"
                "   FROM stac_higher.ingest_files WHERE association_id = %s"
                "   ORDER BY source_path, version DESC"
                ") latest WHERE status = %s ORDER BY created_at",
                (association_id, status),
            )
            rows = await cur.fetchall()
        return [_to_ledger_entry(r) for r in rows]

    async def insert_ledger_version(  # pragma: no cover
        self,
        association_id: str,
        source_path: str,
        *,
        version: int,
        status: str,
        size: int | None,
        fingerprint: str | None,
        item_id: str | None = None,
        source_mtime: dt.datetime | None = None,
    ) -> str:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.ingest_files"
                " (association_id, source_path, version, status, size, fingerprint,"
                "  item_id, source_mtime)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    association_id,
                    source_path,
                    version,
                    status,
                    size,
                    fingerprint,
                    item_id,
                    source_mtime,
                ),
            )
            row = await cur.fetchone()
            await conn.commit()
        return str(row[0])

    async def set_ledger_fields(self, entry_id: str, **fields: Any) -> None:  # pragma: no cover
        unknown = set(fields) - _LEDGER_MUTABLE
        if unknown:
            raise ValueError(f"non-mutable ledger columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = %s" for col in fields)
        values = list(fields.values())
        async with await self._connect() as conn:
            await conn.execute(
                f"UPDATE stac_higher.ingest_files SET {assignments}, updated_at = now()"
                " WHERE id = %s",
                (*values, entry_id),
            )
            await conn.commit()

    async def sweep_stuck_fetching(self, older_than_seconds: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files"
                " SET status = 'settled', updated_at = now()"
                " WHERE status = 'fetching'"
                " AND updated_at < now() - make_interval(secs => %s)",
                (older_than_seconds,),
            )
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def sweep_failed_for_retry(  # pragma: no cover
        self, max_retries: int, older_than_seconds: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files"
                " SET status = 'settled', retries = retries + 1, reason = NULL,"
                "     updated_at = now()"
                " WHERE status = 'failed' AND retries < %s"
                " AND updated_at < now() - make_interval(secs => %s)",
                (max_retries, older_than_seconds),
            )
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def sweep_stuck_stored(  # pragma: no cover
        self, max_retries: int, older_than_seconds: int
    ) -> tuple[int, int]:
        # One statement for both transitions (single scan of the ledger; the
        # sweep runs every minute): rows with budget re-settle, capped rows
        # dead-end to 'failed'.
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files"
                " SET status = CASE WHEN retries < %s THEN 'settled' ELSE 'failed' END,"
                "     retries = retries + (retries < %s)::int,"
                "     updated_at = now()"
                " WHERE status = 'stored'"
                " AND updated_at < now() - make_interval(secs => %s)"
                " RETURNING status",
                (max_retries, max_retries, older_than_seconds),
            )
            statuses = [row[0] for row in await cur.fetchall()]
            await conn.commit()
        return statuses.count("settled"), statuses.count("failed")

    async def set_ledger_status_many(  # pragma: no cover
        self,
        entry_ids: Sequence[str],
        *,
        status: str,
        item_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        if not entry_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.ingest_files"
                " SET status = %s, item_id = %s, reason = %s, updated_at = now()"
                " WHERE id = ANY(%s)",
                (status, item_id, reason, list(entry_ids)),
            )
            await conn.commit()

    async def get_ledger_entries(  # pragma: no cover
        self, entry_ids: Sequence[str]
    ) -> list[LedgerEntry]:
        ids = list(entry_ids)
        if not ids:
            return []
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_LEDGER_COLUMNS} FROM stac_higher.ingest_files"
                " WHERE id = ANY(%s::uuid[]) ORDER BY created_at",
                (ids,),
            )
            rows = await cur.fetchall()
        return [_to_ledger_entry(r) for r in rows]

    async def set_extract_run(  # pragma: no cover
        self, entry_ids: Sequence[str], run_id: str
    ) -> None:
        if not entry_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.ingest_files SET extract_run_id = %s"
                " WHERE id = ANY(%s::uuid[])",
                (run_id, list(entry_ids)),
            )
            await conn.commit()

    async def fail_extracting_rows(  # pragma: no cover
        self, entry_ids: Sequence[str], *, run_id: str, reason: str
    ) -> int:
        ids = list(entry_ids)
        if not ids:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files"
                " SET status = 'failed', reason = %s, item_id = NULL, updated_at = now()"
                " WHERE id = ANY(%s::uuid[])"
                "   AND status = 'extracting'"
                "   AND extract_run_id = %s",
                (reason, ids, run_id),
            )
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def sweep_stuck_extracting(  # pragma: no cover
        self, older_than_seconds: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files f"
                "   SET status = 'failed',"
                "       reason = CASE WHEN f.extract_run_id IS NULL"
                "                     THEN 'extractor run was never queued'"
                "                     ELSE 'extractor run ' || f.extract_run_id"
                "                          || ' is gone or closed'"
                "                END,"
                "       updated_at = now()"
                " WHERE f.status = 'extracting'"
                "   AND f.updated_at < now() - make_interval(secs => %s)"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM stac_higher.process_runs r"
                "      WHERE r.id = f.extract_run_id"
                "        AND r.status IN ('queued', 'running', 'failed'))",
                (older_than_seconds,),
            )
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def bump_flow_stats(  # pragma: no cover
        self,
        association_id: str,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            # FOR UPDATE serializes concurrent rollup writes; the math is the
            # same pure function the fakes apply. flow_stats only — updated_at
            # means "user edit" and stays untouched.
            cur = await conn.execute(
                "SELECT flow_stats FROM stac_higher.collection_connections"
                " WHERE id = %s FOR UPDATE",
                (association_id,),
            )
            row = await cur.fetchone()
            if not row:
                return
            stats = apply_ingest_activity(
                dict(row[0]) if row[0] else {},
                files=files,
                bytes_added=bytes_added,
                items=items,
                failed=failed,
                latency_seconds=latency_seconds,
            )
            await conn.execute(
                "UPDATE stac_higher.collection_connections SET flow_stats = %s"
                " WHERE id = %s",
                (Json(stats), association_id),
            )
            await conn.commit()
