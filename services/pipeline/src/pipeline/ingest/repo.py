"""Repository seam over ``stac_higher.collection_connections`` (ingest rows) +
``stac_higher.ingest_files`` (the per-file ledger).

Mirrors :mod:`pipeline.connections.repo`: an :class:`IngestRepo` ABC the stage
logic depends on (so DISCOVER/GROUP/FETCH are unit-testable against an in-memory
fake), plus a :class:`PgIngestRepo` psycopg implementation for production. Each
Pg method opens a short-lived ``psycopg.AsyncConnection`` and is marked
``# pragma: no cover`` — the SQL is exercised by the DB integration suite, not
unit tests.

Ownership (ADR 0001): the pipeline READS ``collection_connections`` and
READS/WRITES ``ingest_files``; it NEVER runs DDL and never writes
``collection_connections`` (that is the app's association CRUD). ``flow_stats``
telemetry writes are deferred to the observability slice.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.connections.repo import ConnectionRow, _to_connection_row

# ledger statuses (mirrors the migration-005 CHECK constraint).
STATUS_SEEN = "seen"
STATUS_SETTLED = "settled"
STATUS_FETCHING = "fetching"
STATUS_STORED = "stored"
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
    ) -> str:
        """Insert a new ledger version row; returns its id."""

    @abc.abstractmethod
    async def set_ledger_fields(self, entry_id: str, **fields: Any) -> None:
        """Update a ledger row's mutable columns (``status``, ``size``,
        ``fingerprint``, ``checksum``, ``item_id``) and bump ``updated_at``.
        Unknown columns are rejected."""

    @abc.abstractmethod
    async def set_ledger_status_many(
        self, entry_ids: Sequence[str], *, status: str, item_id: str | None = None
    ) -> None:
        """Update ``status`` (and ``item_id``) for ALL given ledger rows in a
        single statement — all-or-nothing. Used by ITEMIZE so a group's members
        are marked together: a crash mid-mark must never leave the group split
        across statuses, which would let a retry rebuild the item from a
        subset of members."""


# --------------------------------------------------------------------------- #
# psycopg implementation
# --------------------------------------------------------------------------- #

_ASSOC_COLUMNS = "cc.id, cc.collection_id, cc.config, cc.enabled"
_CONNECTION_COLUMNS = "c.id, c.name, c.protocol, c.config, c.credentials, c.host_key, c.enabled"
_LEDGER_COLUMNS = (
    "id, association_id, source_path, version, size, fingerprint, checksum,"
    " status, item_id, created_at, updated_at"
)
#: mutable ledger columns settable through set_ledger_fields (guards SQL building).
_LEDGER_MUTABLE = frozenset({"status", "size", "fingerprint", "checksum", "item_id"})


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
        created_at=created_at,
        updated_at=updated_at,
    )


@dataclass
class PgIngestRepo(IngestRepo):
    """psycopg-backed repo. Opens a short-lived connection per operation."""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def list_enabled_ingest_associations(self) -> list[IngestAssociation]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_ASSOC_COLUMNS}, {_CONNECTION_COLUMNS}"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.direction = 'ingest' AND cc.enabled = true AND c.enabled = true"
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
                " AND cc.enabled = true AND c.enabled = true",
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
    ) -> str:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.ingest_files"
                " (association_id, source_path, version, status, size, fingerprint, item_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (association_id, source_path, version, status, size, fingerprint, item_id),
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

    async def set_ledger_status_many(  # pragma: no cover
        self, entry_ids: Sequence[str], *, status: str, item_id: str | None = None
    ) -> None:
        if not entry_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.ingest_files SET status = %s, item_id = %s, updated_at = now()"
                " WHERE id = ANY(%s)",
                (status, item_id, list(entry_ids)),
            )
            await conn.commit()
