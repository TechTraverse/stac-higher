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
import datetime as dt
from dataclasses import dataclass, field
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


@dataclass
class PreRecord:
    """One pre-recorded ``delivery_log`` row (M2-0). ``created`` is True when
    this job inserted the placeholder — only those may be discarded when the
    association turns out to be gone; a pre-existing row is history.
    ``attempts``/``delivered_assets`` carry the row's prior state so a failure
    BEFORE ``deliver_item`` can schedule the retry (and dead-letter at the cap)
    on the same terms, without wiping the fingerprint map."""

    id: str
    item_id: str
    created: bool
    attempts: int = 0
    delivered_assets: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryRow:
    """A failed delivery_log row due for retry (B-iii sweep)."""

    id: str
    association_id: str
    item_id: str
    attempts: int
    item_created_at: str | None = None


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
        return its id. ISO-8601 ``item_created_at`` or ``None``. ``attempts``
        resets only from terminal states (delivered/failed/dead — a NEW event
        starts a fresh cycle, I-44); a sweep-requeued ``pending`` row keeps its
        count so ``max_attempts`` dead-lettering can converge."""

    @abc.abstractmethod
    async def mark_delivering(self, row_id: str) -> int:
        """Flip to delivering and increment attempts; returns the incremented
        attempts count (the retry/dead-letter decision keys off it)."""

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
    async def mark_failed(
        self,
        row_id: str,
        error: str,
        *,
        delivered_assets: dict[str, Any] | None = None,
        next_attempt_at: dt.datetime | None = None,
        dead: bool = False,
    ) -> None:
        """Flip to failed — or ``dead`` when the retry budget is exhausted.
        Persists the PARTIAL delivered_assets map (ISSUES I-49: a retry must
        not rewrite already-delivered assets) and, for failed rows, when the
        retry sweep should pick the row up (``next_attempt_at``)."""

    @abc.abstractmethod
    async def list_due_retries(self, limit: int) -> list[RetryRow]:
        """Failed rows whose ``next_attempt_at`` has passed, oldest first."""

    @abc.abstractmethod
    async def pre_record(
        self, association_id: str, items: list[tuple[str, str | None]]
    ) -> list[PreRecord]:
        """Ensure a ``delivery_log`` row exists for each ``(item_id,
        item_created_at)`` BEFORE the deliver job does anything that can fail
        (M2-0). INSERT-only: an existing row is returned untouched, because
        ``deliver_item``'s ``on_update``/overwrite gates read its prior state.
        A transient fault before ``deliver_item`` records anything would
        otherwise lose the delivery outright — the outbox row is already
        claimed and the retry sweep would have nothing to re-drive."""

    @abc.abstractmethod
    async def discard_pre_records(self, row_ids: list[str]) -> None:
        """Delete placeholder rows this job created after discovering the
        association is disabled/deleted — a legitimate no-op must not leave
        phantom ``pending`` rows for the stall sweep to churn on."""

    @abc.abstractmethod
    async def sweep_stalled_deliveries(
        self, older_than_seconds: int, limit: int
    ) -> int:
        """Crash recovery, mirroring the ingest stored-stall sweep (I-52/I-55):
        rows stranded in ``pending``/``delivering`` past the stall window
        (the job died between pre-record and delivery, or a worker crashed
        mid-transfer) become ``failed`` and due, so the retry sweep re-drives
        them. ``attempts`` is preserved, so ``max_attempts`` dead-lettering
        still converges. Returns the number of rows recovered."""

    @abc.abstractmethod
    async def requeue_for_retry(self, row_ids: list[str]) -> None:
        """Flip due failed rows back to ``pending`` and clear
        ``next_attempt_at`` so the next sweep tick cannot re-enqueue them
        while the retry job is still queued."""


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
                " AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL",
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
                " AND f.reference_removed_at IS NULL"
                " AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL"
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
                # A new event on a settled row is a fresh cycle (I-44); a
                # retry-requeued 'pending'/'delivering' row keeps its count so
                # max_attempts can dead-letter.
                "     attempts = CASE"
                "       WHEN stac_higher.delivery_log.status IN ('pending', 'delivering')"
                "       THEN stac_higher.delivery_log.attempts ELSE 0 END,"
                "     item_created_at = EXCLUDED.item_created_at,"
                "     next_attempt_at = NULL,"
                "     updated_at = now()"
                " RETURNING id",
                (association_id, item_id, item_created_at),
            )
            row = await cur.fetchone()
            await conn.commit()
        return str(row[0])

    async def mark_delivering(self, row_id: str) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivering', attempts = attempts + 1, updated_at = now()"
                " WHERE id = %s"
                " RETURNING attempts",
                (row_id,),
            )
            row = await cur.fetchone()
            await conn.commit()
        return int(row[0]) if row else 1

    async def mark_delivered(  # pragma: no cover
        self,
        row_id: str,
        byte_count: int,
        delivered_assets: dict[str, Any] | None = None,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'delivered', bytes = %s, error = NULL,"
                "     delivered_assets = %s,"
                "     delivered_at = now(), updated_at = now()"
                " WHERE id = %s",
                (byte_count, Json(delivered_assets or {}), row_id),
            )
            await conn.commit()

    async def mark_failed(  # pragma: no cover
        self,
        row_id: str,
        error: str,
        *,
        delivered_assets: dict[str, Any] | None = None,
        next_attempt_at: dt.datetime | None = None,
        dead: bool = False,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = %s, error = %s, delivered_assets = %s,"
                "     next_attempt_at = %s, updated_at = now()"
                " WHERE id = %s",
                (
                    "dead" if dead else "failed",
                    error,
                    Json(delivered_assets or {}),
                    next_attempt_at,
                    row_id,
                ),
            )
            await conn.commit()

    async def list_due_retries(self, limit: int) -> list[RetryRow]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, association_id, item_id, attempts, item_created_at"
                " FROM stac_higher.delivery_log"
                " WHERE status = 'failed' AND next_attempt_at IS NOT NULL"
                " AND next_attempt_at <= now()"
                " ORDER BY next_attempt_at LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [
            RetryRow(
                id=str(r[0]),
                association_id=str(r[1]),
                item_id=r[2],
                attempts=int(r[3]),
                item_created_at=r[4].isoformat() if r[4] else None,
            )
            for r in rows
        ]

    async def pre_record(  # pragma: no cover
        self, association_id: str, items: list[tuple[str, str | None]]
    ) -> list[PreRecord]:
        if not items:
            return []
        item_ids = [i for i, _ in items]
        async with await self._connect() as conn:
            # INSERT-only (DO NOTHING): an existing row keeps its status,
            # attempts and delivered_assets, so deliver_item's on_update and
            # overwrite gates still read the true prior state.
            cur = await conn.execute(
                "INSERT INTO stac_higher.delivery_log"
                " (association_id, item_id, item_created_at, status, attempts)"
                " SELECT %s, i.item_id, i.item_created_at, 'pending', 0"
                " FROM UNNEST(%s::text[], %s::timestamptz[])"
                "   AS i(item_id, item_created_at)"
                " ON CONFLICT (association_id, item_id) DO NOTHING"
                " RETURNING id",
                (association_id, item_ids, [c for _, c in items]),
            )
            created_ids = {str(r[0]) for r in await cur.fetchall()}
            cur = await conn.execute(
                "SELECT id, item_id, attempts, delivered_assets"
                " FROM stac_higher.delivery_log"
                " WHERE association_id = %s AND item_id = ANY(%s)",
                (association_id, item_ids),
            )
            rows = await cur.fetchall()
            await conn.commit()
        return [
            PreRecord(
                id=str(r[0]),
                item_id=r[1],
                created=str(r[0]) in created_ids,
                attempts=int(r[2]),
                delivered_assets=dict(r[3]) if r[3] else {},
            )
            for r in rows
        ]

    async def discard_pre_records(self, row_ids: list[str]) -> None:  # pragma: no cover
        if not row_ids:
            return
        async with await self._connect() as conn:
            # Guarded: only untouched placeholders. A concurrent job that has
            # since started delivering this row must not lose its record.
            await conn.execute(
                "DELETE FROM stac_higher.delivery_log"
                " WHERE id = ANY(%s) AND status = 'pending' AND attempts = 0",
                (row_ids,),
            )
            await conn.commit()

    async def sweep_stalled_deliveries(  # pragma: no cover
        self, older_than_seconds: int, limit: int
    ) -> int:
        async with await self._connect() as conn:
            # next_attempt_at = now() makes them due immediately, so the retry
            # sweep in this same tick re-enqueues them. attempts is preserved:
            # upsert_pending keeps the count for pending/delivering rows, so
            # max_attempts dead-lettering still converges.
            cur = await conn.execute(
                "UPDATE stac_higher.delivery_log SET"
                "   status = 'failed',"
                "   error = 'stalled in ' || status"
                "     || '; recovered by the delivery stall sweep',"
                "   next_attempt_at = now(), updated_at = now()"
                " WHERE id IN ("
                "   SELECT id FROM stac_higher.delivery_log"
                "   WHERE status IN ('pending', 'delivering')"
                "     AND updated_at < now() - make_interval(secs => %s)"
                "   ORDER BY updated_at LIMIT %s)",
                (older_than_seconds, limit),
            )
            await conn.commit()
        return cur.rowcount or 0

    async def requeue_for_retry(self, row_ids: list[str]) -> None:  # pragma: no cover
        if not row_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.delivery_log"
                " SET status = 'pending', next_attempt_at = NULL, updated_at = now()"
                " WHERE id = ANY(%s)",
                (row_ids,),
            )
            await conn.commit()
