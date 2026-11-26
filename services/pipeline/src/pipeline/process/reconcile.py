"""The Reconciler: every submitted run's verdict, written once (K-4, spec §5.3).

A submitted run (``process_runs.executor_handle`` set) belongs to its backend
until the Reconciler moves it out of ``running``. Three wake paths call it:
the run watcher (the backend's event stream, ``process/watcher.py``), the
``LISTEN process_run_cancel`` loop (the app's cancel verb) and the one-minute
run tick (the backstop). They may race freely, because of two rules (plan
R1/R2, ADR 0019):

- **Events are wake-ups; ``status()`` is the truth.** An event names a run,
  never a state; every transition re-reads the backend first, so a missed,
  duplicated or reordered event cannot produce a wrong transition.
- **A verdict is written only inside ``repo.collecting(run, handle)``**, a
  transaction holding the row ``FOR UPDATE SKIP LOCKED`` across log capture
  -> log object -> verdict. A second observer skips the locked row or finds
  it no longer ``running``; a crash before commit rolls back and the next
  observer collects again from the same container (the log key is
  deterministic, so the object is overwritten with the same bytes). The
  container is reaped only AFTER commit.

**Cancel wins (R3, I10).** Once ``cancel_requested_at`` is set the run is
stopped and ends ``cancelled``, even if it exited 0 a moment before the stop;
the error records what the run had done.

**Not exactly-once (pre-existing, docs/ISSUES.md):** the ``process_finalize``
enqueue and the extractor ``on_dead`` call happen after the verdict commits;
a crash in that window loses the publish.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pipeline.config import Settings
from pipeline.metrics import PROCESS_RECONCILES, PROCESS_RUN_SECONDS, PROCESS_RUNS
from pipeline.process.config import ProcessConfigError, parse_process_runtime
from pipeline.process.executor import (
    LOST_ERROR,
    Executor,
    ExecutorUnavailable,
    ExitStatus,
    RunEvent,
    RunHandle,
    RunState,
)
from pipeline.process.hardware import DEFAULT_HARDWARE_PROFILE
from pipeline.process.launch import collect_run_log
from pipeline.process.ledger import (
    DEFAULT_MAX_ATTEMPTS_ON_UNREADABLE_RUNTIME,
    DEFAULT_TIMEOUT_ON_UNREADABLE_RUNTIME,
    INFRA_RETRY_SECONDS,
    outcome_transition,
    run_metric_status,
)
from pipeline.process.repo import ProcessRepo, SubmittedRun, capacity_exempt

logger = logging.getLogger(__name__)

#: Rows per page of a sweep (the tick, a RESYNC, the cancel listener); a
#: sweep pages through every submitted run.
RECONCILE_BATCH = 50
#: A sweep's safety cap: far above any real number of in-flight runs, so
#: hitting it means something is wrong (it logs a warning).
SWEEP_MAX_ROWS = 10_000

#: What a run requeued for its credentials' sake shows (spec §5.4).
CREDENTIAL_REQUEUE_DETAIL = (
    "requeued: its storage credentials would expire before the run could finish; "
    "resubmitting with fresh ones"
)

OUTCOMES = (
    "unchanged", "phase", "succeeded", "failed", "dead", "cancelled",
    "requeued", "merged", "skipped", "unavailable",
)

_ACTIVE_PHASES = ("pending_capacity", "starting", "running")


class RunBatch(Protocol):
    """What ``on_dead`` / ``fail_extract_batch`` read off a run."""

    id: str
    input_items: list[dict[str, Any]]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class Reconciler:
    repo: ProcessRepo
    executor: Executor
    settings: Settings
    #: The platform object store for run logs, or a zero-argument factory
    #: that builds it on first use (review P5: the watcher's Reconciler is
    #: built at worker start, where a DNS blip must not crash the process).
    storage_client: Any
    #: (run_id, process_id): publish a succeeded run (ADR 0014).
    enqueue_finalize: Callable[[str, str], Awaitable[None]]
    #: (run_id): ask for an immediate claim (G-3's `process_run_now`).
    enqueue_run_now: Callable[[str], Awaitable[None]]
    #: G-6: a dead extractor run fails its ingest batch.
    on_dead: Callable[[RunBatch, str], Awaitable[None]] | None = None
    clock: Callable[[], dt.datetime] = field(default=_utcnow)

    # -- entry points ---------------------------------------------------------

    async def reconcile_id(self, run_id: str) -> str | None:
        """Reconcile one run by id; None when it is not a submitted run of
        this backend (never submitted, already collected, or another
        backend's — R16)."""
        run = await self.repo.get_submitted_run(run_id, backend=self.executor.name)
        if run is None:
            return None
        return await self.reconcile(run)

    async def sweep(self, *, limit: int = RECONCILE_BATCH,
                    cancel_requested_only: bool = False) -> int:
        """Reconcile EVERY submitted run of this backend, cancel requests
        first, ``limit`` rows per page (keyset on id) until a short page —
        so the tick and a RESYNC never leave a run unvisited (the timeout on
        read, a missed exit, a credential requeue). Stops at
        ``SWEEP_MAX_ROWS`` with a warning. One row's failure never stops the
        rest (the `_execute_claimed` precedent). Returns rows visited."""
        visited = 0
        passes = [True] if cancel_requested_only else [True, False]
        for cancel_requested in passes:
            after: str | None = None
            while True:
                page = await self.repo.list_submitted_runs(
                    backend=self.executor.name, limit=limit,
                    cancel_requested_only=cancel_requested,
                    exclude_cancel_requested=not cancel_requested,
                    after_id=after,
                )
                for run in page:
                    if visited >= SWEEP_MAX_ROWS:
                        logger.warning(
                            "run sweep stopped at its safety cap",
                            extra={"cap": SWEEP_MAX_ROWS, "backend": self.executor.name},
                        )
                        return visited
                    visited += 1
                    try:
                        await self.reconcile(run)
                    except Exception:
                        logger.exception(
                            "process run could not be reconciled",
                            extra={"run_id": run.id, "process_id": run.process_id},
                        )
                if len(page) < limit:
                    break
                after = page[-1].id
        return visited

    async def on_event(self, event: RunEvent) -> None:
        """The watcher's handler: RESYNC sweeps, a scan's event is not ours
        (R11), anything else wakes its run."""
        if event.run_id is None:
            await self.sweep(limit=RECONCILE_BATCH)
            return
        if event.kind != "process":
            return
        await self.reconcile_id(event.run_id)

    async def reconcile(self, run: SubmittedRun) -> str:
        """Apply the decision table (plan Task 5 Step 3) to one run."""
        handle = RunHandle(id=run.handle, backend=run.backend)
        if run.cancel_requested_at is not None:
            outcome = await self._cancel(run, handle)
        else:
            try:
                state = await asyncio.to_thread(self.executor.status, handle)
            except ExecutorUnavailable:
                outcome = "unavailable"
            else:
                outcome = await self._apply(run, handle, state)
        PROCESS_RECONCILES.labels(outcome=outcome).inc()
        return outcome

    # -- the decision table ---------------------------------------------------

    async def _apply(self, run: SubmittedRun, handle: RunHandle, state: RunState) -> str:
        if state.phase in _ACTIVE_PHASES:
            if state.phase == "pending_capacity" and self._credentials_expiring(run):
                return await self._requeue_for_credentials(run, handle)
            changed = await self.repo.set_phase(
                run.id, handle=run.handle, phase=state.phase, detail=state.detail
            )
            return "phase" if changed else "unchanged"
        if state.phase == "exited":
            return await self._collect_exit(run, handle, state.exit or ExitStatus(-1))
        if state.phase == "lost":
            return await self._requeue_lost(run, handle, state)
        logger.warning(
            "executor reported an unknown run phase",
            extra={"run_id": run.id, "phase": state.phase},
        )
        return "unchanged"

    async def _cancel(self, run: SubmittedRun, handle: RunHandle) -> str:
        """Row 1: stop it, keep its log, land `cancelled` (R3)."""
        try:
            before = await asyncio.to_thread(self.executor.status, handle)
        except ExecutorUnavailable:
            before = None
        try:
            await asyncio.to_thread(self.executor.cancel, handle)
        except ExecutorUnavailable:
            return "unavailable"  # it may still be running: do not collect
        # R3: name the exit only when it PREDATES the request. A stop this
        # cancel (or the on-read deadline kill) caused exits after it.
        error = None
        if (
            before is not None and before.phase == "exited" and before.exit is not None
            and before.finished_at is not None and run.cancel_requested_at is not None
            and before.finished_at < run.cancel_requested_at
        ):
            error = f"the run had already exited {before.exit.exit_code}"
        now = self.clock()
        async with self.repo.collecting(run.id, handle=run.handle) as collecting:
            if collecting is None:
                return "skipped"
            log_ref = await self._log(run, handle)
            written = await collecting.finish(
                status="cancelled", error=error, log_ref=log_ref, next_attempt_at=None, at=now
            )
        await self._after_commit(run, handle, written, error)
        return written

    async def _collect_exit(self, run: SubmittedRun, handle: RunHandle, exit: ExitStatus) -> str:
        """Row 4: logs -> verdict under the row lock; reap and publish after."""
        max_attempts, _timeout = self._budget(run)
        now = self.clock()
        async with self.repo.collecting(run.id, handle=run.handle) as collecting:
            if collecting is None:
                return "skipped"
            transition = outcome_transition(
                exit_code=exit.exit_code,
                timed_out=exit.timed_out,
                # The locked row's count: the claim's own attempt included.
                attempts=collecting.run.attempts,
                max_attempts=max_attempts,
                now=now,
                retry_wait_seconds=INFRA_RETRY_SECONDS,
                error=exit.error,
            )
            log_ref = await self._log(run, handle)
            written = await collecting.finish(
                status=transition.status, error=transition.error, log_ref=log_ref,
                next_attempt_at=transition.next_attempt_at, at=now,
            )
        if run.submitted_at is not None:
            PROCESS_RUN_SECONDS.observe(max((now - run.submitted_at).total_seconds(), 0.0))
        logger.info(
            "process run finished",
            extra={"run_id": run.id, "process_id": run.process_id, "status": written,
                   "exit_code": exit.exit_code, "timed_out": exit.timed_out,
                   "log_ref": log_ref},
        )
        await self._after_commit(run, handle, written, transition.error)
        return written

    async def _requeue_lost(self, run: SubmittedRun, handle: RunHandle, state: RunState) -> str:
        """Row 5: the backend affirms the run is gone — requeue, no attempt
        spent (R15); the backend's reason is the error (K-5 adjustment)."""
        now = self.clock()
        error = state.detail or LOST_ERROR
        async with self.repo.collecting(run.id, handle=run.handle) as collecting:
            if collecting is None:
                return "skipped"
            outcome = await collecting.requeue(
                error=error, phase=None, phase_detail=None,
                next_attempt_at=now + dt.timedelta(seconds=INFRA_RETRY_SECONDS),
            )
        logger.warning(
            "process run lost by the executor; requeued without spending an attempt",
            extra={"run_id": run.id, "process_id": run.process_id, "outcome": outcome,
                   "error": error},
        )
        await self._after_commit(run, handle, outcome, error)
        return outcome

    def _credentials_expiring(self, run: SubmittedRun) -> bool:
        """Spec §5.4: a run still pending with less than timeout + grace left
        on its credentials could not finish if it started now."""
        if run.credentials_expire_at is None:
            return False
        _attempts, timeout = self._budget(run)
        needed = dt.timedelta(seconds=timeout + self.settings.process_credential_grace_seconds)
        return run.credentials_expire_at - self.clock() < needed

    async def _requeue_for_credentials(self, run: SubmittedRun, handle: RunHandle) -> str:
        """Row 3: cancel the pending submission, requeue with the attempt
        restored, and ask for an immediate resubmission (fresh credentials).
        Dormant on Docker, which never holds a submitted run (R9)."""
        try:
            await asyncio.to_thread(self.executor.cancel, handle)
        except ExecutorUnavailable:
            return "unavailable"
        async with self.repo.collecting(run.id, handle=run.handle) as collecting:
            if collecting is None:
                return "skipped"
            outcome = await collecting.requeue(
                error=None, phase="pending_capacity", phase_detail=CREDENTIAL_REQUEUE_DETAIL,
                next_attempt_at=self.clock(),
            )
        logger.info(
            "process run requeued before its credentials expire",
            extra={"run_id": run.id, "process_id": run.process_id, "outcome": outcome},
        )
        # A requeued run is resubmitted at once into the slot it freed, so no
        # wake; a fold or a cancel freed the slot for someone else (P10).
        await self._after_commit(run, handle, outcome, None, wake=outcome != "requeued")
        if outcome == "requeued":
            await self.enqueue_run_now(run.id)
        return outcome

    # -- helpers --------------------------------------------------------------

    def _budget(self, run: SubmittedRun) -> tuple[int, int]:
        """(max_attempts, timeout_seconds) from the stored revision. It parsed
        at claim time, so this cannot fail — but a stranded row is worse than
        a conservative default."""
        try:
            runtime = parse_process_runtime(run.runtime)
        except ProcessConfigError:
            logger.warning(
                "submitted run's revision no longer parses; using conservative limits",
                extra={"run_id": run.id, "process_id": run.process_id},
            )
            return DEFAULT_MAX_ATTEMPTS_ON_UNREADABLE_RUNTIME, DEFAULT_TIMEOUT_ON_UNREADABLE_RUNTIME
        return runtime.max_attempts, runtime.timeout_seconds

    def _storage(self) -> Any:
        if callable(self.storage_client) and not hasattr(self.storage_client, "put_object"):
            self.storage_client = self.storage_client()
        return self.storage_client

    async def _log(self, run: SubmittedRun, handle: RunHandle) -> str | None:
        return await asyncio.to_thread(
            collect_run_log, self.executor, self.settings, self._storage(),
            handle=handle, process_id=run.process_id, run_id=run.id,
        )

    async def _wake_waiters(self, run: SubmittedRun) -> None:
        """K-4 Task 9: the run left `running`, so one slot in its admission
        pool is free — ask for the oldest run waiting for it now, not at the
        next tick (I-170). Best effort: the tick still retries waiters."""
        try:
            profile = parse_process_runtime(run.runtime).hardware_profile
        except ProcessConfigError:
            profile = DEFAULT_HARDWARE_PROFILE
        exempt = capacity_exempt(is_extract=run.association_id is not None, profile=profile)
        try:
            woken = await self.repo.wake_capacity_waiters(profile, exempt=exempt, limit=1)
            for run_id in woken:
                await self.enqueue_run_now(run_id)
        except Exception:
            logger.warning(
                "could not wake a run waiting for capacity",
                extra={"run_id": run.id, "profile": profile}, exc_info=True,
            )

    async def _after_commit(
        self, run: SubmittedRun, handle: RunHandle, written: str, error: str | None,
        *, wake: bool = True,
    ) -> None:
        """The verdict is durable: wake a waiter, publish or fail the batch,
        THEN reap (review P3) — the durable follow-ups first, so a slow or
        failing daemon delays only the container's removal, which the M3-W-1
        reaper's ledger rule retries anyway."""
        status = run_metric_status(written)
        if status is not None:
            PROCESS_RUNS.labels(outcome=status).inc()
        if wake:
            await self._wake_waiters(run)
        if written == "succeeded":
            # Only a SUCCESSFUL run publishes (ADR 0014).
            await self.enqueue_finalize(run.id, run.process_id)
        elif written == "dead" and run.association_id is not None and self.on_dead is not None:
            await self.on_dead(run, error or "run died")
        try:
            await asyncio.to_thread(self.executor.reap, handle)
        except Exception:
            # Idempotent by contract; the M3-W-1 reaper's ledger rule retries.
            logger.warning(
                "process run reap failed after its verdict",
                extra={"run_id": run.id}, exc_info=True,
            )
