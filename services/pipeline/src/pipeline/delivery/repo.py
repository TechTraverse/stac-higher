"""Repository seam over ``stac_higher.delivery_log`` + the destination
association/connection + pgstac items (ROADMAP §5, §6.4).

Mirrors ``ingest/repo.py`` / ``dispatcher/repo.py``: a ``DeliveryRepo`` ABC the
worker + deliver job depend on (unit-tested against ``FakeDeliveryRepo``) plus a
psycopg ``PgDeliveryRepo`` for production. Pg methods open a short-lived
connection and are ``# pragma: no cover`` — exercised by the live verification.

Ownership (ADR 0001): reads ``collection_connections``/``connections`` and pgstac
items; INSERT/UPDATEs only ``delivery_log``. Never runs DDL.
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass
from typing import Any

from pipeline.connections.repo import ConnectionRow, _to_connection_row
from pipeline.ingest.discover import source_fetch_path


@dataclass
class DeliverTarget:
    """An enabled ``direction='deliver'`` association with its destination
    connection loaded (so the worker can ``build_adapter``). ``config`` is the raw
    §5.1 delivery jsonb (parsed by ``delivery.config.parse_delivery_config``)."""

    id: str
    collection_id: str
    config: dict[str, Any]
    connection: ConnectionRow


@dataclass
class DeliveryRow:
    """Prior delivery_log state for one (association, item) — the substrate for
    the on_update gate and the log-based overwrite gate (spec decisions 1-2)."""

    id: str
    status: str
    attempts: int
    delivered_assets: dict[str, Any]


@dataclass
class ReferenceSource:
    """A reference-mode source file for an item: read in place from the ingest
    source connection's adapter (spec decision 3 — the pipeline has no HTTP
    client; ``source_href`` presence flags reference mode)."""

    filename: str
    fetch_path: str
    connection: ConnectionRow


class DeliveryRepo(abc.ABC):
    @abc.abstractmethod
    async def load_target(self, association_id: str) -> DeliverTarget | None:
        """Load one enabled deliver association + its connection, or ``None`` if
        it is gone/disabled (a job that arrives after disable must no-op)."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or ``None`` if not present."""

    @abc.abstractmethod
    async def get_row(self, association_id: str, item_id: str) -> DeliveryRow | None:
        """The existing delivery_log row (status + delivered_assets), or None on
        first delivery. Read BEFORE upsert_pending, which resets status."""

    @abc.abstractmethod
    async def load_reference_sources(self, item_id: str) -> list[ReferenceSource]:
        """Latest-version ingest_files rows for this item with a source_href —
        the item's reference-mode files, with their source connection loaded.
        A reference asset whose source association/connection is disabled is
        not returned, so its delivery fails with a clear canonical-object-missing
        error instead of silently reading a disabled source."""

    @abc.abstractmethod
    async def upsert_pending(
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        """Insert (or reset to pending) the (association, item) delivery_log row;
        return its id. ISO-8601 ``item_created_at`` or ``None``."""

    @abc.abstractmethod
    async def mark_delivering(self, row_id: str) -> None:
        """Flip to delivering and increment attempts."""

    @abc.abstractmethod
    async def mark_delivered(
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        """Flip to delivered; record bytes + delivered_at + the per-asset
        fingerprint map; clear error."""

    @abc.abstractmethod
    async def mark_failed(self, row_id: str, error: str) -> None:
        """Flip to failed; record the error message."""


_TARGET_COLUMNS = "cc.id, cc.collection_id, cc.config"
_CONNECTION_COLUMNS = "c.id, c.name, c.protocol, c.config, c.credentials, c.host_key, c.enabled"


@dataclass
class PgDeliveryRepo(DeliveryRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def load_target(  # pragma: no cover
        self, association_id: str
    ) -> DeliverTarget | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_TARGET_COLUMNS}, {_CONNECTION_COLUMNS}"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.id = %s AND cc.direction = 'deliver'"
                " AND cc.enabled = true AND c.enabled = true",
                (association_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        cc_id, collection_id, config = row[:3]
        return DeliverTarget(
            id=str(cc_id),
            collection_id=collection_id,
            config=dict(config) if config else {},
            connection=_to_connection_row(row[3:]),
        )

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT pgstac.get_item(%s, %s)", (item_id, collection_id)
            )
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None

    async def get_row(  # pragma: no cover
        self, association_id: str, item_id: str
    ) -> DeliveryRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, status, attempts, delivered_assets"
                " FROM stac_higher.delivery_log"
                " WHERE association_id = %s AND item_id = %s",
                (association_id, item_id),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return DeliveryRow(
            id=str(row[0]),
            status=row[1],
            attempts=row[2],
            delivered_assets=dict(row[3]) if row[3] else {},
        )

    async def load_reference_sources(  # pragma: no cover
        self, item_id: str
    ) -> list[ReferenceSource]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (f.association_id, f.source_path)"
                " f.source_path, cc.config,"
                f" {_CONNECTION_COLUMNS}"
                " FROM stac_higher.ingest_files f"
                " JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE f.item_id = %s AND f.source_href IS NOT NULL"
                " AND cc.enabled = true AND c.enabled = true"
                " ORDER BY f.association_id, f.source_path, f.version DESC",
                (item_id,),
            )
            rows = await cur.fetchall()
        sources: list[ReferenceSource] = []
        for row in rows:
            source_path, ingest_config = row[0], dict(row[1]) if row[1] else {}
            sources.append(
                ReferenceSource(
                    filename=source_path.rsplit("/", 1)[-1],
                    fetch_path=source_fetch_path(
                        ingest_config.get("source_path", ""), source_path
                    ),
                    connection=_to_connection_row(row[2:]),
                )
            )
        return sources

    async def upsert_pending(  # pragma: no cover
        self, association_id: str, item_id: str, item_created_at: str | None
    ) -> str:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.delivery_log"
                " (association_id, item_id, item_created_at, status, attempts)"
                " VALUES (%s, %s, %s, 'pending', 0)"
                " ON CONFLICT (association_id, item_id) DO UPDATE"
                " SET status = 'pending',"
                "     attempts = 0,"
                "     item_created_at = EXCLUDED.item_created_at,"
                "     updated_at = now()"
                " RETURNING id",
                (association_id, item_id, item_created_at),
            )
            row = await cur.fetchone()
            await conn.commit()
        return str(row[0])

    async def mark_delivering(self, row_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivering', attempts = attempts + 1, updated_at = now()"
                " WHERE id = %s",
                (row_id,),
            )
            await conn.commit()

    async def mark_delivered(  # pragma: no cover
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivered', bytes = %s, error = NULL,"
                "     delivered_assets = %s::jsonb,"
                "     delivered_at = now(), updated_at = now()"
                " WHERE id = %s",
                (byte_count, json.dumps(delivered_assets or {}), row_id),
            )
            await conn.commit()

    async def mark_failed(self, row_id: str, error: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'failed', error = %s, updated_at = now()"
                " WHERE id = %s",
                (error, row_id),
            )
            await conn.commit()
