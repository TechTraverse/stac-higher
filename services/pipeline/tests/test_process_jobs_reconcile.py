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
    monkeypatch.setattr(jobs, "build_platform_client", lambda _settings: object())
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


# -- K-5: the factory on the run path (plan addendum A4, A5, A17) --------------


class _KubeLike:
    """Stands in for a backend that owns admission (Kueue)."""

    name = "kubernetes"
    enforces_local_capacity = False


class _DockerLike:
    name = "docker"
    enforces_local_capacity = True


@pytest.fixture
def claims(wired, monkeypatch):
    queue, repo, _stub, _results = wired
    seen: list[dict] = []
    real_due, real_one = repo.claim_due_runs, repo.claim_run

    async def claim_due_runs(now, limit, **kwargs):
        seen.append(kwargs)
        return await real_due(now, limit, **kwargs)

    async def claim_run(run_id, now, **kwargs):
        seen.append(kwargs)
        return await real_one(run_id, now, **kwargs)

    monkeypatch.setattr(repo, "claim_due_runs", claim_due_runs)
    monkeypatch.setattr(repo, "claim_run", claim_run)
    return queue, seen


def _profiles():
    from pipeline.process.hardware import load_hardware_profiles

    return load_hardware_profiles()


@pytest.mark.parametrize(
    ("executor", "local"), [(_KubeLike(), False), (_DockerLike(), True)]
)
async def test_the_claim_stamps_the_executors_backend_and_gates_local_capacity(
    claims, monkeypatch, executor, local
):
    queue, seen = claims
    monkeypatch.setattr(jobs, "build_executor", lambda _settings: executor)
    monkeypatch.setattr(jobs, "load_run_profiles", lambda _settings: _profiles())
    await queue.periodic[jobs.JOB_RUN_TICK].func(0)
    await queue.tasks[jobs.JOB_RUN_NOW](run_id="nope")
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs["backend"] == executor.name
        assert (kwargs["capacities"] is not None) is local
        assert kwargs["extract_capacity"] == Settings().process_extract_max_running


async def test_an_unbuildable_executor_skips_the_tick(claims, monkeypatch, caplog):
    from pipeline.process.executor import ExecutorUnavailable

    queue, seen = claims

    def unbuildable(_settings):
        raise ExecutorUnavailable("PROCESS_K8S_API_URL is unset")

    monkeypatch.setattr(jobs, "build_executor", unbuildable)
    await queue.periodic[jobs.JOB_RUN_TICK].func(0)
    await queue.tasks[jobs.JOB_RUN_NOW](run_id="nope")
    await queue.periodic[jobs.JOB_REAP].func(0)
    assert seen == []
    assert [r.getMessage() for r in caplog.records].count(
        "process job skipped: the executor is unavailable"
    ) == 3


async def test_the_reaper_gets_the_queue_wait_ceiling(wired, monkeypatch):
    from pipeline.process.reaper import reap_max_age_seconds

    queue, *_ = wired
    seen: dict = {}

    async def fake_tick(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(jobs, "process_reap_tick", fake_tick)
    monkeypatch.setattr(jobs, "load_run_profiles", lambda _settings: _profiles())
    monkeypatch.setattr(jobs, "PgImagesRepo", lambda _url: type("R", (), {"scan_statuses": 1})())
    await queue.periodic[jobs.JOB_REAP].func(0)
    assert seen["max_age_seconds"] == reap_max_age_seconds(_profiles())
