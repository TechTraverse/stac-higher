"""K-3: per-profile capacity on the Docker host (process-compute spec §8).

Admission is decided IN THE CLAIM (K-3 review): a process or test run whose
profile already has ``capacity`` runs admitted is not claimed at all — it
stays ``queued`` as ``pending_capacity``, claimable again in 15 s, with no
attempt spent. Extractor runs are exempt (K-3 ruling: capacity must not
throttle ingest). These tests drive the fake's claim, which mirrors the Pg
admission; the Pg statements, the advisory lock and the tick's paging are
pinned by tests/test_integration_process_capacity.py against Postgres.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from _process_fake import FakeProcessRepo
from pipeline.config import Settings
from pipeline.process.hardware import docker_capacities, parse_hardware_profiles
from pipeline.process.ledger import CAPACITY_RETRY_SECONDS, PHASE_PENDING_CAPACITY
from pipeline.process.memory_executor import MemoryExecutor, exited
from pipeline.process.repo import QueuedRun
from pipeline.process.runner import run_one
from test_process_triggers import FakeStore, FakeSts

NOW = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)


def profiles(capacity, *, large_capacity=1, docker=None):
    standard_docker = (
        docker if docker is not None else {"device_requests": [], "capacity": capacity}
    )
    return parse_hardware_profiles(
        {
            "version": 1,
            "profiles": [
                {
                    "id": "standard", "label": "S", "description": "", "tier": "cpu",
                    "accelerator": None, "cpu": {"min": 0.25, "max": 4, "default": 1},
                    "memory_mb": {"min": 128, "max": 16384, "default": 512}, "gpu_count": None,
                    "max_queue_wait_seconds": 60, "image": None,
                    "backend": {"docker": standard_docker},
                },
                {
                    "id": "cpu-large", "label": "L", "description": "", "tier": "cpu-large",
                    "accelerator": None, "cpu": {"min": 2, "max": 8, "default": 4},
                    "memory_mb": {"min": 4096, "max": 32768, "default": 8192}, "gpu_count": None,
                    "max_queue_wait_seconds": 60, "image": None,
                    "backend": {"docker": {"capacity": large_capacity}},
                },
            ],
        }
    )


def queued(run_id="run-1", runtime=None):
    return QueuedRun(
        id=run_id, process_id="proc-1", revision_id="rev-1", source_id=None, attempts=1,
        input_items=[], runtime=runtime or {"kind": "inline_python"}, code="pass", env=[],
    )


async def _run(repo, executor, *, capacity, run=None, profile_set=None):
    return await run_one(
        run or queued(), repo=repo, executor=executor, settings=Settings.from_env({}),
        storage_client=FakeStore(), resolve_secret=lambda ref: "x", now=NOW,
        sts_client=FakeSts(), profiles=profile_set or profiles(capacity),
    )


async def _enqueue(repo, *, association_id=None):
    return await repo.enqueue_run(
        process_id="proc-1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None, is_test=True, association_id=association_id,
    )


@pytest.mark.asyncio
async def test_a_full_profile_is_not_claimed_and_no_attempt_is_spent():
    repo = FakeProcessRepo(admitted_elsewhere={"standard": 2})
    caps = docker_capacities(profiles(2))
    run_id = await _enqueue(repo)
    assert await repo.claim_run(run_id, NOW, capacities=caps) is None
    row = repo.enqueued[0]
    assert row["status"] == "queued"
    assert row["phase"] == PHASE_PENDING_CAPACITY
    assert row["phase_detail"] == "profile 'standard' is at its capacity of 2 on this host"
    assert row["next_attempt_at"] == NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
    assert row.get("executor_backend") is None


@pytest.mark.asyncio
async def test_a_waiting_run_is_claimable_once_its_retry_is_due_and_room_frees():
    repo = FakeProcessRepo(admitted_elsewhere={"standard": 2})
    caps = docker_capacities(profiles(2))
    run_id = await _enqueue(repo)
    assert await repo.claim_run(run_id, NOW, capacities=caps) is None
    repo.admitted_elsewhere["standard"] = 1
    assert await repo.claim_run(run_id, NOW + dt.timedelta(seconds=1), capacities=caps) is None
    later = NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
    claimed = await repo.claim_run(run_id, later, capacities=caps)
    assert claimed is not None
    row = repo.enqueued[0]
    assert (row["status"], row["phase"], row["executor_backend"]) == (
        "running", "starting", "docker",
    )


@pytest.mark.asyncio
async def test_admitted_runs_count_until_they_finish():
    repo = FakeProcessRepo()
    caps = docker_capacities(profiles(1))
    first = await _enqueue(repo)
    assert await repo.claim_run(first, NOW, capacities=caps) is not None
    second = await _enqueue(repo)
    assert await repo.claim_run(second, NOW, capacities=caps) is None
    await repo.finish_run(first, status="succeeded", error=None, log_ref=None,
                          next_attempt_at=None)
    later = NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
    assert await repo.claim_run(second, later, capacities=caps) is not None


@pytest.mark.asyncio
async def test_an_extractor_on_a_bounded_non_default_profile_is_admitted_like_a_process_run():
    """K-3 ruling, narrowed: the exemption is for the DEFAULT profile only.
    An extractor whose revision names `cpu-large` counts against it and waits
    when it is full."""
    repo = FakeProcessRepo(
        runtime={"kind": "inline_python", "memory_mb": 8192,
                 "hardware": {"profile": "cpu-large", "cpu": 4, "gpu_count": 0}},
    )
    caps = docker_capacities(profiles(2, large_capacity=1))
    first = await _enqueue(repo, association_id="assoc-1")
    assert await repo.claim_run(first, NOW, capacities=caps) is not None
    assert repo.admitted("cpu-large") == 1
    second = await _enqueue(repo, association_id="assoc-2")
    assert await repo.claim_run(second, NOW, capacities=caps) is None
    assert repo.enqueued[1]["phase"] == PHASE_PENDING_CAPACITY


@pytest.mark.asyncio
async def test_extractor_runs_on_the_default_profile_are_exempt_and_never_count():
    """K-3 ruling: an extractor run on the default profile is ingest work;
    a full `standard` must not hold it back, and it must not fill `standard`
    for process runs either."""
    repo = FakeProcessRepo(admitted_elsewhere={"standard": 2})
    caps = docker_capacities(profiles(2))
    extract = await _enqueue(repo, association_id="assoc-1")
    assert await repo.claim_run(extract, NOW, capacities=caps) is not None
    repo.admitted_elsewhere["standard"] = 1
    transform = await _enqueue(repo)
    assert await repo.claim_run(transform, NOW, capacities=caps) is not None


@pytest.mark.asyncio
async def test_an_unbounded_profile_and_no_capacities_always_admit():
    repo = FakeProcessRepo(admitted_elsewhere={"standard": 99})
    assert await repo.claim_run(
        await _enqueue(repo), NOW, capacities=docker_capacities(profiles(None))
    ) is not None
    assert await repo.claim_run(await _enqueue(repo), NOW, capacities=None) is not None


@pytest.mark.asyncio
async def test_run_one_no_longer_counts_or_releases():
    """Admission is the claim's job: run_one launches whatever it is handed."""
    repo = FakeProcessRepo()
    executor = MemoryExecutor(default_states=[exited(0)])
    result = await _run(repo, executor, capacity=1)
    assert result.status == "succeeded"
    assert len(executor.submitted) == 1


@pytest.mark.asyncio
async def test_only_an_extractor_on_the_default_profile_launches_cpu_exempt():
    repo = FakeProcessRepo()
    executor = MemoryExecutor(default_states=[exited(0)])
    run = QueuedRun(
        id="run-x", process_id="proc-1", revision_id="rev-1", source_id=None, attempts=1,
        input_items=[], runtime={"kind": "inline_python"}, code="pass", env=[],
        association_id="assoc-1",
    )
    await _run(repo, executor, capacity=1, run=run)
    assert executor.submitted[0].cpu_exempt is True
    await _run(repo, executor, capacity=1)  # a process run on standard
    assert executor.submitted[1].cpu_exempt is False
    large = QueuedRun(
        id="run-y", process_id="proc-1", revision_id="rev-1", source_id=None, attempts=1,
        input_items=[], code="pass", env=[], association_id="assoc-1",
        runtime={"kind": "inline_python", "memory_mb": 8192,
                 "hardware": {"profile": "cpu-large", "cpu": 4, "gpu_count": 0}},
    )
    await _run(repo, executor, capacity=1, run=large)  # an extractor on cpu-large
    assert executor.submitted[2].cpu_exempt is False
    assert executor.submitted[2].cpu == 4


@pytest.mark.asyncio
async def test_a_malformed_docker_block_requeues_as_infrastructure(tmp_path):
    """K-3 review: an operator typo in the profile file (`"capacity": "2"`)
    is the deployment's fault, like an unreadable file: back to `queued`
    without spending an attempt, never `dead` for every run on the profile
    (for an extractor, `on_dead` would fail the ingest batch too)."""
    bad = {
        "version": 1,
        "profiles": [
            {
                "id": "standard", "label": "S", "description": "", "tier": "cpu",
                "accelerator": None, "cpu": {"min": 0.25, "max": 4, "default": 1},
                "memory_mb": {"min": 128, "max": 16384, "default": 512}, "gpu_count": None,
                "max_queue_wait_seconds": 60, "image": None,
                "backend": {"docker": {"capacity": "2"}},
            }
        ],
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(bad))
    repo = FakeProcessRepo()
    executor = MemoryExecutor(default_states=[exited(0)])
    result = await run_one(
        queued(), repo=repo, executor=executor,
        settings=Settings.from_env({"PROCESS_HARDWARE_PROFILES_FILE": str(path)}),
        storage_client=FakeStore(), resolve_secret=lambda ref: "x", now=NOW,
        sts_client=FakeSts(),
    )
    assert result.status == "queued"
    assert executor.submitted == []
    assert repo.finished[-1]["status"] == "queued"
    assert "backend.docker.capacity" in repo.finished[-1]["error"]


def test_release_phase_is_the_fixture_value():
    assert PHASE_PENDING_CAPACITY == "pending_capacity"


@pytest.mark.asyncio
async def test_hardware_unavailable_is_a_dead_run_not_a_requeue():
    """The host cannot provide what the profile promises (no NVIDIA runtime,
    fewer CPUs) — configuration, like an unknown profile: dead, naming it."""
    from pipeline.process.executor import HardwareUnavailable

    message = (
        "profile 'gpu-l4' needs an NVIDIA container runtime this Docker host does not expose"
    )
    repo = FakeProcessRepo()
    executor = MemoryExecutor(submit_error=HardwareUnavailable(message))
    result = await _run(repo, executor, capacity=None)
    assert result.status == "dead"
    assert repo.finished[-1]["status"] == "dead"
    assert repo.finished[-1]["error"] == message
    assert repo.finished[-1]["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_exempt_extractor_runs_are_bounded_by_their_own_pool():
    """K-4 addendum A3: submission no longer holds the worker, so exempt
    extractor runs are counted against PROCESS_EXTRACT_MAX_RUNNING — never
    against the profile, and a full pool never holds back a process run."""
    from pipeline.process.ledger import extract_capacity_detail

    repo = FakeProcessRepo()
    caps = docker_capacities(profiles(1))
    for association in ("a1", "a2"):
        run = await _enqueue(repo, association_id=association)
        assert await repo.claim_run(run, NOW, capacities=caps, extract_capacity=2) is not None
    held = await _enqueue(repo, association_id="a3")
    assert await repo.claim_run(held, NOW, capacities=caps, extract_capacity=2) is None
    row = repo._row(held)
    assert (row["status"], row["phase"]) == ("queued", PHASE_PENDING_CAPACITY)
    assert row["phase_detail"] == extract_capacity_detail(2)
    assert "PROCESS_EXTRACT_MAX_RUNNING" in row["phase_detail"]
    # The profile (capacity 1) is untouched by the two exempt runs.
    transform = await _enqueue(repo)
    assert await repo.claim_run(transform, NOW, capacities=caps, extract_capacity=2) is not None


def test_extract_max_running_setting_is_strict():
    assert Settings.from_env(env={}).process_extract_max_running == 4
    blank = Settings.from_env(env={"PROCESS_EXTRACT_MAX_RUNNING": ""})
    assert blank.process_extract_max_running == 4
    assert Settings.from_env(
        env={"PROCESS_EXTRACT_MAX_RUNNING": "2"}
    ).process_extract_max_running == 2
    for bad in ("0", "-1", "2.5", "many"):
        with pytest.raises(ValueError, match="PROCESS_EXTRACT_MAX_RUNNING"):
            Settings.from_env(env={"PROCESS_EXTRACT_MAX_RUNNING": bad})
