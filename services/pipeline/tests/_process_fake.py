"""In-memory ProcessRepo for M5-C unit tests."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from pipeline.process.ledger import (
    CAPACITY_RETRY_SECONDS,
    capacity_detail,
    extract_capacity_detail,
)
from pipeline.process.matcher import ProcessSource
from pipeline.process.repo import (
    CANCELLED_ERROR,
    CollectingRun,
    CronSource,
    ProcessCheckRequest,
    ProcessRepo,
    QueuedRun,
    RateWindow,
    RunRecord,
    SubmissionRecord,
    SubmittedRun,
    capacity_exempt,
)


@dataclass
class FakeProcessRepo(ProcessRepo):
    sources: dict[str, list[ProcessSource]] = field(default_factory=dict)
    cron_sources: list[CronSource] = field(default_factory=list)
    windows: dict[str, RateWindow] = field(default_factory=dict)
    due_runs: list[QueuedRun] = field(default_factory=list)
    checks: list[ProcessCheckRequest] = field(default_factory=list)
    output_collections: list[str] = field(default_factory=list)
    #: GOES spec §3: what the planner reads before launch.
    items: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    source_collections: list[str] = field(default_factory=list)
    #: G-6 Task 9b: what the ingest ledger says about reference-mode assets —
    #: (collection, item) -> {filename: source href}.
    source_hrefs: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    #: What `current_revision` reports; None models "nothing deployed".
    deployed_revision: str | None = "rev-1"
    #: What `process_kind` reports per process id; defaults to "transform".
    kinds: dict[str, str] = field(default_factory=dict)
    #: Process ids `process_is_enabled` reports False for; everything else True.
    disabled_processes: set[str] = field(default_factory=set)
    #: The revision payload `claim_run` returns with a claimed row.
    runtime: dict[str, Any] = field(default_factory=lambda: {"kind": "inline_python"})
    code: str | None = "pass"

    #: recorded writes
    enqueued: list[dict[str, Any]] = field(default_factory=list)
    finished: list[dict[str, Any]] = field(default_factory=list)
    source_runs: list[tuple[str, bool]] = field(default_factory=list)
    stalled_reset: int = 0
    #: K-3: runs admitted on each profile OUTSIDE this fake's own rows (a
    #: busy host). The fake's own admitted rows are counted too, the way the
    #: Pg claim counts every `running` row with `executor_backend` set.
    admitted_elsewhere: dict[str, int] = field(default_factory=dict)
    #: mirrors migration 025's/027's partial unique indexes: (process,
    #: ("src", source) | ("assoc", association)) → the QUEUED run id. A claim
    #: removes the entry, exactly as flipping the row to `running` drops it
    #: out of the partial index.
    _queued: dict[tuple[str, tuple[str, str]], str] = field(default_factory=dict)
    _next_id: int = 0
    #: K-4: what record_submission was asked to write, and folds (run, into).
    submissions: list[dict[str, Any]] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def list_item_event_sources(self, collection_id: str) -> list[ProcessSource]:
        return self.sources.get(collection_id, [])

    async def list_due_cron_sources(self, now: dt.datetime) -> list[CronSource]:
        return list(self.cron_sources)

    async def current_revision(self, process_id: str) -> str | None:
        return self.deployed_revision

    async def process_kind(self, process_id: str) -> str | None:
        return self.kinds.get(process_id, "transform")

    async def process_is_enabled(self, process_id: str) -> bool:
        return process_id not in self.disabled_processes

    async def rate_window(self, process_id: str, since: dt.datetime) -> RateWindow:
        return self.windows.get(
            process_id, RateWindow(recent_runs=0, oldest_in_window=None, max_runs_per_hour=60)
        )

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
        # Model migrations 025/027: at most one QUEUED run per (process,
        # source) or per (process, association), and a second trigger APPENDS
        # to it. Test runs are excluded, as in the real statement.
        key: tuple[str, str] | None = None
        if source_id is not None and not is_test:
            key = ("src", source_id)
        elif association_id is not None and not is_test:
            key = ("assoc", association_id)
        if key is not None:
            existing = self._queued.get((process_id, key))
            if existing is not None:
                for row in self.enqueued:
                    if row["run_id"] == existing:
                        row["input_items"] = list(row["input_items"]) + list(input_items)
                return existing, True
        self._next_id += 1
        run_id = f"run-{self._next_id}"
        self.enqueued.append(
            {
                "run_id": run_id,
                "process_id": process_id,
                "revision_id": revision_id,
                "source_id": source_id,
                "association_id": association_id,
                "input_items": list(input_items),
                "deferred_until": deferred_until,
                "is_test": is_test,
                "status": "queued",
            }
        )
        if key is not None:
            self._queued[(process_id, key)] = run_id
        return run_id, False

    def _profile(self) -> str:
        """Every fake row runs `self.runtime`, so they share its profile."""
        hardware = self.runtime.get("hardware") or {}
        return str(hardware.get("profile") or "standard")

    def admitted(self, profile: str) -> int:
        own = sum(
            1
            for row in self.enqueued
            if row["status"] == "running"
            and row.get("executor_backend")
            and not capacity_exempt(
                is_extract=row["association_id"] is not None, profile=self._profile()
            )
        )
        return self.admitted_elsewhere.get(profile, 0) + (
            own if profile == self._profile() else 0
        )

    def admitted_exempt(self) -> int:
        """Exempt extractor runs admitted right now (addendum A3)."""
        return sum(
            1
            for row in self.enqueued
            if row["status"] == "running"
            and row.get("executor_backend")
            and capacity_exempt(
                is_extract=row["association_id"] is not None, profile=self._profile()
            )
        )

    async def claim_run(
        self,
        run_id: str,
        now: dt.datetime,
        *,
        capacities: Mapping[str, int | None] | None = None,
        backend: str = "docker",
        extract_capacity: int | None = None,
    ) -> QueuedRun | None:
        for row in self.enqueued:
            if row["run_id"] != run_id or row["status"] != "queued":
                continue
            deferred = row["deferred_until"]
            if deferred is not None and deferred > now:
                return None
            retry_at = row.get("next_attempt_at")
            if retry_at is not None and retry_at > now:
                return None
            # K-3: the Pg claim's admission (extractor runs exempt).
            profile = self._profile()
            exempt = capacity_exempt(
                is_extract=row["association_id"] is not None, profile=profile
            )
            if exempt:
                # K-4 A3: the deployment-wide exempt extractor pool.
                capacity = extract_capacity
                full = capacity is not None and self.admitted_exempt() >= capacity
                detail = extract_capacity_detail(capacity) if capacity is not None else None
            else:
                capacity = None if capacities is None else capacities.get(profile)
                full = capacity is not None and self.admitted(profile) >= capacity
                detail = capacity_detail(profile, capacity) if capacity is not None else None
            if full:
                row["phase"] = "pending_capacity"
                row["phase_detail"] = detail
                row["next_attempt_at"] = now + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
                return None
            row["status"] = "running"
            row["phase"] = "starting"
            row["phase_detail"] = None
            row["next_attempt_at"] = None
            row["executor_backend"] = backend
            # K-4 I1: the claim stamp, and nothing of an earlier submission.
            row["started_at"] = now
            row["attempts"] = row.get("attempts", 0) + 1
            row["executor_handle"] = None
            row["submitted_at"] = None
            row["credentials_expire_at"] = None
            # Out of the partial index, so a later trigger starts a new run.
            if row["source_id"] is not None:
                self._queued.pop((row["process_id"], ("src", row["source_id"])), None)
            if row["association_id"] is not None:
                self._queued.pop((row["process_id"], ("assoc", row["association_id"])), None)
            return QueuedRun(
                id=run_id,
                process_id=row["process_id"],
                revision_id=row["revision_id"],
                source_id=row["source_id"],
                association_id=row["association_id"],
                attempts=row["attempts"],
                input_items=list(row["input_items"]),
                runtime=self.runtime,
                code=self.code,
                env=[],
                is_test=row["is_test"],
                claimed_at=now,
            )
        return None

    async def claim_due_runs(
        self,
        now: dt.datetime,
        limit: int,
        *,
        capacities: Mapping[str, int | None] | None = None,
        backend: str = "docker",
        extract_capacity: int | None = None,
    ) -> list[QueuedRun]:
        # Scripted: the tick's admission and paging are Pg-only and covered by
        # tests/test_integration_process_capacity.py.
        batch = self.due_runs[:limit]
        self.due_runs = self.due_runs[limit:]
        # K-4: each scripted run becomes a `running` row carrying the claim
        # stamp, so the ledger's compare-and-set predicates apply to it.
        claimed = []
        for run in batch:
            row = self._row(run.id)
            if row is None:
                row = {
                    "run_id": run.id, "process_id": run.process_id,
                    "revision_id": run.revision_id, "source_id": run.source_id,
                    "association_id": run.association_id,
                    "input_items": list(run.input_items), "deferred_until": None,
                    "is_test": run.is_test,
                }
                self.enqueued.append(row)
            row.update(
                status="running", phase="starting", phase_detail=None,
                next_attempt_at=None, executor_backend=backend, started_at=now,
                attempts=run.attempts, executor_handle=None, submitted_at=None,
                credentials_expire_at=None,
            )
            claimed.append(replace(run, claimed_at=now))
        return claimed

    def _row(self, run_id: str) -> dict[str, Any] | None:
        return next((row for row in self.enqueued if row["run_id"] == run_id), None)

    async def get_run(self, run_id: str) -> RunRecord | None:
        for row in self.enqueued:
            if row["run_id"] == run_id:
                return RunRecord(
                    id=row["run_id"],
                    process_id=row["process_id"],
                    association_id=row["association_id"],
                    status=row["status"],
                    input_items=list(row["input_items"]),
                )
        return None

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
        if status == "queued":
            if claimed_at is not None:
                raise ValueError("a claimed run is requeued by requeue_without_attempt")
            outcome = await self.requeue_without_attempt(
                run_id, claimed_at=None, error=error or "",
                next_attempt_at=next_attempt_at or dt.datetime.now(dt.UTC),
            )
            return None if outcome == "skipped" else "queued"
        row = self._row(run_id)
        if claimed_at is not None and row is not None and (
            row["status"] != "running"
            or row.get("started_at") != claimed_at
            or row.get("executor_handle") is not None
        ):
            return None  # I3: the CAS matched nothing
        written = status
        if claimed_at is not None and row is not None and row.get("cancel_requested_at"):
            written = "cancelled"
            error = CANCELLED_ERROR + (f"; {error}" if error else "")
        self.finished.append(
            {
                "run_id": run_id,
                "status": written,
                "error": error,
                "log_ref": log_ref,
                "next_attempt_at": next_attempt_at,
            }
        )
        if row is not None:
            row.update(status=written, phase=None, phase_detail=None, error=error)
            if log_ref is not None:
                row["log_ref"] = log_ref
        return written

    def _requeue_row(
        self,
        row: dict[str, Any],
        *,
        error: str | None,
        phase: str | None,
        phase_detail: str | None,
        next_attempt_at: dt.datetime | None,
        restore_attempt: bool,
    ) -> str:
        """`_requeue_once` in memory: cancel wins (I10), else fold into an
        occupied slot (#67), else back to `queued` with the executor columns
        cleared."""
        if row.get("cancel_requested_at"):
            row.update(
                status="cancelled", phase=None, phase_detail=None, next_attempt_at=None,
                error=CANCELLED_ERROR + (f"; {error}" if error else ""),
            )
            return "cancelled"
        key: tuple[str, str] | None = None
        if not row["is_test"]:
            if row["source_id"] is not None:
                key = ("src", row["source_id"])
            elif row["association_id"] is not None:
                key = ("assoc", row["association_id"])
        holder = self._queued.get((row["process_id"], key)) if key else None
        if holder is not None and holder != row["run_id"]:
            into = self._row(holder)
            if into is not None:
                into["input_items"] = list(into["input_items"]) + list(row["input_items"])
            self.enqueued.remove(row)
            self.merged.append((row["run_id"], holder))
            return "merged"
        row.update(
            status="queued", error=error, phase=phase, phase_detail=phase_detail,
            next_attempt_at=next_attempt_at, started_at=None, executor_backend=None,
            executor_handle=None, submitted_at=None, credentials_expire_at=None,
        )
        if restore_attempt:
            row["attempts"] = max(row.get("attempts", 0) - 1, 0)
        if key is not None:
            self._queued[(row["process_id"], key)] = row["run_id"]
        return "requeued"

    async def requeue_without_attempt(
        self,
        run_id: str,
        *,
        claimed_at: dt.datetime | None,
        error: str,
        next_attempt_at: dt.datetime,
    ) -> str:
        row = self._row(run_id)
        self.finished.append(
            {"run_id": run_id, "status": "queued", "error": error, "log_ref": None,
             "next_attempt_at": next_attempt_at}
        )
        if row is None:
            return "requeued"  # a hand-built QueuedRun the fake never claimed
        if (
            row["status"] != "running"
            or row.get("executor_handle") is not None
            or (claimed_at is not None and row.get("started_at") != claimed_at)
        ):
            return "skipped"
        outcome = self._requeue_row(
            row, error=error, phase=None, phase_detail=None,
            next_attempt_at=next_attempt_at, restore_attempt=True,
        )
        self.finished[-1]["status"] = "cancelled" if outcome == "cancelled" else "queued"
        return outcome

    async def wake_capacity_waiters(
        self, profile_id: str, *, exempt: bool, limit: int
    ) -> list[str]:
        woken: list[str] = []
        for row in self.enqueued:
            if len(woken) >= limit:
                break
            row_exempt = capacity_exempt(
                is_extract=row["association_id"] is not None, profile=self._profile()
            )
            in_pool = row_exempt if exempt else (
                not row_exempt and self._profile() == profile_id
            )
            if row["status"] == "queued" and row.get("phase") == "pending_capacity" and in_pool:
                row["next_attempt_at"] = None
                woken.append(row["run_id"])
        return woken

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
        self.submissions.append(
            {"run_id": run_id, "claimed_at": claimed_at, "handle": handle,
             "backend": backend, "phase": phase, "submitted_at": submitted_at,
             "credentials_expire_at": credentials_expire_at}
        )
        row = self._row(run_id)
        if row is None:
            # A hand-built QueuedRun: model it as the claim the test implies.
            row = {
                "run_id": run_id, "process_id": "p", "revision_id": "rev-1",
                "source_id": None, "association_id": None, "input_items": [],
                "deferred_until": None, "is_test": False, "status": "running",
                "started_at": claimed_at, "attempts": 1, "executor_backend": backend,
                "executor_handle": None,
            }
            self.enqueued.append(row)
        if (
            row["status"] != "running"
            or row.get("executor_handle") is not None
            or row.get("executor_backend") != backend
            or (claimed_at is not None and row.get("started_at") != claimed_at)
        ):
            return None
        row.update(
            executor_handle=handle, submitted_at=submitted_at, phase=phase,
            phase_detail=None, credentials_expire_at=credentials_expire_at,
        )
        return SubmissionRecord(cancel_requested=row.get("cancel_requested_at") is not None)

    async def set_phase(
        self, run_id: str, *, handle: str, phase: str, detail: str | None
    ) -> bool:
        row = self._row(run_id)
        if row is None or row["status"] != "running" or row.get("executor_handle") != handle:
            return False
        if (row.get("phase"), row.get("phase_detail")) == (phase, detail):
            return False
        row.update(phase=phase, phase_detail=detail)
        return True

    def _submitted(self, row: dict[str, Any]) -> SubmittedRun:
        return SubmittedRun(
            id=row["run_id"], process_id=row["process_id"], revision_id=row["revision_id"],
            source_id=row["source_id"], association_id=row["association_id"],
            attempts=row.get("attempts", 1), is_test=row["is_test"],
            handle=row["executor_handle"], backend=row["executor_backend"],
            phase=row.get("phase"), submitted_at=row.get("submitted_at"),
            cancel_requested_at=row.get("cancel_requested_at"),
            credentials_expire_at=row.get("credentials_expire_at"),
            runtime=dict(row.get("runtime") or self.runtime),
            input_items=list(row["input_items"]),
        )

    def _is_submitted(self, row: dict[str, Any], backend: str) -> bool:
        return (
            row["status"] == "running"
            and row.get("executor_handle") is not None
            and row.get("executor_backend") == backend
        )

    async def list_submitted_runs(
        self,
        *,
        backend: str,
        limit: int,
        cancel_requested_only: bool = False,
        exclude_cancel_requested: bool = False,
        after_id: str | None = None,
    ) -> list[SubmittedRun]:
        rows = [
            row for row in self.enqueued
            if self._is_submitted(row, backend)
            and (not cancel_requested_only or row.get("cancel_requested_at"))
            and (not exclude_cancel_requested or not row.get("cancel_requested_at"))
            and (after_id is None or row["run_id"] > after_id)
        ]
        rows.sort(key=lambda r: r["run_id"])
        return [self._submitted(row) for row in rows[:limit]]

    async def get_submitted_run(self, run_id: str, *, backend: str) -> SubmittedRun | None:
        row = self._row(run_id)
        return self._submitted(row) if row and self._is_submitted(row, backend) else None

    @asynccontextmanager
    async def collecting(
        self, run_id: str, *, handle: str
    ) -> AsyncIterator[CollectingRun | None]:
        row = self._row(run_id)
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        if (
            row is None or row["status"] != "running"
            or row.get("executor_handle") != handle or lock.locked()
        ):
            yield None  # SKIP LOCKED, or no longer this handle's running row
            return
        async with lock:
            snapshot = dict(row)
            collecting = _FakeCollectingRun(self, row, self._submitted(row))
            try:
                yield collecting
            except BaseException:
                # Roll back: the row as it was, nothing recorded.
                row.clear()
                row.update(snapshot)
                if row not in self.enqueued:
                    self.enqueued.append(row)
                del self.finished[collecting.finished_mark:]
                del self.source_runs[collecting.source_runs_mark:]
                raise

    async def list_output_collections(self, process_id: str) -> tuple[str, ...]:
        return tuple(self.output_collections)

    async def list_source_collections(self, process_id: str) -> tuple[str, ...]:
        return tuple(sorted(set(self.source_collections)))

    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        return self.items.get((collection_id, item_id))

    async def reference_source_hrefs(self, collection_id: str, item_id: str) -> dict[str, str]:
        return dict(self.source_hrefs.get((collection_id, item_id), {}))

    async def reset_stalled_runs(self, older_than: dt.datetime, limit: int) -> int:
        # I9: only unsubmitted rows (plus the scripted count older tests set).
        recovered = 0
        for row in list(self.enqueued):
            started = row.get("started_at")
            if (
                row["status"] == "running" and row.get("executor_handle") is None
                and started is not None and started < older_than and recovered < limit
            ):
                self._requeue_row(
                    row, error="run was stranded by a worker or executor crash",
                    phase=None, phase_detail=None, next_attempt_at=None,
                    restore_attempt=False,
                )
                recovered += 1
        return self.stalled_reset + recovered

    async def run_statuses(self, run_ids: Sequence[str]) -> dict:
        return {}

    async def record_source_run(
        self, source_id: str, *, succeeded: bool, at: dt.datetime
    ) -> None:
        self.source_runs.append((source_id, succeeded))

    async def claim_process_checks(self, limit: int) -> list[ProcessCheckRequest]:
        batch = self.checks[:limit]
        self.checks = self.checks[limit:]
        return batch

    async def attach_check_run(self, check_id: str, run_id: str) -> None:
        return None

    async def finish_check(
        self, check_id: str, *, status: str, result: dict[str, Any] | None
    ) -> None:
        return None


class _FakeCollectingRun(CollectingRun):
    def __init__(self, repo: FakeProcessRepo, row: dict[str, Any], run: SubmittedRun) -> None:
        self._repo = repo
        self._row = row
        self.run = run
        self._done = False
        self.finished_mark = len(repo.finished)
        self.source_runs_mark = len(repo.source_runs)

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
        written = status
        if self._row.get("cancel_requested_at"):
            written = "cancelled"
            error = CANCELLED_ERROR + (f"; {error}" if error else "")
        self._row.update(status=written, phase=None, phase_detail=None, error=error,
                         next_attempt_at=None)
        if log_ref is not None:
            self._row["log_ref"] = log_ref
        self._repo.finished.append(
            {"run_id": self.run.id, "status": written, "error": error, "log_ref": log_ref,
             "next_attempt_at": next_attempt_at}
        )
        if self.run.source_id and written in ("succeeded", "dead"):
            self._repo.source_runs.append((self.run.source_id, written == "succeeded"))
        return written

    async def requeue(
        self,
        *,
        error: str | None,
        phase: str | None,
        phase_detail: str | None,
        next_attempt_at: dt.datetime,
    ) -> str:
        self._once()
        outcome = self._repo._requeue_row(
            self._row, error=error, phase=phase, phase_detail=phase_detail,
            next_attempt_at=next_attempt_at, restore_attempt=True,
        )
        self._repo.finished.append(
            {"run_id": self.run.id, "status": "cancelled" if outcome == "cancelled" else "queued",
             "error": error, "log_ref": None, "next_attempt_at": next_attempt_at}
        )
        return outcome
