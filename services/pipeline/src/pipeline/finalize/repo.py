"""Repository seam for finalize (Phase 7 §6, §4.1-4.2).

Mirrors the other repos: a :class:`FinalizeRepo` ABC the resolver / recorder /
sweep depend on (unit-tested against ``FakeFinalizeRepo``) plus a psycopg
``PgFinalizeRepo`` whose methods are ``# pragma: no cover`` — exercised at the
P7-Z live gate.

Ownership (ADR 0001 / spec §11): the app owns the ``staged_uploads`` DDL and
INSERTs ``pending`` rows; the pipeline writes ONLY the status columns
(``status``/``result``/``error``/``item_id``/``claimed_at``/``finalized_at``)
— the ``ingest_files`` split, verbatim. Catalog access is item DATA only
(``pgstac.get_item`` / ``pgstac.delete_item``); ``asset_gc`` marks use the
same idempotent open-key upsert as :class:`pipeline.gc.repo.PgGcRepo`.
"""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass
from typing import Any

from pipeline.finalize.seam import PreflightChecks
from pipeline.finalize.status import (
    STATUS_EXPIRED,
    STATUS_FINALIZED,
    STATUS_FINALIZING,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from pipeline.gc.repo import insert_asset_gc_mark


@dataclass(frozen=True)
class StagedUploadRow:
    """The slice of a ``staged_uploads`` row the resolver/recorder read."""

    id: str
    collection_id: str
    item_id: str | None
    status: str
    prior_item: dict[str, Any] | None
    created_at: dt.datetime | None = None


@dataclass(frozen=True)
class StaleClaim:
    """A ``finalizing`` row stranded past the stale threshold (§6.4)."""

    id: str
    collection_id: str
    item_id: str | None


class FinalizeRepo(PreflightChecks):
    """DB seam for the push producer layers + the finalize sweep."""

    # -- staged_uploads ledger ------------------------------------------------

    @abc.abstractmethod
    async def get_session(self, upload_id: str) -> StagedUploadRow | None:
        """The ledger row for one upload session, or ``None``."""

    @abc.abstractmethod
    async def item_predates(
        self, collection_id: str, item_id: str, before: dt.datetime
    ) -> bool:
        """True when ``item_events`` holds any event for this item strictly
        older than ``before`` — evidence the item existed before the push
        session was minted. Needed because the transaction-API write path
        splits a client PUT into delete+insert outbox events (ISSUES I-46),
        so an ``insert`` op alone does not prove a true create. Residual:
        events die by monthly partition DETACH+DROP (operator-manual, I-11),
        so a long-dormant item could misread as a create — documented in
        ISSUES."""

    @abc.abstractmethod
    async def claim(self, upload_id: str, item_id: str) -> bool:
        """Atomically claim ``pending → finalizing`` and bind ``item_id``
        (§6.4 — the ADR 0004 drain idiom, so concurrent duplicates no-op).
        Only an unbound row or one already bound to this ``item_id`` is
        claimable. Returns True when THIS call took the claim."""

    @abc.abstractmethod
    async def release_claim(self, upload_id: str) -> None:
        """Flip ``finalizing`` back to ``pending`` (item binding kept) — the
        no-op paths (item vanished / session superseded) hand the row back to
        the TTL clock instead of stamping a verdict."""

    @abc.abstractmethod
    async def record_finalized(self, upload_id: str, result: dict[str, Any]) -> None:
        """Stamp ``finalizing → finalized`` with ``result`` + ``finalized_at``."""

    @abc.abstractmethod
    async def record_rejected(
        self, upload_id: str, result: dict[str, Any], error: str | None
    ) -> None:
        """Stamp ``finalizing → rejected`` with ``result``/``error`` +
        ``finalized_at``."""

    # -- catalog (item data only) --------------------------------------------

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or ``None`` if not present."""

    @abc.abstractmethod
    async def delete_item(self, item_id: str, collection_id: str) -> bool:
        """``pgstac.delete_item`` — failure-tolerant (an item already gone
        must not kill the recorder). True on success."""

    # -- GC (ADR 0011) --------------------------------------------------------

    @abc.abstractmethod
    async def mark_asset_prefix(
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        """Idempotent ``asset_gc`` mark (open-key unique index) — the recorder
        marks BEFORE an insert-rejection delete when the steps already moved
        bytes under the canonical prefix (mark-first, ADR 0011)."""

    @abc.abstractmethod
    async def gc_grace_days(self, collection_id: str) -> int:
        """The collection's ``gc_grace_days`` (missing settings row → the
        migration-003 default, 30)."""

    # -- sweep (§6.4) ---------------------------------------------------------

    @abc.abstractmethod
    async def expire_pending(self, ttl_seconds: int) -> int:
        """Flip ``pending`` rows past ``created_at + ttl`` to ``expired`` —
        the §4.1 governing clock (minted but never pushed). Returns count."""

    @abc.abstractmethod
    async def list_stale_finalizing(self, stale_seconds: int, limit: int) -> list[StaleClaim]:
        """``finalizing`` rows whose claim is older than the stale threshold
        (presumed crashed; idempotent to re-run)."""

    @abc.abstractmethod
    async def requeue_stale(self, upload_ids: list[str]) -> None:
        """Flip stale ``finalizing`` rows back to ``pending`` (claim cleared,
        item binding kept) so the re-enqueued job can claim again."""

    # -- staging byte-sweep protection (§4.1 ledger clock) --------------------

    @abc.abstractmethod
    async def list_active_upload_ids(self, ttl_seconds: int) -> set[str]:
        """Upload ids whose ledger row is non-terminal and younger than the
        TTL (by ``created_at`` — or ``claimed_at``, so an actively-finalizing
        session claimed near expiry keeps its bytes). The byte-level TTL sweep
        skips these prefixes; prefixes with no ledger row (legacy debris,
        future Phase 9 runs) keep the object-mtime rule."""


@dataclass
class PgFinalizeRepo(FinalizeRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def item_predates(  # pragma: no cover
        self, collection_id: str, item_id: str, before: dt.datetime
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM stac_higher.item_events"
                " WHERE collection_id = %s AND item_id = %s AND occurred_at < %s)",
                (collection_id, item_id, before),
            )
            row = await cur.fetchone()
        return bool(row and row[0])

    async def get_session(self, upload_id: str) -> StagedUploadRow | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, collection_id, item_id, status, prior_item, created_at"
                " FROM stac_higher.staged_uploads WHERE id = %s",
                (upload_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return StagedUploadRow(
            id=str(row[0]),
            collection_id=row[1],
            item_id=row[2],
            status=row[3],
            prior_item=dict(row[4]) if row[4] else None,
            created_at=row[5],
        )

    async def claim(self, upload_id: str, item_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, claimed_at = now(), item_id = %s"
                " WHERE id = %s AND status = %s"
                " AND (item_id IS NULL OR item_id = %s)",
                (STATUS_FINALIZING, item_id, upload_id, STATUS_PENDING, item_id),
            )
            claimed = (cur.rowcount or 0) > 0
            await conn.commit()
        return claimed

    async def release_claim(self, upload_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, claimed_at = NULL"
                " WHERE id = %s AND status = %s",
                (STATUS_PENDING, upload_id, STATUS_FINALIZING),
            )
            await conn.commit()

    async def record_finalized(  # pragma: no cover
        self, upload_id: str, result: dict[str, Any]
    ) -> None:
        import json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, result = %s::jsonb, error = NULL, finalized_at = now()"
                " WHERE id = %s AND status = %s",
                (STATUS_FINALIZED, json.dumps(result), upload_id, STATUS_FINALIZING),
            )
            await conn.commit()

    async def record_rejected(  # pragma: no cover
        self, upload_id: str, result: dict[str, Any], error: str | None
    ) -> None:
        import json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, result = %s::jsonb, error = %s, finalized_at = now()"
                " WHERE id = %s AND status = %s",
                (STATUS_REJECTED, json.dumps(result), error, upload_id, STATUS_FINALIZING),
            )
            await conn.commit()

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute("SELECT pgstac.get_item(%s, %s)", (item_id, collection_id))
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None

    async def delete_item(self, item_id: str, collection_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            try:
                await conn.execute("SELECT pgstac.delete_item(%s, %s)", (item_id, collection_id))
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    async def mark_asset_prefix(  # pragma: no cover
        self,
        prefix: str,
        collection_id: str,
        item_id: str | None,
        reason: str,
        grace_days: int,
    ) -> bool:
        # The shared insert refuses a cube repository's prefix (ADR 0022).
        async with await self._connect() as conn:
            return await insert_asset_gc_mark(
                conn, prefix, collection_id, item_id, reason, grace_days
            )

    async def gc_grace_days(self, collection_id: str) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT gc_grace_days FROM stac_higher.collection_settings"
                " WHERE collection_id = %s",
                (collection_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 30

    async def collection_archived(self, collection_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT archived FROM stac_higher.collection_settings"
                " WHERE collection_id = %s",
                (collection_id,),
            )
            row = await cur.fetchone()
        return bool(row[0]) if row else False

    async def has_open_gc_mark(self, prefix: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            # A mark covers the prefix when its key equals it or is an
            # ancestor (a collection-delete mark covers every item under it).
            cur = await conn.execute(
                "SELECT 1 FROM stac_higher.asset_gc"
                " WHERE collected_at IS NULL"
                " AND left(%s, length(object_key)) = object_key LIMIT 1",
                (prefix,),
            )
            row = await cur.fetchone()
        return row is not None

    async def expire_pending(self, ttl_seconds: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, finalized_at = now()"
                " WHERE status = %s"
                " AND created_at < now() - make_interval(secs => %s)",
                (STATUS_EXPIRED, STATUS_PENDING, ttl_seconds),
            )
            expired = cur.rowcount or 0
            await conn.commit()
        return expired

    async def list_stale_finalizing(  # pragma: no cover
        self, stale_seconds: int, limit: int
    ) -> list[StaleClaim]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, collection_id, item_id FROM stac_higher.staged_uploads"
                " WHERE status = %s"
                " AND claimed_at < now() - make_interval(secs => %s)"
                " ORDER BY claimed_at LIMIT %s",
                (STATUS_FINALIZING, stale_seconds, limit),
            )
            rows = await cur.fetchall()
        return [StaleClaim(id=str(r[0]), collection_id=r[1], item_id=r[2]) for r in rows]

    async def requeue_stale(self, upload_ids: list[str]) -> None:  # pragma: no cover
        if not upload_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.staged_uploads"
                " SET status = %s, claimed_at = NULL"
                " WHERE id = ANY(%s::uuid[]) AND status = %s",
                (STATUS_PENDING, upload_ids, STATUS_FINALIZING),
            )
            await conn.commit()

    async def list_active_upload_ids(self, ttl_seconds: int) -> set[str]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id FROM stac_higher.staged_uploads"
                " WHERE status IN (%s, %s)"
                " AND (created_at >= now() - make_interval(secs => %s)"
                "      OR claimed_at >= now() - make_interval(secs => %s))",
                (STATUS_PENDING, STATUS_FINALIZING, ttl_seconds, ttl_seconds),
            )
            rows = await cur.fetchall()
        return {str(r[0]) for r in rows}
