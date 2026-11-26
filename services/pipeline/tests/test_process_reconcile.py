"""K-4 Reconciler: the decision table (plan Task 5 Step 3) against the fake
ledger and the scripted MemoryExecutor. The row lock itself is pinned against
Postgres in tests/test_integration_process_reconcile.py."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from _process_fake import FakeProcessRepo
from pipeline.config import Settings
from pipeline.process.executor import RESYNC, ExecutorUnavailable, RunEvent, RunState
from pipeline.process.memory_executor import MemoryExecutor, exited
from pipeline.process.reconcile import CREDENTIAL_REQUEUE_DETAIL, Reconciler
from pipeline.process.repo import SubmittedRun
from pipeline.storage.keys import run_log_key

NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.UTC)


class Store:
    def __init__(self, fail_after_put: bool = False):
        self.puts: list[str] = []
        self.fail_after_put = fail_after_put

    def put_object(self, **kwargs):
        self.puts.append(kwargs["Key"])


class Harness:
    def __init__(self, executor: MemoryExecutor | None = None, repo=None):
        self.repo = repo or FakeProcessRepo(runtime={"kind": "inline_python",
                                                     "retry": {"max_attempts": 3}})
        self.executor = executor or MemoryExecutor()
        self.store = Store()
        self.finalized: list[tuple[str, str]] = []
        self.run_now: list[str] = []
        self.dead: list[tuple[str, str]] = []

    def reconciler(self, **overrides) -> Reconciler:
        async def finalize(run_id, process_id):
            self.finalized.append((run_id, process_id))

        async def run_now(run_id):
            self.run_now.append(run_id)

        async def on_dead(run, error):
            self.dead.append((run.id, error))

        kwargs = dict(
            repo=self.repo, executor=self.executor, settings=Settings.from_env({}),
            storage_client=self.store, enqueue_finalize=finalize, enqueue_run_now=run_now,
            on_dead=on_dead, clock=lambda: NOW,
        )
        kwargs.update(overrides)
        return Reconciler(**kwargs)

    async def submitted(self, *, source_id=None, association_id=None, attempts=0,
                        expires=None, cancel=False, submitted_ago=30) -> SubmittedRun:
        run_id = await self.repo.enqueue_run(
            process_id="p1", revision_id="rev-1", source_id=source_id,
            input_items=[{"item_id": "a"}], deferred_until=None, is_test=source_id is None
            and association_id is None, association_id=association_id,
        )
        self.repo._row(run_id)["attempts"] = attempts
        run = await self.repo.claim_run(run_id, NOW, backend="memory")
        handle = MemoryExecutor.handle_for(run_id).id
        await self.repo.record_submission(
            run_id, claimed_at=run.claimed_at, handle=handle, backend="memory",
            phase="starting", submitted_at=NOW - dt.timedelta(seconds=submitted_ago),
            credentials_expire_at=expires,
        )
        if cancel:
            self.repo._row(run_id)["cancel_requested_at"] = NOW
        return await self.repo.get_submitted_run(run_id, backend="memory")

    def row(self, run_id):
        return self.repo._row(run_id)


# -- row 2: still running -----------------------------------------------------


async def test_a_running_report_updates_the_phase_once():
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    run = await h.submitted()
    rec = h.reconciler()
    assert await rec.reconcile(run) == "phase"
    assert await rec.reconcile(run) == "unchanged"
    assert h.row(run.id)["phase"] == "running"
    assert h.executor.reaped == [] and h.finalized == []


async def test_an_unreachable_backend_changes_nothing():
    h = Harness(MemoryExecutor(status_error=ExecutorUnavailable("down")))
    run = await h.submitted()
    assert await h.reconciler().reconcile(run) == "unavailable"
    assert h.row(run.id)["status"] == "running"


# -- row 4: exited -------------------------------------------------------------


async def test_an_exit_0_is_collected_published_and_reaped():
    h = Harness(MemoryExecutor(default_states=[exited(0)], log_output=b"ok"))
    run = await h.submitted(source_id="s1")
    assert await h.reconciler().reconcile(run) == "succeeded"
    row = h.row(run.id)
    assert row["status"] == "succeeded" and row["log_ref"] == run_log_key("p1", run.id)
    assert h.store.puts == [row["log_ref"]]
    assert h.executor.reaped == [run.handle]
    assert h.finalized == [(run.id, "p1")]
    assert h.repo.source_runs == [("s1", True)]


async def test_a_failing_exit_within_budget_is_failed_for_retry():
    h = Harness(MemoryExecutor(default_states=[exited(2)]))
    run = await h.submitted(attempts=0)  # the claim makes it 1 of 3
    assert await h.reconciler().reconcile(run) == "failed"
    assert h.repo.finished[-1]["next_attempt_at"] == NOW + dt.timedelta(seconds=60)
    assert h.repo.finished[-1]["error"] == "run exited 2"
    assert h.finalized == [] and h.executor.reaped == [run.handle]


async def test_a_failing_exit_on_the_last_attempt_is_dead_and_fails_an_extract_batch():
    h = Harness(MemoryExecutor(default_states=[exited(1, timed_out=True)]))
    run = await h.submitted(association_id="assoc-1", attempts=2)
    assert await h.reconciler().reconcile(run) == "dead"
    assert h.dead == [(run.id, "run exceeded its timeout and was killed")]
    assert h.finalized == []


# -- row 1: cancel ------------------------------------------------------------


async def test_cancel_wins_over_a_concurrent_exit():
    finished = NOW - dt.timedelta(seconds=1)  # exited just before the click
    h = Harness(MemoryExecutor(default_states=[exited(0, finished_at=finished)],
                               log_output=b"partial"))
    run = await h.submitted(cancel=True)
    assert await h.reconciler().reconcile(run) == "cancelled"
    row = h.row(run.id)
    assert row["status"] == "cancelled"
    assert row["error"] == "cancelled by an operator; the run had already exited 0"
    assert h.executor.cancelled == [run.handle] and h.executor.reaped == [run.handle]
    assert row["log_ref"] is not None  # the partial log is kept
    assert h.finalized == []


async def test_cancelling_a_running_run_stops_it_and_keeps_its_log():
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    run = await h.submitted(cancel=True)
    assert await h.reconciler().reconcile(run) == "cancelled"
    assert h.row(run.id)["error"] == "cancelled by an operator"
    assert h.executor.cancelled == [run.handle]


async def test_a_cancel_the_backend_cannot_deliver_is_retried_later():
    h = Harness(MemoryExecutor(cancel_error=ExecutorUnavailable("down")))
    run = await h.submitted(cancel=True)
    assert await h.reconciler().reconcile(run) == "unavailable"
    assert h.row(run.id)["status"] == "running" and h.executor.reaped == []


# -- row 3: credentials --------------------------------------------------------


async def test_a_pending_run_whose_credentials_would_expire_is_requeued():
    # timeout 900 + grace 300 = 1200 s needed; 1000 s left.
    h = Harness(MemoryExecutor(default_states=[RunState("pending_capacity", detail="queued")]))
    run = await h.submitted(attempts=1, expires=NOW + dt.timedelta(seconds=1000),
                            submitted_ago=120)
    assert await h.reconciler().reconcile(run) == "requeued"
    row = h.row(run.id)
    assert (row["status"], row["phase"], row["phase_detail"]) == (
        "queued", "pending_capacity", CREDENTIAL_REQUEUE_DETAIL
    )
    assert row["attempts"] == 1  # the claim's attempt is given back
    assert row["next_attempt_at"] == NOW and row["executor_handle"] is None
    assert h.executor.cancelled == [run.handle] and h.executor.reaped == [run.handle]
    assert h.run_now == [run.id]


async def test_a_pending_run_with_enough_credential_time_just_waits():
    h = Harness(MemoryExecutor(default_states=[RunState("pending_capacity", detail="q")]))
    run = await h.submitted(expires=NOW + dt.timedelta(seconds=1201))
    assert await h.reconciler().reconcile(run) == "phase"
    assert h.row(run.id)["phase_detail"] == "q" and h.executor.cancelled == []


async def test_a_run_whose_profile_promises_no_wait_is_not_requeued_at_once():
    """K-5 review C2: max_queue_wait 0 and timeout + grace >= 900 mint
    exactly timeout + grace, so a just-submitted run already has less than
    that left. Requeueing it then would re-mint and resubmit forever."""
    h = Harness(MemoryExecutor(default_states=[RunState("pending_capacity", detail="q")]))
    # submitted 30 s ago with timeout + grace (1200 s) of credentials
    run = await h.submitted(expires=NOW + dt.timedelta(seconds=1170))
    assert await h.reconciler().reconcile(run) == "phase"
    assert h.executor.cancelled == []
    # once it has waited the minimum, it is requeued
    old = await h.submitted(expires=NOW + dt.timedelta(seconds=1139), submitted_ago=61)
    assert await h.reconciler().reconcile(old) == "requeued"


async def test_the_requeue_waits_out_the_promised_queue_wait():
    """A profile promising 600 s of wait minted 600 s more: no requeue
    before the run has waited that long."""
    h = Harness(MemoryExecutor(default_states=[RunState("pending_capacity")]))
    # minted at submission for 600 + 1200 = 1800 s; 300 s in
    run = await h.submitted(expires=NOW + dt.timedelta(seconds=1500), submitted_ago=300)
    assert await h.reconciler().reconcile(run) == "phase"
    later = await h.submitted(expires=NOW + dt.timedelta(seconds=1190), submitted_ago=610)
    assert await h.reconciler().reconcile(later) == "requeued"


# -- row 5: lost ---------------------------------------------------------------


async def test_a_lost_run_is_requeued_with_the_backends_reason():
    h = Harness(MemoryExecutor(default_states=[RunState("lost", detail="job deleted")]))
    run = await h.submitted(attempts=1)
    assert await h.reconciler().reconcile(run) == "requeued"
    row = h.row(run.id)
    assert (row["status"], row["error"], row["attempts"]) == ("queued", "job deleted", 1)
    assert row["next_attempt_at"] == NOW + dt.timedelta(seconds=60)


# -- exactly once --------------------------------------------------------------


class _CrashingStore(Store):
    def put_object(self, **kwargs):
        super().put_object(**kwargs)
        if len(self.puts) == 1:
            raise SystemExit  # not an Exception: store_log cannot swallow it


async def test_a_restart_mid_collection_still_finalizes_once():
    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    h.store = _CrashingStore()
    run = await h.submitted(source_id="s1")
    with pytest.raises(SystemExit):
        await h.reconciler().reconcile(run)
    assert h.row(run.id)["status"] == "running"
    assert h.executor.reaped == [] and h.finalized == [] and h.repo.source_runs == []
    assert await h.reconciler().reconcile(run) == "succeeded"
    assert len(h.store.puts) == 2 and len(set(h.store.puts)) == 1  # same key twice
    assert h.executor.reaped == [run.handle]
    assert h.finalized == [(run.id, "p1")] and h.repo.source_runs == [("s1", True)]


async def test_the_watcher_and_the_tick_racing_collect_once():
    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    run = await h.submitted()
    rec = h.reconciler()
    outcomes = await asyncio.gather(rec.reconcile(run), rec.reconcile(run))
    assert sorted(outcomes) == ["skipped", "succeeded"]
    assert h.finalized == [(run.id, "p1")] and h.executor.reaped == [run.handle]


async def test_a_collected_run_is_no_longer_a_submitted_run():
    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    run = await h.submitted()
    rec = h.reconciler()
    assert await rec.reconcile_id(run.id) == "succeeded"
    assert await rec.reconcile_id(run.id) is None


# -- routing -------------------------------------------------------------------


async def test_a_row_of_another_backend_is_skipped():
    h = Harness()
    run = await h.submitted()
    h.row(run.id)["executor_backend"] = "kubernetes"
    assert await h.reconciler().reconcile_id(run.id) is None
    assert h.executor.status_calls == []


async def test_an_image_scan_event_is_ignored():
    h = Harness()
    run = await h.submitted()
    await h.reconciler().on_event(RunEvent(run_id=run.id, kind="image_scan"))
    assert h.executor.status_calls == []
    await h.reconciler().on_event(RunEvent(run_id=run.id, handle_id=run.handle))
    assert h.executor.status_calls == [run.handle]


async def test_resync_sweeps_every_submitted_run_cancel_requests_first():
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    first = await h.submitted()
    second = await h.submitted(cancel=True)
    await h.reconciler().on_event(RESYNC)
    assert h.executor.status_calls[0] == second.handle
    assert first.handle in h.executor.status_calls


async def test_one_failing_row_does_not_stop_the_sweep(monkeypatch):
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    bad = await h.submitted()
    good = await h.submitted()
    rec = h.reconciler()
    original = rec.reconcile

    async def flaky(run):
        if run.id == bad.id:
            raise RuntimeError("boom")
        return await original(run)

    monkeypatch.setattr(rec, "reconcile", flaky)
    assert await rec.sweep(limit=10) == 2
    assert h.row(good.id)["phase"] == "running"


# -- Task 9: a freed slot wakes a waiter ---------------------------------------


async def test_a_finished_run_wakes_one_capacity_waiter_on_its_profile():
    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    run = await h.submitted()
    waiting = []
    for _ in range(2):
        run_id = await h.repo.enqueue_run(
            process_id="p1", revision_id="rev-1", source_id=None, input_items=[],
            deferred_until=None, is_test=True,
        )
        h.row(run_id).update(phase="pending_capacity",
                             next_attempt_at=NOW + dt.timedelta(seconds=15))
        waiting.append(run_id)
    assert await h.reconciler().reconcile(run) == "succeeded"
    assert h.run_now == [waiting[0]]  # one slot, the oldest waiter
    assert h.row(waiting[0])["next_attempt_at"] is None
    assert h.row(waiting[1])["next_attempt_at"] is not None


async def test_an_exempt_extractor_wakes_only_the_exempt_pool():
    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    run = await h.submitted(association_id="assoc-1")
    process_waiter = await h.repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None, is_test=True,
    )
    extract_waiter = await h.repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None, association_id="assoc-2",
    )
    for run_id in (process_waiter, extract_waiter):
        h.row(run_id).update(phase="pending_capacity", next_attempt_at=NOW)
    await h.reconciler().reconcile(run)
    assert h.run_now == [extract_waiter]


async def test_a_still_running_run_wakes_nobody():
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    run = await h.submitted()
    waiter = await h.repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None, is_test=True,
    )
    h.row(waiter).update(phase="pending_capacity", next_attempt_at=NOW)
    await h.reconciler().reconcile(run)
    assert h.run_now == []


# -- review round 1 --------------------------------------------------------------


async def test_a_sweep_visits_every_submitted_run_past_one_page():
    """P1: more submitted runs than one page are all reconciled."""
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    runs = [await h.submitted() for _ in range(5)]
    cancelled = await h.submitted(cancel=True)
    swept = await h.reconciler().sweep(limit=2)
    assert swept == 6
    assert {r.handle for r in runs} <= set(h.executor.status_calls)
    assert h.executor.cancelled == [cancelled.handle]


async def test_a_sweep_stops_at_its_safety_cap(caplog, monkeypatch):
    import pipeline.process.reconcile as reconcile_module

    monkeypatch.setattr(reconcile_module, "SWEEP_MAX_ROWS", 3)
    h = Harness(MemoryExecutor(default_states=[RunState("running")]))
    for _ in range(5):
        await h.submitted()
    assert await h.reconciler().sweep(limit=2) == 3
    assert any(r.getMessage() == "run sweep stopped at its safety cap" for r in caplog.records)


async def test_a_stop_we_caused_is_not_reported_as_an_earlier_exit():
    """P2: the cancel's own stop (or the on-read deadline kill) exits the run
    AFTER the request — no "had already exited" suffix."""
    after = NOW + dt.timedelta(seconds=5)
    h = Harness(MemoryExecutor(default_states=[exited(143, finished_at=after)]))
    run = await h.submitted(cancel=True)
    assert await h.reconciler().reconcile(run) == "cancelled"
    assert h.row(run.id)["error"] == "cancelled by an operator"


async def test_an_exit_before_the_request_is_reported():
    before = NOW - dt.timedelta(seconds=5)
    h = Harness(MemoryExecutor(default_states=[exited(0, finished_at=before)]))
    run = await h.submitted(cancel=True)
    assert await h.reconciler().reconcile(run) == "cancelled"
    assert h.row(run.id)["error"] == "cancelled by an operator; the run had already exited 0"


async def test_an_exit_with_no_finish_time_gets_no_suffix():
    h = Harness(MemoryExecutor(default_states=[exited(1)]))
    run = await h.submitted(cancel=True)
    await h.reconciler().reconcile(run)
    assert h.row(run.id)["error"] == "cancelled by an operator"


async def test_publish_happens_before_the_reap():
    """P3: the durable verdict's follow-ups first; the reap is best effort."""
    order: list[str] = []
    executor = MemoryExecutor(default_states=[exited(0)])
    original_reap = executor.reap

    def reap(handle):
        order.append("reap")
        original_reap(handle)

    executor.reap = reap
    h = Harness(executor)
    run = await h.submitted()

    async def finalize(run_id, process_id):
        order.append("finalize")

    await h.reconciler(enqueue_finalize=finalize).reconcile(run)
    assert order == ["finalize", "reap"]


async def test_a_credential_requeue_that_merged_wakes_a_waiter():
    """P10: a merged (or cancelled) credential requeue freed a slot nobody
    is resubmitted into."""
    h = Harness(MemoryExecutor(default_states=[RunState("pending_capacity")]))
    run = await h.submitted(source_id="s1", expires=NOW + dt.timedelta(seconds=10),
                            submitted_ago=120)
    await h.repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id="s1", input_items=[],
        deferred_until=None,
    )
    waiter = await h.repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None, is_test=True,
    )
    h.row(waiter).update(phase="pending_capacity", next_attempt_at=NOW)
    assert await h.reconciler().reconcile(run) == "merged"
    assert h.run_now == [waiter]


async def test_requeue_metrics_use_the_ledger_status():
    """P8: one PROCESS_RUNS vocabulary — a requeue or fold is `queued`."""
    from pipeline.metrics import PROCESS_RUNS

    def count(label):
        return PROCESS_RUNS.labels(outcome=label)._value.get()

    queued, requeued = count("queued"), count("requeued")
    h = Harness(MemoryExecutor(default_states=[RunState("lost")]))
    run = await h.submitted()
    assert await h.reconciler().reconcile(run) == "requeued"
    assert count("queued") == queued + 1
    assert count("requeued") == requeued


async def test_the_storage_client_is_built_on_first_use_and_cached():
    """P5: building a Reconciler (at worker start) never builds the storage
    client — a DNS blip at boot must not crash-loop the worker."""
    built: list[int] = []

    def factory():
        built.append(1)
        return Store()

    h = Harness(MemoryExecutor(default_states=[exited(0)]))
    rec = h.reconciler(storage_client=factory)
    assert built == []
    for _ in range(2):
        await rec.reconcile(await h.submitted())
    assert built == [1]
