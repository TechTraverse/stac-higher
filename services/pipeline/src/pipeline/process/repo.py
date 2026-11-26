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
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from pipeline.process.hardware import DEFAULT_HARDWARE_PROFILE
from pipeline.process.ledger import (
    CAPACITY_RETRY_SECONDS,
    capacity_detail,
    extract_capacity_detail,
)
from pipeline.process.matcher import ProcessSource

logger = logging.getLogger(__name__)

#: How many times `_requeue` retries when a concurrent enqueue takes the slot
#: between its check and its flip. Each retry finds that run and folds, so a
#: second attempt is the most a single race needs.
_REQUEUE_ATTEMPTS = 3

#: K-3: per-profile capacity, profile id -> concurrent runs one host admits
#: (None = unbounded; a profile missing from the map is unbounded too).
Capacities = Mapping[str, "int | None"]

#: The executor backend stamped on a run at admission (`executor_backend`,
#: migration 029). K-5 passes "kubernetes".
DEFAULT_EXECUTOR_BACKEND = "docker"

#: The profile a revision's runs are admitted against (spec §4: an absent
#: `hardware` block is the default profile).
_PROFILE_SQL = f"coalesce(rev.runtime->'hardware'->>'profile', '{DEFAULT_HARDWARE_PROFILE}')"

#: K-3 ruling (narrowed): an extractor run on the DEFAULT profile is exempt
#: from capacity (it is ingest work; extraction throughput must not regress).
#: An extractor whose revision names any other profile is admitted like a
#: process run. Parameterised by %(default_profile)s.
_EXEMPT_SQL = (
    f"(r.association_id IS NOT NULL AND {_PROFILE_SQL} = %(default_profile)s)"
)


#: K-4 A3: the advisory-lock key of the EXEMPT extractor pool. Profile keys
#: are `process-capacity:<id>`, so no profile can collide with it.
EXTRACT_CAPACITY_LOCK = "process-extract-capacity"
#: What `_admit_and_claim` reports when the exempt pool, not a profile, is full.
_EXTRACT_POOL = "\x00extract-pool"


def capacity_exempt(*, is_extract: bool, profile: str) -> bool:
    """The exemption rule in Python (the fake and the launch path use it)."""
    return is_extract and profile == DEFAULT_HARDWARE_PROFILE


class RunLedgerState(NamedTuple):
    """What the reaper reads per run: its status and, once submitted, the
    executor handle of its CURRENT submission (None before
    ``record_submission`` and after a requeue)."""

    status: str
    handle: str | None = None


@dataclass(frozen=True)
class QueuedRun:
    """A run row the worker is about to execute."""

    id: str
    process_id: str
    revision_id: str
    source_id: str | None
    attempts: int
    #: Set for an extractor run (§15); mutually exclusive with source_id in
    #: practice, but the dataclass does not enforce that.
    association_id: str | None = None
    input_items: list[dict[str, Any]] = field(default_factory=list)
    #: Revision payload, joined so the worker needs one round trip, not three.
    runtime: dict[str, Any] = field(default_factory=dict)
    code: str | None = None
    env: list[dict[str, Any]] = field(default_factory=list)
    is_test: bool = False
    #: K-4: the ``started_at`` the claim wrote (I1/I2) — the CLAIM STAMP. Every
    #: write the claiming worker makes before submission is fenced on it, so a
    #: stale worker whose row was requeued and re-claimed under it matches
    #: nothing. None only for a QueuedRun built by hand (unit tests): its
    #: writes are then fenced on ``status = 'running'`` alone.
    claimed_at: dt.datetime | None = None


@dataclass(frozen=True)
class SubmittedRun:
    """K-4: a ``running`` row whose run the executor holds
    (``executor_handle`` set) — what the Reconciler reconciles."""

    id: str
    process_id: str
    revision_id: str
    source_id: str | None
    association_id: str | None
    attempts: int
    is_test: bool
    handle: str
    backend: str
    phase: str | None
    submitted_at: dt.datetime | None
    cancel_requested_at: dt.datetime | None
    credentials_expire_at: dt.datetime | None
    runtime: dict[str, Any] = field(default_factory=dict)
    input_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SubmissionRecord:
    """What :meth:`ProcessRepo.record_submission` learned: whether an operator
    asked to cancel the run before it was submitted (I5)."""

    cancel_requested: bool


#: K-4 (I10): every pipeline move out of ``running`` lands ``cancelled`` once a
#: cancel was requested, and the error keeps what happened after this prefix.
CANCELLED_ERROR = "cancelled by an operator"


class CollectingRun(abc.ABC):
    """K-4 (I7/I8): one submitted run whose row this transaction holds
    ``FOR UPDATE``. Yielded by :meth:`ProcessRepo.collecting` and valid only
    inside that block; at most one of ``finish``/``requeue`` may be called."""

    run: SubmittedRun

    @abc.abstractmethod
    async def finish(
        self,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        at: dt.datetime,
    ) -> str:
        """Write the verdict (``succeeded``/``failed``/``dead``/``cancelled``)
        and, for a source run reaching a terminal verdict, its ``flow_stats``
        in the same transaction. Returns the status written: ``cancelled``
        whenever a cancel was requested (I10)."""

    @abc.abstractmethod
    async def requeue(
        self,
        *,
        error: str | None,
        phase: str | None,
        phase_detail: str | None,
        next_attempt_at: dt.datetime,
    ) -> str:
        """Back to ``queued`` without spending an attempt (lost, credential
        expiry), or FOLD into the queued run holding the slot (#67). Returns
        ``requeued``, ``merged`` or ``cancelled`` (I10)."""


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


@dataclass(frozen=True)
class RunRecord:
    """A run row as the finalize extract branch needs it (G-6)."""

    id: str
    process_id: str
    association_id: str | None
    status: str
    input_items: list[dict[str, Any]] = field(default_factory=list)


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
    async def current_revision(self, process_id: str) -> str | None:
        """The process's deployed revision, or None when nothing is deployed.

        Read at TRIGGER time rather than carried on the dispatch payload, so a
        deploy racing a dispatch pins the deployed revision rather than a
        stale one.
        """

    @abc.abstractmethod
    async def rate_window(self, process_id: str, since: dt.datetime) -> RateWindow:
        """Runs started for this process since ``since``, plus the ceiling."""

    @abc.abstractmethod
    async def process_kind(self, process_id: str) -> str | None:
        """`transform` | `extractor`, or None when the process is gone
        (GOES spec §6). Read where the two kinds diverge — finalize — rather
        than carried on the run, so a process cannot change meaning mid-run
        (kind is create-only in the app anyway)."""

    @abc.abstractmethod
    async def process_is_enabled(self, process_id: str) -> bool:
        """Whether the (live) process is enabled. `current_revision` filters on
        `enabled`, so a disabled process is indistinguishable from an undeployed
        one there; ITEMIZE reads this first so a disabled extractor fails its
        rows with "is disabled" rather than "no deployed revision" (G-6)."""

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
        association_id: str | None = None,
    ) -> str | None:
        """Queue a run, or FOLD the items into this source's pending run
        (§7 coalescing, widened by G-3 to every QUEUED run rather than only
        rate-deferred ones). Returns the run id, or None when the caller had
        nothing to add.

        Coalescing is why this is one statement and not a read-then-write: two
        dispatch wakes racing on the same source must not both see "no pending
        run" and create one each. The partial unique index is the arbiter, and
        the ON CONFLICT path appends instead of failing.

        An extractor run names its ASSOCIATION instead of a source (§15) and
        coalesces per (process, association) the same way.
        """

    async def enqueue_run_detailed(
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
        association_id: str | None = None,
    ) -> tuple[str | None, bool]:
        """:meth:`enqueue_run` plus whether the items were MERGED into a run
        that already existed. The caller needs that to decide whether to ask
        for immediate execution twice for the same row (harmless, but noisy).

        Defaults to "inserted" over :meth:`enqueue_run` so an implementation
        that has no notion of merging keeps working.
        """
        run_id = await self.enqueue_run(
            process_id=process_id,
            revision_id=revision_id,
            source_id=source_id,
            input_items=input_items,
            deferred_until=deferred_until,
            is_test=is_test,
            association_id=association_id,
        )
        return run_id, False

    @abc.abstractmethod
    async def claim_run(
        self,
        run_id: str,
        now: dt.datetime,
        *,
        capacities: Capacities | None = None,
        backend: str = DEFAULT_EXECUTOR_BACKEND,
        extract_capacity: int | None = None,
    ) -> QueuedRun | None:
        """Claim ONE run by id, or None when it is not claimable (already
        running, still deferred, waiting for capacity, or gone).

        The same admission as :meth:`claim_due_runs` with a different
        predicate, deliberately: the immediate-dispatch job (G-3) and the
        periodic tick race by design, and whichever claims first wins while
        the other simply finds nothing.

        **Capacity (K-3, spec §8).** With ``capacities``, a process or test
        run on a bounded profile is ADMITTED only when fewer than
        ``capacity`` runs on that profile are admitted right now (``running``
        with ``executor_backend`` set). Otherwise it is not claimed: it stays
        ``queued`` as ``phase = pending_capacity`` with ``next_attempt_at``
        15 s out, and no attempt is spent. An extractor run
        (``association_id`` set) on the DEFAULT profile is exempt and never
        counts (K-3 ruling: extraction throughput must not regress); an
        extractor on any other profile is admitted like a process run.
        ``capacities=None`` admits everything.

        **The exempt pool (K-4 addendum A3).** With ``extract_capacity``, an
        exempt extractor run is admitted only while fewer than that many
        exempt runs are admitted deployment-wide (its own advisory lock,
        ``EXTRACT_CAPACITY_LOCK``); otherwise it waits exactly as a run on a
        full profile does. None leaves the pool unbounded.
        """

    @abc.abstractmethod
    async def claim_due_runs(
        self,
        now: dt.datetime,
        limit: int,
        *,
        capacities: Capacities | None = None,
        backend: str = DEFAULT_EXECUTOR_BACKEND,
        extract_capacity: int | None = None,
    ) -> list[QueuedRun]:
        """Claim up to ``limit`` runnable rows, oldest first: `queued` with no
        deferral and no pending ``next_attempt_at`` (or ones that have
        elapsed), plus `failed` rows whose retry is due. Each claim is
        admitted as :meth:`claim_run` describes. A profile found full is
        skipped for the rest of the call (its due queued runs are marked
        ``pending_capacity``), so it neither spends the ``limit`` nor holds
        back newer work on other profiles."""

    @abc.abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None:
        """One run row's identity, status and input batch."""

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
        claimed_at: dt.datetime | None = None,
    ) -> str | None:
        """Write a run's terminal (or retry-pending) state.

        With ``claimed_at`` (K-4 I3) this is the claiming worker's verdict on
        a run it never submitted: a compare-and-set on ``status = 'running'``,
        the claim stamp and ``executor_handle IS NULL``. It returns the status
        written — ``cancelled`` when a cancel was requested meanwhile (I10) —
        or None when the CAS matched nothing (the row was re-claimed under a
        stale worker). ``status="queued"`` is refused with ``claimed_at``:
        use :meth:`requeue_without_attempt`.

        Without ``claimed_at`` it is the finalize path's unconditional write
        (I14), unchanged; ``status="queued"`` there is the unfenced
        :meth:`requeue_without_attempt` (kept for callers that never claimed).
        """

    @abc.abstractmethod
    async def wake_capacity_waiters(
        self, profile_id: str, *, exempt: bool, limit: int
    ) -> list[str]:
        """K-4 Task 9 (I-170's latency half): a run just left `running`, so
        its admission pool has a free slot. Clear ``next_attempt_at`` on up to
        ``limit`` of the oldest `queued` runs waiting as `pending_capacity` in
        the same pool — the profile's process runs, or (``exempt``) the
        exempt extractor pool — and return their ids for an immediate claim.
        The claim still runs the admission count, so a wake never
        oversubscribes a pool."""

    @abc.abstractmethod
    async def requeue_without_attempt(
        self,
        run_id: str,
        *,
        claimed_at: dt.datetime | None,
        error: str,
        next_attempt_at: dt.datetime,
    ) -> str:
        """K-4 I4/R13: an unsubmitted run our infrastructure could not start
        goes back to ``queued`` with its attempt restored and its retry time in
        ``next_attempt_at`` — or FOLDS into the queued run holding its slot
        (#67), or lands ``cancelled`` (I10). Fenced on the claim stamp.
        Returns ``requeued`` | ``merged`` | ``cancelled`` | ``skipped``."""

    @abc.abstractmethod
    async def record_submission(
        self,
        run_id: str,
        *,
        claimed_at: dt.datetime | None,
        handle: str,
        backend: str,
        phase: str,
        submitted_at: dt.datetime,
        credentials_expire_at: dt.datetime | None,
    ) -> SubmissionRecord | None:
        """K-4 I5: the run was submitted — write its handle once. None when
        the CAS failed (the claim was lost, or the handle is already set):
        the caller must cancel and reap what it just submitted."""

    @abc.abstractmethod
    async def set_phase(
        self, run_id: str, *, handle: str, phase: str, detail: str | None
    ) -> bool:
        """K-4 I6: a submitted run's phase as the backend reports it. Never
        terminal; False when the row is no longer this handle's ``running``
        row or nothing changed."""

    @abc.abstractmethod
    async def list_submitted_runs(
        self,
        *,
        backend: str,
        limit: int,
        cancel_requested_only: bool = False,
        exclude_cancel_requested: bool = False,
        after_id: str | None = None,
    ) -> list[SubmittedRun]:
        """One page of ``running`` rows with a handle on ``backend``, ordered
        by id, after ``after_id`` (keyset paging); optionally only, or only
        not, the rows whose cancel was requested."""

    @abc.abstractmethod
    async def get_submitted_run(self, run_id: str, *, backend: str) -> SubmittedRun | None:
        """One submitted run of ``backend``, or None."""

    @abc.abstractmethod
    def collecting(
        self, run_id: str, *, handle: str
    ) -> AbstractAsyncContextManager[CollectingRun | None]:
        """K-4 I7/I8: hold the run's row ``FOR UPDATE SKIP LOCKED`` while the
        caller captures logs and writes the verdict. Yields None when the row
        is not this handle's ``running`` row or another observer holds it.
        Commits on a clean exit; rolls back on ANY exception (including
        cancellation), so a crash mid-collection leaves the row ``running``
        for the next observer to collect again."""

    @abc.abstractmethod
    async def list_output_collections(self, process_id: str) -> tuple[str, ...]:
        """The collections this process may publish into.

        Read at FINALIZE time rather than pinned on the run: detaching an
        output should stop publishing there immediately, including for a run
        already in flight.
        """

    @abc.abstractmethod
    async def list_source_collections(self, process_id: str) -> tuple[str, ...]:
        """The distinct collections wired as this process's SOURCES (GOES
        spec §3.2): the run's session policy gets read access to exactly
        their canonical prefixes, and a legacy input ref with no collection
        falls back to the single one."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The pgstac document for one triggering item, or None when it has
        been deleted since the trigger (the planner records a skip)."""

    @abc.abstractmethod
    async def reference_source_hrefs(self, collection_id: str, item_id: str) -> dict[str, str]:
        """filename -> source href for a `storage_mode: reference` item's
        assets, from the ingest ledger (empty for copy-mode or manual items).
        The catalog stores CANONICAL hrefs for reference items too; the app's
        asset route resolves them through this same ledger column
        (`storage/reference.ts`), and the run planner must do the same or it
        grants a key that does not exist."""

    @abc.abstractmethod
    async def reset_stalled_runs(self, older_than: dt.datetime, limit: int) -> int:
        """Return runs stranded `running` by a crashed worker to `queued`.

        The crash direction stays safe: a run that actually finished has
        already left `running`, so the worst case is a duplicate execution,
        not a lost one. A run whose coalescing slot is taken by now folds
        into that queued run rather than aborting the sweep (#67). Returns
        how many runs were requeued, folded or cancelled.

        K-4 (I9, R14): only UNSUBMITTED rows (``executor_handle IS NULL``). A
        submitted run is reconciled through the executor's ``status()``, so
        a run longer than the stall window is never requeued under itself.
        """

    @abc.abstractmethod
    async def run_statuses(self, run_ids: Sequence[str]) -> dict[str, RunLedgerState]:
        """Current ledger status and recorded executor handle for each of
        ``run_ids`` that still exists.

        A read, never a write — the M3-W-1 reaper's only use of the ledger.
        Run ids with no row are simply ABSENT from the result, which the
        reaper reads as the strongest orphan signal there is. The handle
        (K-5 review C1) lets it tell the CURRENT submission of a `running`
        run from a superseded one.
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

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

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
                # G-6: an extractor is never dispatched by item events or cron.
                "   AND p.kind = 'transform'"
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
                # G-6: an extractor is never dispatched by item events or cron.
                "   AND p.kind = 'transform'"
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

    async def current_revision(self, process_id: str) -> str | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT current_revision FROM stac_higher.processes"
                " WHERE id = %s AND deleted_at IS NULL AND enabled",
                (process_id,),
            )
            row = await cur.fetchone()
        return str(row[0]) if row and row[0] else None

    async def process_kind(self, process_id: str) -> str | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT kind FROM stac_higher.processes WHERE id = %s AND deleted_at IS NULL",
                (process_id,),
            )
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def process_is_enabled(self, process_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT enabled FROM stac_higher.processes"
                " WHERE id = %s AND deleted_at IS NULL",
                (process_id,),
            )
            row = await cur.fetchone()
        return bool(row[0]) if row else False

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
        association_id: str | None = None,
    ) -> str | None:
        run_id, _merged = await self.enqueue_run_detailed(
            process_id=process_id,
            revision_id=revision_id,
            source_id=source_id,
            input_items=input_items,
            deferred_until=deferred_until,
            is_test=is_test,
            association_id=association_id,
        )
        return run_id

    async def enqueue_run_detailed(  # pragma: no cover
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
        association_id: str | None = None,
    ) -> tuple[str | None, bool]:
        items = json.dumps(list(input_items))
        async with await self._connect() as conn:
            if source_id is not None and not is_test:
                # §7 coalescing, widened by G-3. The partial unique index
                # (migration 025, process_runs_queued_source_idx) is the
                # arbiter: two wakes racing on the same source cannot both
                # insert, and the loser APPENDS its items to the pending run
                # rather than failing. `||` on a jsonb array is concatenation.
                #
                # A claim flips the row to `running`, which takes it OUT of the
                # index — so an arrival after the claim inserts a new run and
                # its items are never lost to a batch already executing.
                #
                # `xmax = 0` on the returned row distinguishes an insert from
                # an update; a test run is excluded above because folding it
                # into a pending batch would make "run this now" mean
                # something else.
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s, %s)"
                    " ON CONFLICT (process_id, source_id)"
                    "   WHERE status = 'queued' AND source_id IS NOT NULL"
                    " DO UPDATE SET input_items ="
                    "   stac_higher.process_runs.input_items || EXCLUDED.input_items"
                    " RETURNING id, (xmax = 0) AS inserted",
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
                if not row:
                    return None, False
                return str(row[0]), not bool(row[1])

            if association_id is not None and not is_test:
                # §15: extractor runs coalesce per (process, association);
                # migration 027's process_runs_queued_association_idx is the
                # arbiter, the exact shape of the source branch above.
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, association_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, NULL, %s, %s::jsonb, %s, %s)"
                    " ON CONFLICT (process_id, association_id)"
                    "   WHERE status = 'queued' AND association_id IS NOT NULL"
                    " DO UPDATE SET input_items ="
                    "   stac_higher.process_runs.input_items || EXCLUDED.input_items"
                    " RETURNING id, (xmax = 0) AS inserted",
                    (process_id, revision_id, association_id, items, deferred_until, is_test),
                )
                row = await cur.fetchone()
                await conn.commit()
                if not row:
                    return None, False
                return str(row[0]), not bool(row[1])

            cur = await conn.execute(
                "INSERT INTO stac_higher.process_runs"
                " (process_id, revision_id, source_id, association_id, input_items,"
                "  rate_deferred_until, is_test)"
                " VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)"
                " RETURNING id",
                (
                    process_id,
                    revision_id,
                    source_id,
                    association_id,
                    items,
                    deferred_until,
                    is_test,
                ),
            )
            row = await cur.fetchone()
            await conn.commit()
        return (str(row[0]) if row else None), False

    # -- claims + capacity admission (K-3) ------------------------------------
    #
    # THE INVARIANT: at no committed instant are more than `capacity` runs
    # ADMITTED on a profile, whatever the workers' clocks say. Admitted means
    # `status = 'running' AND executor_backend IS NOT NULL`, minus extractor
    # runs on the default profile (exempt, `_EXEMPT_SQL`). Every admission happens in ONE
    # transaction that takes `pg_advisory_xact_lock('process-capacity:' ||
    # profile)`, counts the admitted runs in a statement that starts after
    # the lock is held (so it sees every earlier admission, all of which
    # committed before releasing the lock), and stamps `executor_backend` in
    # the claiming UPDATE before committing. Runs leave the admitted set only
    # by changing status (finish, requeue, stall sweep), which can only lower
    # the count. Lock order is always advisory lock, then row lock: nothing
    # else takes the advisory lock, so no cycle exists. K-4's watcher reuses
    # `executor_backend` as the admitted marker and `next_attempt_at`.

    _QUEUED_DUE = (
        "(r.status = 'queued'"
        " AND (r.rate_deferred_until IS NULL OR r.rate_deferred_until <= %(now)s)"
        " AND (r.next_attempt_at IS NULL OR r.next_attempt_at <= %(now)s))"
    )
    _FAILED_DUE = "(r.status = 'failed' AND r.finished_at <= %(now)s)"
    _RETURNING = (
        " RETURNING r.id, r.process_id, r.revision_id, r.source_id,"
        "           r.association_id, r.attempts, r.input_items,"
        "           rev.runtime, rev.code, rev.env, r.is_test, r.started_at"
    )

    @staticmethod
    def _queued_run(r: Any) -> QueuedRun:
        return QueuedRun(
            id=str(r[0]),
            process_id=str(r[1]),
            revision_id=str(r[2]),
            source_id=str(r[3]) if r[3] else None,
            association_id=str(r[4]) if r[4] else None,
            attempts=int(r[5]),
            input_items=list(r[6] or []),
            runtime=dict(r[7] or {}),
            code=r[8],
            env=list(r[9] or []),
            is_test=bool(r[10]),
            claimed_at=r[11],
        )

    @staticmethod
    async def _begin_read_committed(conn: Any) -> None:
        """K-4 A2: open a transaction pinned to READ COMMITTED. The admission
        count and the collector's locked re-read both need each statement to
        see what committed before it, whatever the database's
        ``default_transaction_isolation`` says. ``SET TRANSACTION`` must be
        the first statement of its transaction, so a caller holding one open
        is a bug, not something to paper over."""
        import psycopg.pq

        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raise RuntimeError("an admission or collection must start a fresh transaction")
        await conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")

    async def _admit_and_claim(  # pragma: no cover
        self,
        conn: Any,
        run_id: str,
        *,
        now: dt.datetime,
        due_sql: str,
        capacities: Capacities | None,
        backend: str,
        mark_profile: bool,
        extract_capacity: int | None = None,
    ) -> tuple[QueuedRun | None, str | None]:
        """One transaction: admit and claim ``run_id``, or not. Returns
        ``(run, None)`` on a claim, ``(None, profile)`` when its profile is
        full, ``(None, None)`` when it is not claimable (someone else took
        it, or it is no longer due)."""
        await self._begin_read_committed(conn)
        cur = await conn.execute(
            f"SELECT {_PROFILE_SQL}, r.association_id IS NOT NULL"
            "  FROM stac_higher.process_runs r"
            "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
            " WHERE r.id = %(id)s",
            {"id": run_id},
        )
        found = await cur.fetchone()
        if found is None:
            await conn.commit()
            return None, None
        profile, is_extract = str(found[0]), bool(found[1])
        exempt = capacity_exempt(is_extract=is_extract, profile=profile)
        if exempt:
            # K-4 A3: the exempt extractor pool, counted deployment-wide.
            capacity = extract_capacity
            lock_key = EXTRACT_CAPACITY_LOCK
            pool_sql = _EXEMPT_SQL
            detail = extract_capacity_detail(capacity) if capacity is not None else ""
        else:
            capacity = None if capacities is None else capacities.get(profile)
            lock_key = f"process-capacity:{profile}"
            pool_sql = f"NOT {_EXEMPT_SQL} AND {_PROFILE_SQL} = %(profile)s"
            detail = capacity_detail(profile, capacity) if capacity is not None else ""
        params = {
            "id": run_id, "now": now, "backend": backend, "profile": profile,
            "default_profile": DEFAULT_HARDWARE_PROFILE,
        }
        if capacity is not None:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
            cur = await conn.execute(
                "SELECT count(*) FROM stac_higher.process_runs r"
                "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
                " WHERE r.status = 'running' AND r.executor_backend IS NOT NULL"
                f"  AND {pool_sql}",
                params,
            )
            admitted = int((await cur.fetchone())[0])
            if admitted >= capacity:
                # Not claimed, no attempt spent: the queued run says why and
                # when it may be tried again. The tick marks every due queued
                # run in the pool at once (`mark_profile`).
                target = pool_sql if mark_profile else "r.id = %(id)s"
                await conn.execute(
                    "UPDATE stac_higher.process_runs r"
                    "   SET phase = 'pending_capacity', phase_detail = %(detail)s,"
                    "       next_attempt_at = %(retry_at)s"
                    "  FROM stac_higher.process_revisions rev"
                    f" WHERE rev.id = r.revision_id AND {self._QUEUED_DUE} AND {target}",
                    {
                        **params,
                        "detail": detail,
                        "retry_at": now + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS),
                    },
                )
                await conn.commit()
                return None, _EXTRACT_POOL if exempt else profile
        cur = await conn.execute(
            "UPDATE stac_higher.process_runs r"
            "    SET status = 'running', started_at = %(now)s,"
            "        attempts = r.attempts + 1,"
            "        rate_deferred_until = NULL,"
            "        phase = 'starting', phase_detail = NULL, next_attempt_at = NULL,"
            # The admitted marker (K-3); K-4 I1: nothing of an earlier
            # attempt's submission survives into this one.
            "        executor_backend = %(backend)s, executor_handle = NULL,"
            "        submitted_at = NULL, credentials_expire_at = NULL"
            "   FROM stac_higher.process_revisions rev"
            f" WHERE r.id = %(id)s AND rev.id = r.revision_id AND {due_sql}"
            + self._RETURNING,
            params,
        )
        row = await cur.fetchone()
        await conn.commit()
        return (self._queued_run(row) if row else None), None

    async def claim_due_runs(  # pragma: no cover
        self,
        now: dt.datetime,
        limit: int,
        *,
        capacities: Capacities | None = None,
        backend: str = DEFAULT_EXECUTOR_BACKEND,
        extract_capacity: int | None = None,
    ) -> list[QueuedRun]:
        # Candidates are read without locks, oldest first (ties by id), then
        # admitted one transaction each. A full profile joins `full` and is
        # excluded from every later page, so it costs one candidate per call,
        # not a slice of the batch; a lost race just moves on. Each loop
        # either claims, fills a profile, or retires a candidate, so it ends.
        claimed: list[QueuedRun] = []
        full: list[str] = []
        extract_full = False
        tried: list[str] = []
        due = f"({self._QUEUED_DUE} OR {self._FAILED_DUE})"
        async with await self._connect() as conn:
            while len(claimed) < limit:
                cur = await conn.execute(
                    "SELECT r.id::text FROM stac_higher.process_runs r"
                    "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
                    f" WHERE {due}"
                    "   AND NOT (r.id::text = ANY(%(tried)s::text[]))"
                    f"   AND (({_EXEMPT_SQL} AND NOT %(extract_full)s)"
                    f"        OR (NOT {_EXEMPT_SQL}"
                    f"            AND NOT ({_PROFILE_SQL} = ANY(%(full)s::text[]))))"
                    " ORDER BY r.created_at, r.id"
                    " LIMIT %(page)s",
                    {
                        "now": now, "tried": tried, "full": full,
                        "extract_full": extract_full,
                        "page": limit - len(claimed),
                        "default_profile": DEFAULT_HARDWARE_PROFILE,
                    },
                )
                candidates = [str(r[0]) for r in await cur.fetchall()]
                await conn.commit()
                if not candidates:
                    break
                for run_id in candidates:
                    tried.append(run_id)
                    run, full_profile = await self._admit_and_claim(
                        conn, run_id, now=now, due_sql=due, capacities=capacities,
                        backend=backend, mark_profile=True,
                        extract_capacity=extract_capacity,
                    )
                    if run is not None:
                        claimed.append(run)
                    elif full_profile == _EXTRACT_POOL:
                        extract_full = True
                        break  # re-page without exempt runs
                    elif full_profile is not None:
                        full.append(full_profile)
                        break  # re-page without that profile
        return claimed

    async def claim_run(  # pragma: no cover
        self,
        run_id: str,
        now: dt.datetime,
        *,
        capacities: Capacities | None = None,
        backend: str = DEFAULT_EXECUTOR_BACKEND,
        extract_capacity: int | None = None,
    ) -> QueuedRun | None:
        """The tick's admission with an id predicate (G-3). Only a `queued`
        row whose deferral and ``next_attempt_at`` (if any) have elapsed is
        claimable — a `failed` row's retry stays the tick's job, since its
        wait is a schedule, not an event."""
        async with await self._connect() as conn:
            run, _full = await self._admit_and_claim(
                conn, run_id, now=now, due_sql=self._QUEUED_DUE, capacities=capacities,
                backend=backend, mark_profile=False, extract_capacity=extract_capacity,
            )
        return run

    async def get_run(self, run_id: str) -> RunRecord | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, process_id, association_id, status, input_items"
                "  FROM stac_higher.process_runs WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return RunRecord(
            id=str(row[0]), process_id=str(row[1]),
            association_id=str(row[2]) if row[2] else None,
            status=str(row[3]), input_items=list(row[4] or []),
        )

    #: K-4 I3/I7/I10: the verdict UPDATE. A requested cancel wins: the row
    #: lands `cancelled` and the error keeps what happened after the prefix.
    #: The fence (claim stamp or handle) is appended by the caller.
    _FINISH_SQL = (
        "UPDATE stac_higher.process_runs"
        "   SET status = CASE WHEN cancel_requested_at IS NOT NULL"
        "                     THEN 'cancelled' ELSE %(status)s END,"
        "       error = CASE WHEN cancel_requested_at IS NOT NULL"
        f"                    THEN '{CANCELLED_ERROR}' || coalesce('; ' || %(error)s, '')"
        "                    ELSE %(error)s END,"
        "       log_ref = coalesce(%(log_ref)s, log_ref),"
        "       output_items = coalesce(%(output)s::jsonb, output_items),"
        "       phase = NULL, phase_detail = NULL, next_attempt_at = NULL,"
        # `failed` rows carry their retry due-time in finished_at, which is
        # what claim_due_runs compares against; terminal rows carry the
        # actual finish time.
        "       finished_at = CASE WHEN cancel_requested_at IS NOT NULL THEN now()"
        "                          ELSE coalesce(%(next_attempt_at)s, now()) END"
        " WHERE id = %(id)s AND status = 'running'"
    )

    async def finish_run(  # pragma: no cover
        self,
        run_id: str,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        output_items: Sequence[dict[str, Any]] | None = None,
        claimed_at: dt.datetime | None = None,
    ) -> str | None:
        if status == "queued":
            if claimed_at is not None:
                raise ValueError("a claimed run is requeued by requeue_without_attempt")
            outcome = await self.requeue_without_attempt(
                run_id, claimed_at=None, error=error or "",
                next_attempt_at=next_attempt_at or dt.datetime.now(dt.UTC),
            )
            return None if outcome == "skipped" else "queued"
        params = {
            "id": run_id, "status": status, "error": error, "log_ref": log_ref,
            "output": json.dumps(list(output_items)) if output_items is not None else None,
            "next_attempt_at": next_attempt_at, "claimed_at": claimed_at,
        }
        async with await self._connect() as conn:
            if claimed_at is not None:
                cur = await conn.execute(
                    self._FINISH_SQL
                    + " AND started_at = %(claimed_at)s AND executor_handle IS NULL"
                    " RETURNING status",
                    params,
                )
                row = await cur.fetchone()
                await conn.commit()
                return str(row[0]) if row else None
            # I14: the finalize path's unconditional write on its own row.
            await conn.execute(
                "UPDATE stac_higher.process_runs"
                "   SET status = %(status)s, error = %(error)s,"
                "       log_ref = coalesce(%(log_ref)s, log_ref),"
                "       output_items = coalesce(%(output)s::jsonb, output_items),"
                # K-3: phase is a sub-state of queued/running only.
                "       phase = NULL, phase_detail = NULL,"
                "       finished_at = coalesce(%(next_attempt_at)s, now())"
                " WHERE id = %(id)s",
                params,
            )
            await conn.commit()
        return status

    #: K-4 R13: what every "no attempt spent" requeue writes. The attempt the
    #: claim added is given back; the retry time lives in next_attempt_at,
    #: which is what the claim of a `queued` row reads.
    _RESTORE_ATTEMPT_SQL = (
        "started_at = NULL, attempts = greatest(attempts - 1, 0), error = %(error)s,"
        " phase = %(phase)s, phase_detail = %(detail)s, next_attempt_at = %(at)s"
    )

    async def requeue_without_attempt(  # pragma: no cover
        self,
        run_id: str,
        *,
        claimed_at: dt.datetime | None,
        error: str,
        next_attempt_at: dt.datetime,
    ) -> str:
        async with await self._connect() as conn:
            return await self._requeue(
                conn,
                run_id,
                set_sql=self._RESTORE_ATTEMPT_SQL,
                params={"phase": None, "detail": None, "at": next_attempt_at},
                error=error,
                claimed_at=claimed_at,
                unsubmitted=True,
            )

    async def wake_capacity_waiters(  # pragma: no cover
        self, profile_id: str, *, exempt: bool, limit: int
    ) -> list[str]:
        pool = _EXEMPT_SQL if exempt else f"NOT {_EXEMPT_SQL} AND {_PROFILE_SQL} = %(profile)s"
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.process_runs w SET next_attempt_at = NULL"
                " WHERE w.id IN ("
                "   SELECT r.id FROM stac_higher.process_runs r"
                "     JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
                "    WHERE r.status = 'queued' AND r.phase = 'pending_capacity'"
                f"     AND {pool}"
                "    ORDER BY r.created_at, r.id LIMIT %(limit)s"
                "    FOR UPDATE OF r SKIP LOCKED)"
                " RETURNING w.id::text",
                {"profile": profile_id, "limit": limit,
                 "default_profile": DEFAULT_HARDWARE_PROFILE},
            )
            ids = [str(r[0]) for r in await cur.fetchall()]
            await conn.commit()
        return ids

    async def record_submission(  # pragma: no cover
        self,
        run_id: str,
        *,
        claimed_at: dt.datetime | None,
        handle: str,
        backend: str,
        phase: str,
        submitted_at: dt.datetime,
        credentials_expire_at: dt.datetime | None,
    ) -> SubmissionRecord | None:
        fence = " AND started_at = %(claimed_at)s" if claimed_at is not None else ""
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.process_runs"
                "   SET executor_handle = %(handle)s, submitted_at = %(submitted_at)s,"
                "       phase = %(phase)s, phase_detail = NULL,"
                "       credentials_expire_at = %(expires)s"
                " WHERE id = %(id)s AND status = 'running' AND executor_handle IS NULL"
                # The claim stamped the backend (K-3's admitted marker).
                "   AND executor_backend = %(backend)s"
                f"{fence}"
                " RETURNING cancel_requested_at IS NOT NULL",
                {
                    "id": run_id, "handle": handle, "backend": backend, "phase": phase,
                    "submitted_at": submitted_at, "expires": credentials_expire_at,
                    "claimed_at": claimed_at,
                },
            )
            row = await cur.fetchone()
            await conn.commit()
        return SubmissionRecord(cancel_requested=bool(row[0])) if row else None

    async def set_phase(  # pragma: no cover
        self, run_id: str, *, handle: str, phase: str, detail: str | None
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.process_runs"
                "   SET phase = %(p)s, phase_detail = %(d)s"
                " WHERE id = %(id)s AND status = 'running' AND executor_handle = %(h)s"
                "   AND (phase IS DISTINCT FROM %(p)s OR phase_detail IS DISTINCT FROM %(d)s)",
                {"id": run_id, "h": handle, "p": phase, "d": detail},
            )
            changed = cur.rowcount == 1
            await conn.commit()
        return changed

    _SUBMITTED_SELECT = (
        "SELECT r.id, r.process_id, r.revision_id, r.source_id, r.association_id,"
        "       r.attempts, r.is_test, r.executor_handle, r.executor_backend, r.phase,"
        "       r.submitted_at, r.cancel_requested_at, r.credentials_expire_at,"
        "       rev.runtime, r.input_items"
        "  FROM stac_higher.process_runs r"
        "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
        " WHERE r.status = 'running' AND r.executor_handle IS NOT NULL"
    )

    @staticmethod
    def _submitted_run(r: Any) -> SubmittedRun:
        return SubmittedRun(
            id=str(r[0]), process_id=str(r[1]), revision_id=str(r[2]),
            source_id=str(r[3]) if r[3] else None,
            association_id=str(r[4]) if r[4] else None,
            attempts=int(r[5]), is_test=bool(r[6]), handle=str(r[7]), backend=str(r[8]),
            phase=r[9], submitted_at=r[10], cancel_requested_at=r[11],
            credentials_expire_at=r[12], runtime=dict(r[13] or {}),
            input_items=list(r[14] or []),
        )

    async def list_submitted_runs(  # pragma: no cover
        self,
        *,
        backend: str,
        limit: int,
        cancel_requested_only: bool = False,
        exclude_cancel_requested: bool = False,
        after_id: str | None = None,
    ) -> list[SubmittedRun]:
        where = ""
        if cancel_requested_only:
            where += " AND r.cancel_requested_at IS NOT NULL"
        if exclude_cancel_requested:
            where += " AND r.cancel_requested_at IS NULL"
        if after_id is not None:
            where += " AND r.id > %(after)s::uuid"
        async with await self._connect() as conn:
            cur = await conn.execute(
                self._SUBMITTED_SELECT
                + f" AND r.executor_backend = %(backend)s{where}"
                " ORDER BY r.id LIMIT %(limit)s",
                {"backend": backend, "limit": limit, "after": after_id},
            )
            rows = await cur.fetchall()
        return [self._submitted_run(r) for r in rows]

    async def get_submitted_run(  # pragma: no cover
        self, run_id: str, *, backend: str
    ) -> SubmittedRun | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                self._SUBMITTED_SELECT
                + " AND r.executor_backend = %(backend)s AND r.id = %(id)s",
                {"backend": backend, "id": run_id},
            )
            row = await cur.fetchone()
        return self._submitted_run(row) if row else None

    @asynccontextmanager
    async def collecting(  # pragma: no cover
        self, run_id: str, *, handle: str
    ) -> AsyncIterator[CollectingRun | None]:
        async with await self._connect() as conn:
            await self._begin_read_committed(conn)
            try:
                cur = await conn.execute(
                    self._SUBMITTED_SELECT
                    + " AND r.id = %(id)s AND r.executor_handle = %(h)s"
                    " FOR UPDATE OF r SKIP LOCKED",
                    {"id": run_id, "h": handle},
                )
                row = await cur.fetchone()
                yield _PgCollectingRun(self, conn, self._submitted_run(row)) if row else None
            except BaseException:
                await conn.rollback()
                raise
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

    async def list_source_collections(  # pragma: no cover
        self, process_id: str
    ) -> tuple[str, ...]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT collection_id FROM stac_higher.process_sources"
                " WHERE process_id = %s ORDER BY collection_id",
                (process_id,),
            )
            rows = await cur.fetchall()
        return tuple(r[0] for r in rows)

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT pgstac.get_item(%s, %s)", (item_id, collection_id)
            )
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None

    async def reference_source_hrefs(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (filename) filename, source_href FROM ("
                "  SELECT regexp_replace(f.source_path, '^.*/', '') AS filename,"
                "         f.source_href, f.version"
                "    FROM stac_higher.ingest_files f"
                "    JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                "   WHERE cc.collection_id = %s AND f.item_id = %s"
                "     AND f.source_href IS NOT NULL"
                "     AND f.reference_removed_at IS NULL"
                ") x ORDER BY filename, version DESC",
                (collection_id, item_id),
            )
            rows = await cur.fetchall()
        return {str(r[0]): str(r[1]) for r in rows}

    async def reference_source_files(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, tuple[str, str]]:
        """filename -> (source_href, association_id): ``reference_source_hrefs``
        plus the association that produced each file (the cube writer reads a
        source only through it, spec §6.1)."""
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (filename) filename, source_href, association_id FROM ("
                "  SELECT regexp_replace(f.source_path, '^.*/', '') AS filename,"
                "         f.source_href, f.association_id, f.version"
                "    FROM stac_higher.ingest_files f"
                "    JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                "   WHERE cc.collection_id = %s AND f.item_id = %s"
                "     AND f.source_href IS NOT NULL"
                "     AND f.reference_removed_at IS NULL"
                ") x ORDER BY filename, version DESC",
                (collection_id, item_id),
            )
            rows = await cur.fetchall()
        return {str(r[0]): (str(r[1]), str(r[2])) for r in rows}

    async def reset_stalled_runs(  # pragma: no cover
        self, older_than: dt.datetime, limit: int
    ) -> int:
        # One run at a time, oldest first, each in its own transaction: a
        # single UPDATE of every stalled row aborted on the first slot
        # collision and recovered NOTHING, for any process (#67). Oldest
        # first means a slot's earliest stalled run takes the slot and the
        # later ones fold into it.
        #
        # K-4 I9: only UNSUBMITTED rows. A submitted run is reconciled through
        # the executor's status(), however long it runs (R14).
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id FROM stac_higher.process_runs"
                " WHERE status = 'running' AND started_at < %s"
                "   AND executor_handle IS NULL"
                " ORDER BY started_at LIMIT %s",
                (older_than, limit),
            )
            stalled = [str(r[0]) for r in await cur.fetchall()]
            await conn.commit()
            recovered = 0
            for run_id in stalled:
                outcome = await self._requeue(
                    conn,
                    run_id,
                    set_sql="started_at = NULL, phase = NULL, phase_detail = NULL,"
                    " error = %(error)s",
                    params={},
                    error="run was stranded by a worker or executor crash",
                    # Re-checked under the row lock: a run that finished, was
                    # submitted or was re-claimed since the SELECT is not stalled.
                    stalled_before=older_than,
                    unsubmitted=True,
                )
                if outcome != "skipped":
                    recovered += 1
        return recovered

    async def _requeue(  # pragma: no cover
        self,
        conn: Any,
        run_id: str,
        *,
        set_sql: str,
        params: dict[str, Any],
        error: str | None,
        stalled_before: dt.datetime | None = None,
        claimed_at: dt.datetime | None = None,
        handle: str | None = None,
        unsubmitted: bool = False,
    ) -> str:
        """Return a `running` run to `queued` in its own transaction, retrying
        a fold race; see :func:`_requeue_once` for the rule. Returns
        ``"requeued"``, ``"merged"``, ``"cancelled"`` or ``"skipped"``."""
        import psycopg

        for _attempt in range(_REQUEUE_ATTEMPTS):
            try:
                outcome = await _requeue_once(
                    conn, run_id, set_sql=set_sql, params=params, error=error,
                    stalled_before=stalled_before, claimed_at=claimed_at, handle=handle,
                    unsubmitted=unsubmitted,
                )
                await conn.commit()
                return outcome
            except psycopg.errors.UniqueViolation:
                await conn.rollback()
        raise RuntimeError(
            f"run {run_id}: its coalescing slot kept changing under the requeue"
        )

    async def run_statuses(  # pragma: no cover
        self, run_ids: Sequence[str]
    ) -> dict[str, RunLedgerState]:
        ids = list(run_ids)
        if not ids:
            return {}
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, status, executor_handle FROM stac_higher.process_runs"
                " WHERE id = ANY(%s::uuid[])",
                (ids,),
            )
            rows = await cur.fetchall()
        return {row[0]: RunLedgerState(row[1], row[2]) for row in rows}

    async def record_source_run(  # pragma: no cover
        self, source_id: str, *, succeeded: bool, at: dt.datetime
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                _RECORD_SOURCE_RUN_SQL,
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


#: Every requeue gives the executor columns back: the run is no longer
#: admitted (K-3's marker) and nothing of this attempt's submission survives.
_CLEAR_EXECUTOR_SQL = (
    "executor_backend = NULL, executor_handle = NULL, submitted_at = NULL,"
    " credentials_expire_at = NULL"
)


async def _requeue_once(  # pragma: no cover
    conn: Any,
    run_id: str,
    *,
    set_sql: str,
    params: dict[str, Any],
    error: str | None,
    stalled_before: dt.datetime | None = None,
    claimed_at: dt.datetime | None = None,
    handle: str | None = None,
    unsubmitted: bool = False,
) -> str:
    """Return a `running` run to `queued`, or FOLD it into the queued run that
    already holds its coalescing slot (#67). Runs inside the caller's
    transaction and never commits.

    Migrations 025/027 allow one `queued` run per (process, source) and per
    (process, association), and a newer trigger may have queued one while
    this run was running — so flipping it back would violate the index.
    Instead its items are appended to the queued run (the `enqueue_run` ON
    CONFLICT rule, applied late), its `extracting` ledger rows move to that
    run, and its own row is deleted. The QUEUED run survives, not this one:
    an ITEMIZE that just folded files into it is about to point their ledger
    rows at its id, and a row that never started has no history to lose.
    Deleting the running row also hands the M3-W-1 reaper its strongest
    orphan signal if a container of it is somehow still alive. Test runs
    never coalesce (`enqueue_run`), so they are only flipped. A race that
    queues a run between the slot check and the flip raises the unique
    violation; the caller retries and the next attempt folds.

    K-4: the fences are re-checked under the row lock — ``claimed_at`` (the
    claim stamp, I4), ``handle`` (a submitted run's handle, I8),
    ``stalled_before`` (I9) and ``unsubmitted`` (no handle) — and a run whose
    cancel was requested lands `cancelled` rather than requeueing (I10).
    ``set_sql`` may use ``%(error)s``.
    """
    predicates = ["id = %(run)s", "status = 'running'"]
    if claimed_at is not None:
        predicates.append("started_at = %(claimed_at)s")
    if stalled_before is not None:
        predicates.append("started_at < %(before)s")
    if unsubmitted:
        predicates.append("executor_handle IS NULL")
    if handle is not None:
        predicates.append("executor_handle = %(handle)s")
    fence = {"run": run_id, "claimed_at": claimed_at, "before": stalled_before,
             "handle": handle}
    cur = await conn.execute(
        "SELECT process_id, source_id, association_id, is_test,"
        "       cancel_requested_at IS NOT NULL"
        "  FROM stac_higher.process_runs"
        f" WHERE {' AND '.join(predicates)}"
        "   FOR UPDATE",
        fence,
    )
    row = await cur.fetchone()
    if row is None:
        return "skipped"
    process_id, source_id, association_id, is_test, cancel_requested = row

    if cancel_requested:
        # I10: cancel wins over a requeue — the operator asked for it to stop.
        await conn.execute(
            "UPDATE stac_higher.process_runs"
            "   SET status = 'cancelled', finished_at = now(),"
            "       phase = NULL, phase_detail = NULL, next_attempt_at = NULL,"
            f"      error = '{CANCELLED_ERROR}' || coalesce('; ' || %(error)s, '')"
            " WHERE id = %(run)s",
            {"run": run_id, "error": error},
        )
        return "cancelled"

    holder = None
    if not is_test:
        # Either index can be the one occupied; NULL never matches.
        cur = await conn.execute(
            "SELECT id FROM stac_higher.process_runs"
            " WHERE status = 'queued' AND process_id = %(process)s"
            "   AND (source_id = %(source)s OR association_id = %(assoc)s)"
            " ORDER BY created_at LIMIT 1 FOR UPDATE",
            {"process": process_id, "source": source_id, "assoc": association_id},
        )
        holder = await cur.fetchone()

    if holder is None:
        await conn.execute(
            f"UPDATE stac_higher.process_runs SET status = 'queued', {set_sql},"
            f" {_CLEAR_EXECUTOR_SQL}"
            " WHERE id = %(run)s",
            {**params, "error": error, "run": run_id},
        )
        return "requeued"

    into = {"run": run_id, "into": holder[0]}
    await conn.execute(
        "UPDATE stac_higher.process_runs q"
        "   SET input_items = q.input_items || r.input_items"
        "  FROM stac_higher.process_runs r"
        " WHERE q.id = %(into)s AND r.id = %(run)s",
        into,
    )
    # Finalize only lands rows the run owns (extract_run.py), so moving the
    # batch without moving ownership would strand them `extracting`.
    await conn.execute(
        "UPDATE stac_higher.ingest_files SET extract_run_id = %(into)s"
        " WHERE extract_run_id = %(run)s AND status = 'extracting'",
        into,
    )
    await conn.execute("DELETE FROM stac_higher.process_runs WHERE id = %(run)s", into)
    logger.info(
        "process run folded into the queued run holding its slot",
        extra={"run_id": run_id, "into_run_id": str(holder[0]),
               "process_id": str(process_id)},
    )
    return "merged"


#: The pipeline-written `flow_stats` rollup bump (M2-A shape).
_RECORD_SOURCE_RUN_SQL = (
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
    " WHERE id = %(id)s"
)


class _PgCollectingRun(CollectingRun):  # pragma: no cover - DB-gated tests
    """The row is locked by the enclosing ``collecting`` transaction; every
    write here joins it and commits (or rolls back) with it."""

    def __init__(self, repo: PgProcessRepo, conn: Any, run: SubmittedRun) -> None:
        self._repo = repo
        self._conn = conn
        self.run = run
        self._done = False

    def _once(self) -> None:
        if self._done:
            raise RuntimeError(f"run {self.run.id}: its verdict was already written")
        self._done = True

    async def finish(
        self,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        at: dt.datetime,
    ) -> str:
        self._once()
        cur = await self._conn.execute(
            PgProcessRepo._FINISH_SQL + " AND executor_handle = %(handle)s RETURNING status",
            {
                "id": self.run.id, "status": status, "error": error, "log_ref": log_ref,
                "output": None, "next_attempt_at": next_attempt_at, "handle": self.run.handle,
            },
        )
        row = await cur.fetchone()
        if row is None:  # cannot happen: the row is locked as running/handle
            raise RuntimeError(f"run {self.run.id}: the locked row was not running")
        written = str(row[0])
        # flow_stats in the same transaction, so it is exactly-once with the
        # verdict. A test run or manual re-run is source-less and counts
        # nothing; a cancelled run is neutral (spec §9).
        if self.run.source_id and written in ("succeeded", "dead"):
            await self._conn.execute(
                _RECORD_SOURCE_RUN_SQL,
                {"id": self.run.source_id, "ok": written == "succeeded", "at": at.isoformat()},
            )
        return written

    async def requeue(
        self,
        *,
        error: str | None,
        phase: str | None,
        phase_detail: str | None,
        next_attempt_at: dt.datetime,
    ) -> str:
        import psycopg

        self._once()
        for _attempt in range(_REQUEUE_ATTEMPTS):
            try:
                # A savepoint: a fold race must not abort the outer
                # (collecting) transaction.
                async with self._conn.transaction():
                    return await _requeue_once(
                        self._conn, self.run.id,
                        set_sql=PgProcessRepo._RESTORE_ATTEMPT_SQL,
                        params={"phase": phase, "detail": phase_detail, "at": next_attempt_at},
                        error=error, handle=self.run.handle,
                    )
            except psycopg.errors.UniqueViolation:
                continue
        raise RuntimeError(
            f"run {self.run.id}: its coalescing slot kept changing under the requeue"
        )
