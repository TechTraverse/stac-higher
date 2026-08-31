"""Repository seam over the process tables (spec §6, ADR 0001).

A ``ProcessRepo`` ABC the run/dispatch logic depends on (unit-tested against
an in-memory fake) plus a psycopg ``PgProcessRepo`` for production, mirroring
dispatcher/repo.py. Pg methods open a short-lived AsyncConnection and are
``# pragma: no cover`` — their SQL is exercised by the M5-G rehearsal and the
DB-gated integration test, not by unit tests.

Ownership: the APP owns this DDL and writes processes/revisions/sources/
outputs; the PIPELINE (here) inserts and updates ``process_runs``, updates
``process_sources.flow_stats``, and drains ``process_checks``. Never DDL.

The two statements worth reading closely are ``enqueue_run`` (the §7
coalescing rule) and ``claim_run`` (the crash-safe claim) — both are written
so concurrency is handled by the DATABASE rather than by hoping two workers
do not overlap.
"""

from __future__ import annotations

import abc
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.process.matcher import ProcessSource


@dataclass(frozen=True)
class QueuedRun:
    """A run row the worker is about to execute."""

    id: str
    process_id: str
    revision_id: str
    source_id: str | None
    attempts: int
    input_items: list[dict[str, Any]] = field(default_factory=list)
    #: Revision payload, joined so the worker needs one round trip, not three.
    runtime: dict[str, Any] = field(default_factory=dict)
    code: str | None = None
    env: list[dict[str, Any]] = field(default_factory=list)
    is_test: bool = False


@dataclass(frozen=True)
class CronSource:
    """A due `cron` source (the ingest-scheduler pattern)."""

    id: str
    process_id: str
    collection_id: str
    trigger: dict[str, Any]
    current_revision: str | None
    max_runs_per_hour: int
    last_run_at: dt.datetime | None


@dataclass(frozen=True)
class RateWindow:
    """What the §7 ceiling needs to decide, in one read."""

    recent_runs: int
    oldest_in_window: dt.datetime | None
    max_runs_per_hour: int


@dataclass(frozen=True)
class ProcessCheckRequest:
    """A UI test-run request awaiting a run (ADR 0004)."""

    id: str
    process_id: str
    revision_id: str


class ProcessRepo(abc.ABC):
    # -- reads the dispatcher and scheduler need -----------------------------

    @abc.abstractmethod
    async def list_item_event_sources(self, collection_id: str) -> list[ProcessSource]:
        """Enabled sources on an ENABLED, non-deleted process for a collection.

        The process-level filters live here rather than in the matcher because
        a disabled process should not even be considered — matching it and
        then discarding the match would put a runaway process's cost back on
        the dispatch path.
        """

    @abc.abstractmethod
    async def list_due_cron_sources(self, now: dt.datetime) -> list[CronSource]:
        """Enabled cron sources whose schedule is due."""

    @abc.abstractmethod
    async def rate_window(self, process_id: str, since: dt.datetime) -> RateWindow:
        """Runs started for this process since ``since``, plus the ceiling."""

    # -- the run ledger ------------------------------------------------------

    @abc.abstractmethod
    async def enqueue_run(
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
    ) -> str | None:
        """Queue a run, or FOLD the items into this source's already-deferred
        run (§7 coalescing). Returns the run id, or None when the caller had
        nothing to add.

        Coalescing is why this is one statement and not a read-then-write: two
        dispatch ticks racing on the same source must not both see "no
        deferred run" and create one each. The partial unique index is the
        arbiter, and the ON CONFLICT path appends instead of failing.
        """

    @abc.abstractmethod
    async def claim_due_runs(self, now: dt.datetime, limit: int) -> list[QueuedRun]:
        """Atomically claim runnable rows: `queued` with no deferral (or one
        that has elapsed), plus `failed` rows whose retry is due. Stamps
        `running` + `started_at` + attempts in the claiming statement so
        overlapping workers cannot double-claim (the I-40 idiom)."""

    @abc.abstractmethod
    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        output_items: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        """Write a run's terminal (or retry-pending) state."""

    @abc.abstractmethod
    async def list_output_collections(self, process_id: str) -> tuple[str, ...]:
        """The collections this process may publish into.

        Read at FINALIZE time rather than pinned on the run: detaching an
        output should stop publishing there immediately, including for a run
        already in flight.
        """

    @abc.abstractmethod
    async def reset_stalled_runs(self, older_than: dt.datetime, limit: int) -> int:
        """Return runs stranded `running` by a crashed worker to `queued`.

        The crash direction stays safe: a run that actually finished has
        already left `running`, so the worst case is a duplicate execution,
        not a lost one.
        """

    @abc.abstractmethod
    async def record_source_run(
        self, source_id: str, *, succeeded: bool, at: dt.datetime
    ) -> None:
        """Bump the source's pipeline-written `flow_stats` rollup (M2-A shape).

        The app reads it and never writes it, so this is the only writer.
        """

    # -- the ADR 0004 test-run bridge ----------------------------------------

    @abc.abstractmethod
    async def claim_process_checks(self, limit: int) -> list[ProcessCheckRequest]:
        """Claim pending test-run requests (FOR UPDATE SKIP LOCKED)."""

    @abc.abstractmethod
    async def attach_check_run(self, check_id: str, run_id: str) -> None:
        """Point a claimed check at the run it produced."""

    @abc.abstractmethod
    async def finish_check(
        self, check_id: str, *, status: str, result: dict[str, Any] | None
    ) -> None:
        """Write a test request's outcome for the UI poll."""


@dataclass
class PgProcessRepo(ProcessRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def list_item_event_sources(  # pragma: no cover
        self, collection_id: str
    ) -> list[ProcessSource]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT s.id, s.process_id, s.collection_id, s.trigger"
                "  FROM stac_higher.process_sources s"
                "  JOIN stac_higher.processes p ON p.id = s.process_id"
                " WHERE s.collection_id = %s AND s.enabled"
                "   AND p.enabled AND p.deleted_at IS NULL"
                "   AND p.current_revision IS NOT NULL"
                "   AND s.trigger->>'kind' = 'item_event'",
                (collection_id,),
            )
            rows = await cur.fetchall()
        return [
            ProcessSource(
                id=str(r[0]),
                process_id=str(r[1]),
                collection_id=r[2],
                trigger=dict(r[3] or {}),
            )
            for r in rows
        ]

    async def list_due_cron_sources(  # pragma: no cover
        self, now: dt.datetime
    ) -> list[CronSource]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT s.id, s.process_id, s.collection_id, s.trigger,"
                "       p.current_revision, p.max_runs_per_hour,"
                "       (SELECT max(r.created_at) FROM stac_higher.process_runs r"
                "         WHERE r.source_id = s.id)"
                "  FROM stac_higher.process_sources s"
                "  JOIN stac_higher.processes p ON p.id = s.process_id"
                " WHERE s.enabled AND p.enabled AND p.deleted_at IS NULL"
                "   AND p.current_revision IS NOT NULL"
                "   AND s.trigger->>'kind' = 'cron'"
            )
            rows = await cur.fetchall()
        return [
            CronSource(
                id=str(r[0]),
                process_id=str(r[1]),
                collection_id=r[2],
                trigger=dict(r[3] or {}),
                current_revision=str(r[4]) if r[4] else None,
                max_runs_per_hour=int(r[5]),
                last_run_at=r[6],
            )
            for r in rows
        ]

    async def rate_window(  # pragma: no cover
        self, process_id: str, since: dt.datetime
    ) -> RateWindow:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT count(*)::int, min(r.created_at), p.max_runs_per_hour"
                "  FROM stac_higher.processes p"
                "  LEFT JOIN stac_higher.process_runs r"
                "    ON r.process_id = p.id AND r.created_at >= %s"
                "   AND r.rate_deferred_until IS NULL"
                " WHERE p.id = %s"
                " GROUP BY p.max_runs_per_hour",
                (since, process_id),
            )
            row = await cur.fetchone()
        if not row:
            return RateWindow(recent_runs=0, oldest_in_window=None, max_runs_per_hour=0)
        return RateWindow(
            recent_runs=int(row[0]),
            oldest_in_window=row[1],
            max_runs_per_hour=int(row[2]),
        )

    async def enqueue_run(  # pragma: no cover
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
    ) -> str | None:
        items = json.dumps(list(input_items))
        async with await self._connect() as conn:
            if deferred_until is not None and source_id is not None:
                # §7 coalescing. The partial unique index
                # (process_runs_deferred_source_idx) is the arbiter: two ticks
                # racing to defer the same source cannot both insert, and the
                # loser APPENDS its items to the existing deferred run rather
                # than failing. `||` on a jsonb array is concatenation.
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s, %s)"
                    " ON CONFLICT (source_id)"
                    "   WHERE rate_deferred_until IS NOT NULL"
                    "     AND status = 'queued' AND source_id IS NOT NULL"
                    " DO UPDATE SET input_items ="
                    "   stac_higher.process_runs.input_items || EXCLUDED.input_items"
                    " RETURNING id",
                    (
                        process_id,
                        revision_id,
                        source_id,
                        items,
                        deferred_until,
                        is_test,
                    ),
                )
            else:
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s, %s)"
                    " RETURNING id",
                    (
                        process_id,
                        revision_id,
                        source_id,
                        items,
                        deferred_until,
                        is_test,
                    ),
                )
            row = await cur.fetchone()
            await conn.commit()
        return str(row[0]) if row else None

    async def claim_due_runs(  # pragma: no cover
        self, now: dt.datetime, limit: int
    ) -> list[QueuedRun]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH due AS ("
                "  SELECT r.id FROM stac_higher.process_runs r"
                "   WHERE (r.status = 'queued'"
                "          AND (r.rate_deferred_until IS NULL"
                "               OR r.rate_deferred_until <= %(now)s))"
                "      OR (r.status = 'failed' AND r.finished_at <= %(now)s)"
                "   ORDER BY r.created_at"
                "   LIMIT %(limit)s"
                "   FOR UPDATE SKIP LOCKED"
                ")"
                " UPDATE stac_higher.process_runs r"
                "    SET status = 'running', started_at = %(now)s,"
                "        attempts = r.attempts + 1,"
                "        rate_deferred_until = NULL"
                "   FROM due, stac_higher.process_revisions rev"
                "  WHERE r.id = due.id AND rev.id = r.revision_id"
                " RETURNING r.id, r.process_id, r.revision_id, r.source_id,"
                "           r.attempts, r.input_items, rev.runtime, rev.code,"
                "           rev.env, r.is_test",
                {"now": now, "limit": limit},
            )
            rows = await cur.fetchall()
            await conn.commit()
        return [
            QueuedRun(
                id=str(r[0]),
                process_id=str(r[1]),
                revision_id=str(r[2]),
                source_id=str(r[3]) if r[3] else None,
                attempts=int(r[4]),
                input_items=list(r[5] or []),
                runtime=dict(r[6] or {}),
                code=r[7],
                env=list(r[8] or []),
                is_test=bool(r[9]),
            )
            for r in rows
        ]

    async def finish_run(  # pragma: no cover
        self,
        run_id: str,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        output_items: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.process_runs"
                "   SET status = %s, error = %s,"
                "       log_ref = coalesce(%s, log_ref),"
                "       output_items = coalesce(%s::jsonb, output_items),"
                # `failed` rows carry their retry due-time in finished_at,
                # which is what claim_due_runs compares against; terminal rows
                # carry the actual finish time.
                "       finished_at = coalesce(%s, now())"
                " WHERE id = %s",
                (
                    status,
                    error,
                    log_ref,
                    json.dumps(list(output_items)) if output_items is not None else None,
                    next_attempt_at,
                    run_id,
                ),
            )
            await conn.commit()

    async def list_output_collections(  # pragma: no cover
        self, process_id: str
    ) -> tuple[str, ...]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT collection_id FROM stac_higher.process_outputs"
                " WHERE process_id = %s ORDER BY collection_id",
                (process_id,),
            )
            rows = await cur.fetchall()
        return tuple(r[0] for r in rows)

    async def reset_stalled_runs(  # pragma: no cover
        self, older_than: dt.datetime, limit: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH stalled AS ("
                "  SELECT id FROM stac_higher.process_runs"
                "   WHERE status = 'running' AND started_at < %s"
                "   ORDER BY started_at LIMIT %s FOR UPDATE SKIP LOCKED"
                ")"
                " UPDATE stac_higher.process_runs r"
                "    SET status = 'queued', started_at = NULL,"
                "        error = 'run was stranded by a worker or executor crash'"
                "   FROM stalled WHERE r.id = stalled.id",
                (older_than, limit),
            )
            await conn.commit()
        return cur.rowcount or 0

    async def record_source_run(  # pragma: no cover
        self, source_id: str, *, succeeded: bool, at: dt.datetime
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.process_sources"
                "   SET flow_stats = coalesce(flow_stats, '{}'::jsonb)"
                "     || jsonb_build_object("
                "          'runs',"
                "          coalesce((flow_stats->>'runs')::int, 0) + 1,"
                "          'last_run_at', %(at)s::text)"
                "     || CASE WHEN %(ok)s"
                "             THEN jsonb_build_object('last_success_at', %(at)s::text)"
                "             ELSE jsonb_build_object("
                "                    'last_error_at', %(at)s::text,"
                "                    'failed',"
                "                    coalesce((flow_stats->>'failed')::int, 0) + 1)"
                "        END"
                " WHERE id = %(id)s",
                {"id": source_id, "ok": succeeded, "at": at.isoformat()},
            )
            await conn.commit()

    async def claim_process_checks(  # pragma: no cover
        self, limit: int
    ) -> list[ProcessCheckRequest]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH pending AS ("
                "  SELECT id FROM stac_higher.process_checks"
                "   WHERE status = 'pending'"
                "   ORDER BY requested_at LIMIT %s FOR UPDATE SKIP LOCKED"
                ")"
                " UPDATE stac_higher.process_checks c"
                "    SET status = 'running'"
                "   FROM pending WHERE c.id = pending.id"
                " RETURNING c.id, c.process_id, c.revision_id",
                (limit,),
            )
            rows = await cur.fetchall()
            await conn.commit()
        return [
            ProcessCheckRequest(
                id=str(r[0]), process_id=str(r[1]), revision_id=str(r[2])
            )
            for r in rows
        ]

    async def attach_check_run(self, check_id: str, run_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.process_checks SET run_id = %s WHERE id = %s",
                (run_id, check_id),
            )
            await conn.commit()

    async def finish_check(  # pragma: no cover
        self, check_id: str, *, status: str, result: dict[str, Any] | None
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.process_checks"
                "   SET status = %s, result = %s::jsonb, finished_at = now()"
                " WHERE id = %s",
                (status, json.dumps(result) if result is not None else None, check_id),
            )
            await conn.commit()
