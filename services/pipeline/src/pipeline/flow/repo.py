"""Repository seam for the flow monitor (M2-B, ROADMAP §6.6).

Mirrors the other repos: a :class:`FlowMonitorRepo` ABC the monitor logic
depends on (unit-tested against ``FakeMonitorRepo``) plus a psycopg
``PgFlowMonitorRepo``. Pg methods open a short-lived connection and are
``# pragma: no cover`` — exercised by the M2-I rehearsal / DB integration.

Ownership (ADR 0001): reads ``collection_connections`` / ``connections`` /
``delivery_log`` / ``ingest_files`` / ``delivery_backfills`` /
``staged_uploads`` (P7-H); INSERT/UPDATEs only ``stac_higher.alerts`` (raise,
``last_seen`` bump, auto-resolve). The app owns the DDL (migrations 014/021)
and the ack/resolve verbs. Never runs DDL.
"""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlowCandidate:
    """An enabled association with a declared §5.1 expectation."""

    id: str
    collection_id: str
    direction: str  # "ingest" | "deliver"
    connection_id: str
    expectation: dict[str, Any]
    flow_stats: dict[str, Any] = field(default_factory=dict)
    #: GREATEST(created_at, updated_at) — the inactivity fallback when the
    #: rollup has no last_activity_at yet (a fresh or just-edited association
    #: starts its window at the edit, not at epoch).
    edited_at: dt.datetime | None = None


@dataclass
class ErrorConnection:
    id: str
    name: str
    last_error: str | None = None


@dataclass(frozen=True)
class AlertCondition:
    """One currently-true alerting condition. ``(source, kind, connection_id,
    association_id, channel_id, collection_id)`` is the dedup identity (spec
    §3.3; M2-C added the channel leg for webhook-failure alerts, P7-H the
    collection leg for push rejections)."""

    source: str  # "flow" | "health" | "job_failure"
    kind: str
    connection_id: str | None = None
    association_id: str | None = None
    #: Channel-anchored alerts (M2-C ``webhook_failed``); None for the
    #: monitor's own kinds.
    channel_id: str | None = None
    #: Collection-anchored alerts (P7-H ``push_rejected`` — push has no
    #: connection/association/channel); None for every other kind.
    collection_id: str | None = None
    message: str = ""


class FlowMonitorRepo(abc.ABC):
    @abc.abstractmethod
    async def list_flow_candidates(self) -> list[FlowCandidate]:
        """Enabled, non-deleted associations (with enabled connections) whose
        ``expectation`` is declared — the only ones flow-alerting evaluates
        (§6.6: absence-of-data is only detectable against a declared
        expectation)."""

    @abc.abstractmethod
    async def has_breaching_delivery(
        self, association_id: str, window_seconds: int
    ) -> bool:
        """True when a non-terminal delivery_log row (pending/delivering/
        failed) has been outstanding longer than the SLO window — the item is
        already late even though no terminal latency exists yet."""

    @abc.abstractmethod
    async def list_error_connections(self) -> list[ErrorConnection]:
        """Enabled, non-deleted connections currently in ``status='error'``
        (the health sweep's ok→error output, observed as state — idempotent
        across ticks; dedup makes it equivalent to a transition hook)."""

    @abc.abstractmethod
    async def list_dead_delivery_counts(self) -> list[tuple[str, int]]:
        """``(association_id, dead_row_count)`` for enabled associations with
        dead-lettered deliveries. Clears (auto-resolve) when the rows are
        redelivered."""

    @abc.abstractmethod
    async def list_terminal_ingest_failures(
        self, max_retries: int
    ) -> list[tuple[str, int]]:
        """``(association_id, count)`` of LATEST-version ledger rows that are
        ``failed`` with their retry budget spent — terminal until the source
        bytes change or an operator intervenes."""

    @abc.abstractmethod
    async def list_failed_backfills(self) -> list[tuple[str, str, str | None]]:
        """``(association_id, backfill_id, error)`` where the association's
        MOST RECENT backfill failed. A newer successful backfill clears it."""

    @abc.abstractmethod
    async def list_recent_push_rejections(
        self, lookback_seconds: int
    ) -> list[tuple[str, dt.datetime]]:
        """``(collection_id, finalized_at)`` for every ``staged_uploads`` row
        with ``status = 'rejected'`` whose verdict landed inside the lookback
        window (Phase 7 §8). The resolved-at floor is applied MONITOR-side
        (:func:`pipeline.flow.monitor.evaluate_push_rejections`) so the
        semantics stay unit-testable."""

    @abc.abstractmethod
    async def latest_push_rejected_resolved_at(self) -> dict[str, dt.datetime]:
        """Per collection, the most recent ``resolved_at`` among RESOLVED
        ``push_rejected`` alerts — the §8 floor. Rejected ledger rows are
        terminal, so without it a manual resolve would re-fire on the next
        tick for the remainder of the lookback."""

    @abc.abstractmethod
    async def sync_alerts(
        self, conditions: list[AlertCondition], owned_kinds: tuple[str, ...]
    ) -> tuple[int, int]:
        """Reconcile the alerts table with the currently-true conditions, in
        one transaction: raise a new ``firing`` row per unseen condition, bump
        ``last_seen`` (+ message) on re-observed ones — including
        ``acknowledged`` rows, which keep tracking — and auto-resolve open
        alerts of the monitor's OWN kinds whose condition cleared. Kinds this
        monitor does not own (e.g. M2-C's webhook-failure alerts) are never
        touched. Returns ``(newly_raised, auto_resolved)``."""


@dataclass
class PgFlowMonitorRepo(FlowMonitorRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def list_flow_candidates(self) -> list[FlowCandidate]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT cc.id, cc.collection_id, cc.direction, cc.connection_id,"
                "       cc.expectation, cc.flow_stats,"
                "       GREATEST(cc.created_at, cc.updated_at)"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.expectation IS NOT NULL"
                " AND cc.enabled = true AND c.enabled = true"
                " AND cc.deleted_at IS NULL AND c.deleted_at IS NULL"
            )
            rows = await cur.fetchall()
        return [
            FlowCandidate(
                id=str(r[0]),
                collection_id=r[1],
                direction=r[2],
                connection_id=str(r[3]),
                expectation=dict(r[4]) if r[4] else {},
                flow_stats=dict(r[5]) if r[5] else {},
                edited_at=r[6],
            )
            for r in rows
        ]

    async def has_breaching_delivery(  # pragma: no cover
        self, association_id: str, window_seconds: int
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM stac_higher.delivery_log"
                "  WHERE association_id = %s"
                "  AND status IN ('pending', 'delivering', 'failed')"
                "  AND COALESCE(item_created_at, created_at)"
                "      < now() - make_interval(secs => %s))",
                (association_id, window_seconds),
            )
            row = await cur.fetchone()
        return bool(row and row[0])

    async def list_error_connections(self) -> list[ErrorConnection]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, name, last_error FROM stac_higher.connections"
                " WHERE enabled = true AND deleted_at IS NULL AND status = 'error'"
            )
            rows = await cur.fetchall()
        return [ErrorConnection(id=str(r[0]), name=r[1], last_error=r[2]) for r in rows]

    async def list_dead_delivery_counts(self) -> list[tuple[str, int]]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT dl.association_id, count(*)"
                " FROM stac_higher.delivery_log dl"
                " JOIN stac_higher.collection_connections cc ON cc.id = dl.association_id"
                " WHERE dl.status = 'dead'"
                " AND cc.enabled = true AND cc.deleted_at IS NULL"
                " GROUP BY dl.association_id"
            )
            rows = await cur.fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

    async def list_terminal_ingest_failures(  # pragma: no cover
        self, max_retries: int
    ) -> list[tuple[str, int]]:
        async with await self._connect() as conn:
            # Latest version per (association, source_path) only: a failed row
            # superseded by a re-ingested newer version is history, not a
            # standing failure.
            cur = await conn.execute(
                "SELECT f.association_id, count(*) FROM ("
                "  SELECT DISTINCT ON (association_id, source_path)"
                "         association_id, status, retries"
                "  FROM stac_higher.ingest_files"
                "  ORDER BY association_id, source_path, version DESC"
                ") f"
                " JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                " WHERE f.status = 'failed' AND f.retries >= %s"
                " AND cc.enabled = true AND cc.deleted_at IS NULL"
                " GROUP BY f.association_id",
                (max_retries,),
            )
            rows = await cur.fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

    async def list_failed_backfills(  # pragma: no cover
        self,
    ) -> list[tuple[str, str, str | None]]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT b.association_id, b.id, b.error FROM ("
                "  SELECT DISTINCT ON (association_id) association_id, id, status, error"
                "  FROM stac_higher.delivery_backfills"
                "  ORDER BY association_id, created_at DESC"
                ") b"
                " JOIN stac_higher.collection_connections cc ON cc.id = b.association_id"
                " WHERE b.status = 'failed'"
                " AND cc.enabled = true AND cc.deleted_at IS NULL"
            )
            rows = await cur.fetchall()
        return [(str(r[0]), str(r[1]), r[2]) for r in rows]

    async def list_recent_push_rejections(  # pragma: no cover
        self, lookback_seconds: int
    ) -> list[tuple[str, dt.datetime]]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT collection_id, finalized_at"
                " FROM stac_higher.staged_uploads"
                " WHERE status = 'rejected'"
                " AND finalized_at > now() - make_interval(secs => %s)",
                (lookback_seconds,),
            )
            rows = await cur.fetchall()
        return [(str(r[0]), r[1]) for r in rows]

    async def latest_push_rejected_resolved_at(  # pragma: no cover
        self,
    ) -> dict[str, dt.datetime]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT collection_id, max(resolved_at)"
                " FROM stac_higher.alerts"
                " WHERE source = 'job_failure' AND kind = 'push_rejected'"
                " AND state = 'resolved' AND collection_id IS NOT NULL"
                " AND resolved_at IS NOT NULL"
                " GROUP BY collection_id"
            )
            rows = await cur.fetchall()
        return {str(r[0]): r[1] for r in rows}

    async def sync_alerts(  # pragma: no cover
        self, conditions: list[AlertCondition], owned_kinds: tuple[str, ...]
    ) -> tuple[int, int]:
        raised = 0
        async with await self._connect() as conn:
            for c in conditions:
                # The partial unique index (migrations 014/015/021) is the
                # arbiter: a re-observed condition bumps last_seen + message
                # on the open row (firing OR acknowledged — ack suppresses
                # notification, not detection). xmax = 0 marks a fresh row.
                # The conflict target must match the index expressions
                # EXACTLY, including the M2-C channel leg and the P7-H
                # collection leg (text column — no cast).
                cur = await conn.execute(
                    "INSERT INTO stac_higher.alerts"
                    " (source, kind, connection_id, association_id, channel_id,"
                    "  collection_id, message)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (source, kind,"
                    "   coalesce(connection_id::text, ''),"
                    "   coalesce(association_id::text, ''),"
                    "   coalesce(channel_id::text, ''),"
                    "   coalesce(collection_id, ''))"
                    " WHERE state <> 'resolved'"
                    " DO UPDATE SET last_seen = now(), message = EXCLUDED.message"
                    " RETURNING (xmax = 0)",
                    (
                        c.source,
                        c.kind,
                        c.connection_id,
                        c.association_id,
                        c.channel_id,
                        c.collection_id,
                        c.message,
                    ),
                )
                row = await cur.fetchone()
                if row and row[0]:
                    raised += 1
            # Auto-resolve: open alerts of OUR kinds whose condition is no
            # longer present this tick. Other writers' kinds are untouched.
            cur = await conn.execute(
                "UPDATE stac_higher.alerts a"
                " SET state = 'resolved', resolved_at = now()"
                " WHERE a.state <> 'resolved' AND a.kind = ANY(%s)"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM unnest("
                "     %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],"
                "     %s::text[])"
                "     AS c(source, kind, conn, assoc, chan, coll)"
                "   WHERE c.source = a.source AND c.kind = a.kind"
                "   AND c.conn = coalesce(a.connection_id::text, '')"
                "   AND c.assoc = coalesce(a.association_id::text, '')"
                "   AND c.chan = coalesce(a.channel_id::text, '')"
                "   AND c.coll = coalesce(a.collection_id, ''))",
                (
                    list(owned_kinds),
                    [c.source for c in conditions],
                    [c.kind for c in conditions],
                    [c.connection_id or "" for c in conditions],
                    [c.association_id or "" for c in conditions],
                    [c.channel_id or "" for c in conditions],
                    [c.collection_id or "" for c in conditions],
                ),
            )
            resolved = cur.rowcount or 0
            await conn.commit()
        return raised, resolved
