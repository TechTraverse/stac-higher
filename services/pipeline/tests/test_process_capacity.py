"""K-3: per-profile capacity on the Docker host (process-compute spec §8).

A claimed run whose profile already has ``capacity`` runs ``running`` on this
host, claimed ahead of it, is released back to ``queued`` as
``pending_capacity``, claimable again in 15 s, with no attempt spent and
nothing staged, minted or launched.
"""

from __future__ import annotations

import datetime as dt

import pytest

from _process_fake import FakeProcessRepo
from pipeline.config import Settings
from pipeline.process.executor import ExitStatus
from pipeline.process.hardware import parse_hardware_profiles
from pipeline.process.ledger import CAPACITY_RETRY_SECONDS, PHASE_PENDING_CAPACITY
from pipeline.process.memory_executor import MemoryExecutor
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


@pytest.mark.asyncio
async def test_a_full_profile_releases_the_run_without_spending_an_attempt():
    repo = FakeProcessRepo(running_on_profile={"standard": 2})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=2)
    assert result.status == "queued"
    assert executor.launched == []
    assert repo.released == [
        {
            "run_id": "run-1",
            "next_attempt_at": NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS),
            "detail": "profile 'standard' is at its capacity of 2 on this host",
        }
    ]
    # Not a finish: the row goes back to queued with its attempt restored by
    # release_for_capacity, never through finish_run's requeue.
    assert repo.finished == []
    # Counted against the runs claimed AHEAD of this one (no livelock when two
    # workers claim the last slot at once: the earlier claim proceeds).
    assert repo.counted == [("standard", "run-1")]


@pytest.mark.asyncio
async def test_a_profile_with_room_launches():
    repo = FakeProcessRepo(running_on_profile={"standard": 1})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=2)
    assert result.status == "succeeded"
    assert len(executor.launched) == 1
    assert repo.released == []


@pytest.mark.asyncio
async def test_an_unbounded_profile_never_counts():
    repo = FakeProcessRepo(running_on_profile={"standard": 99})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=None)
    assert result.status == "succeeded"
    assert repo.counted == []  # the repo was not even asked


@pytest.mark.asyncio
async def test_capacity_is_per_profile():
    """A full `standard` does not hold a `cpu-large` run back, and the count
    asked for is the run's own profile."""
    repo = FakeProcessRepo(running_on_profile={"standard": 5, "cpu-large": 0})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    run = queued(
        runtime={
            "kind": "inline_python",
            "memory_mb": 8192,
            "hardware": {"profile": "cpu-large", "cpu": 4, "gpu_count": 0},
        }
    )
    result = await _run(repo, executor, capacity=1, run=run)
    assert result.status == "succeeded"
    assert repo.counted == [("cpu-large", "run-1")]


@pytest.mark.asyncio
async def test_a_malformed_docker_block_is_a_dead_run_naming_the_profile():
    repo = FakeProcessRepo()
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(
        repo, executor, capacity=None, profile_set=profiles(None, docker={"capacity": "two"})
    )
    assert result.status == "dead"
    assert executor.launched == []
    assert "profile 'standard': backend.docker.capacity" in repo.finished[-1]["error"]


@pytest.mark.asyncio
async def test_a_released_run_is_claimable_again_with_its_phase():
    """The fake models the ledger: release puts the row back to queued as
    pending_capacity, and the next claim flips it to starting."""
    repo = FakeProcessRepo(running_on_profile={"standard": 1})
    run_id = await repo.enqueue_run(
        process_id="proc-1", revision_id="rev-1", source_id=None, input_items=[],
        deferred_until=None,
    )
    claimed = await repo.claim_run(run_id, NOW)
    assert claimed is not None and repo.enqueued[0]["phase"] == "starting"
    await _run(repo, MemoryExecutor(), capacity=1, run=claimed)
    assert repo.enqueued[0]["status"] == "queued"
    assert repo.enqueued[0]["phase"] == PHASE_PENDING_CAPACITY
    # Not claimable before next_attempt_at, claimable after.
    assert await repo.claim_run(run_id, NOW) is None
    later = NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS)
    again = await repo.claim_run(run_id, later)
    assert again is not None and repo.enqueued[0]["phase"] == "starting"


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
    executor = MemoryExecutor(launch_error=HardwareUnavailable(message))
    result = await _run(repo, executor, capacity=None)
    assert result.status == "dead"
    assert repo.finished[-1]["status"] == "dead"
    assert repo.finished[-1]["error"] == message
    assert repo.finished[-1]["next_attempt_at"] is None
