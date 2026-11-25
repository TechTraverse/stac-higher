"""K-4 wiring in jobs/process.py: every submitted run is reconciled once
right after submission (addendum A6), and the run tick sweeps submitted runs
after claiming (the backstop)."""

from __future__ import annotations

import datetime as dt

import pytest

from _process_fake import FakeProcessRepo
from pipeline.config import Settings
from pipeline.jobs import process as jobs
from pipeline.process.repo import QueuedRun
from pipeline.process.runner import RunResult
from pipeline.queue.memory import InMemoryQueue


class StubReconciler:
    def __init__(self):
        self.reconciled: list[str] = []
        self.sweeps: list[int] = []
        self.storage_client = object()

    async def reconcile_id(self, run_id):
        self.reconciled.append(run_id)
        return "phase"

    async def sweep(self, *, limit, cancel_requested_only=False):
        self.sweeps.append(limit)
        return 0


@pytest.fixture
def wired(monkeypatch):
    repo = FakeProcessRepo()
    stub = StubReconciler()
    results: dict[str, str] = {}

    async def fake_run_one(run, **_kwargs):
        return RunResult(run.id, results.get(run.id, "running"))

    monkeypatch.setattr(jobs, "PgProcessRepo", lambda _url: repo)
    monkeypatch.setattr(jobs, "build_reconciler", lambda *_a, **_k: stub)
    monkeypatch.setattr(jobs, "run_one", fake_run_one)
    monkeypatch.setattr(jobs, "load_run_profiles", lambda _settings: None)
    queue = InMemoryQueue()
    jobs.register(queue, Settings.from_env({}))
    return queue, repo, stub, results


def _queued(run_id):
    return QueuedRun(id=run_id, process_id="p", revision_id="r", source_id=None, attempts=1)


async def test_each_submitted_run_is_reconciled_once_right_away(wired):
    queue, repo, stub, results = wired
    repo.due_runs = [_queued("a"), _queued("b"), _queued("c")]
    results["b"] = "dead"  # a pre-submit verdict: nothing to reconcile
    await queue.periodic[jobs.JOB_RUN_TICK].func(int(dt.datetime.now().timestamp()))
    assert stub.reconciled == ["a", "c"]
    assert stub.sweeps == [jobs.RECONCILE_BATCH]


async def test_the_tick_sweeps_even_when_nothing_is_due(wired):
    queue, _repo, stub, _results = wired
    await queue.periodic[jobs.JOB_RUN_TICK].func(0)
    assert stub.reconciled == [] and stub.sweeps == [jobs.RECONCILE_BATCH]
