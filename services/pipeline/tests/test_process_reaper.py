"""Orphaned run-container reaper (M3-W-1, the M5-C deferral closed).

A worker killed between `launch` and `reap` leaves a container behind: the
`finally` never ran, and `process/sweep.py` deliberately refuses to reap
containers from a DB sweep. These tests pin the leg that does — and, more
importantly, the two things that make it SAFE to run continuously:

- the ledger is read AFTER the container listing, so a container that appears
  between the two can never be judged against a ledger read that predates it;
- a container whose run row is still `running` is left alone.
"""

from __future__ import annotations

import datetime as dt

import pytest

from _process_fake import FakeProcessRepo
from pipeline import metrics
from pipeline.process.config import MAX_TIMEOUT_SECONDS
from pipeline.process.docker_executor import RUN_ID_LABEL, DockerExecutor
from pipeline.process.executor import ExecutorUnavailable, LaunchedRun, RunHandle
from pipeline.process.memory_executor import MemoryExecutor
from pipeline.process.reaper import DEFAULT_REAP_MAX_AGE_SECONDS, process_reap_tick

NOW = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)


def launched(run_id: str, *, age_seconds: int = 10, container: str | None = None):
    return LaunchedRun(
        handle=RunHandle(id=container or f"c-{run_id}", backend="fake"),
        run_id=run_id,
        created_at=NOW - dt.timedelta(seconds=age_seconds),
    )


class RecordingExecutor(MemoryExecutor):
    """A MemoryExecutor whose listing is scripted, and which logs call order."""

    def __init__(self, listing, *, order=None, list_error=None, reap_error=None):
        super().__init__()
        self.listing = listing
        self.order = order if order is not None else []
        self.list_error = list_error
        self.reap_error = reap_error

    def list_launched(self):
        self.order.append("list")
        if self.list_error is not None:
            raise self.list_error
        return list(self.listing)

    def reap(self, handle: RunHandle) -> None:
        self.order.append(f"reap:{handle.id}")
        if self.reap_error is not None and handle.id in self.reap_error:
            raise self.reap_error[handle.id]
        super().reap(handle)


class LedgerRepo(FakeProcessRepo):
    """Records that the status read happened, and when."""

    def __init__(self, statuses, *, order=None):
        super().__init__()
        self.statuses = statuses
        self.order = order if order is not None else []
        self.asked: list[str] = []

    async def run_statuses(self, run_ids):
        self.order.append("statuses")
        self.asked = list(run_ids)
        return {r: self.statuses[r] for r in run_ids if r in self.statuses}


async def tick(executor, repo, **kwargs):
    return await process_reap_tick(
        executor=executor,
        repo=repo,
        max_age_seconds=kwargs.pop("max_age_seconds", DEFAULT_REAP_MAX_AGE_SECONDS),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# What gets reaped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["succeeded", "failed", "dead", "queued"])
async def test_a_container_whose_run_is_not_running_is_an_orphan(status):
    """`running` is the ONLY status a live container can legitimately have:
    the claim flips the row before the launch, so anything else means the
    worker that owned this container is gone."""
    executor = RecordingExecutor([launched("r1")])
    repo = LedgerRepo({"r1": status})

    result = await tick(executor, repo)

    assert executor.reaped == ["c-r1"]
    assert result.reaped == 1


async def test_a_running_run_keeps_its_container():
    executor = RecordingExecutor([launched("r1")])
    repo = LedgerRepo({"r1": "running"})

    result = await tick(executor, repo)

    assert executor.reaped == []
    assert result.seen == 1 and result.reaped == 0


async def test_a_container_with_no_run_row_at_all_is_reaped():
    """The row can be gone entirely — a hard-deleted process, or history
    pruning. A container nobody can account for is the clearest orphan."""
    executor = RecordingExecutor([launched("ghost")])
    repo = LedgerRepo({})

    await tick(executor, repo)

    assert executor.reaped == ["c-ghost"]


async def test_a_container_older_than_the_age_ceiling_is_reaped_even_if_running():
    """The backstop for a row wedged `running` that stall recovery cannot
    reach — a run cannot outlive the runtime's own timeout ceiling."""
    executor = RecordingExecutor([launched("r1", age_seconds=100_000)])
    repo = LedgerRepo({"r1": "running"})

    await tick(executor, repo)

    assert executor.reaped == ["c-r1"]


async def test_the_age_ceiling_leaves_room_for_the_longest_legal_run():
    """A legitimately long run must never be killed by the reaper: the ceiling
    has to sit above the runtime config's own maximum timeout."""
    assert DEFAULT_REAP_MAX_AGE_SECONDS > MAX_TIMEOUT_SECONDS


async def test_a_container_the_backend_gave_no_creation_time_for_is_judged_by_the_ledger():
    executor = RecordingExecutor(
        [LaunchedRun(handle=RunHandle(id="c1", backend="fake"), run_id="r1", created_at=None)]
    )
    repo = LedgerRepo({"r1": "running"})

    await tick(executor, repo)

    assert executor.reaped == []


# ---------------------------------------------------------------------------
# Ordering and failure containment
# ---------------------------------------------------------------------------


async def test_the_ledger_is_read_after_the_container_listing():
    """The race the ordering closes: read statuses first and a run claimed
    (and launched) a moment later would be judged `queued`, and the reaper
    would kill a live container."""
    order: list[str] = []
    executor = RecordingExecutor([launched("r1")], order=order)
    repo = LedgerRepo({"r1": "succeeded"}, order=order)

    await tick(executor, repo)

    assert order == ["list", "statuses", "reap:c-r1"]


async def test_only_the_listed_containers_are_asked_about():
    executor = RecordingExecutor([launched("r1"), launched("r2")])
    repo = LedgerRepo({"r1": "running", "r2": "dead"})

    await tick(executor, repo)

    assert sorted(repo.asked) == ["r1", "r2"]
    assert executor.reaped == ["c-r2"]


async def test_an_unavailable_backend_never_touches_the_ledger():
    """A daemon outage must not turn into a DB query storm, and must not be
    read as 'there are no containers'."""
    executor = RecordingExecutor([], list_error=ExecutorUnavailable("no daemon"))
    repo = LedgerRepo({})

    result = await tick(executor, repo)

    assert result == type(result)()
    assert repo.order == []


async def test_nothing_running_means_no_ledger_query():
    executor = RecordingExecutor([])
    repo = LedgerRepo({})

    await tick(executor, repo)

    assert repo.order == []


async def test_one_failed_reap_does_not_abandon_the_rest():
    executor = RecordingExecutor(
        [launched("r1"), launched("r2")],
        reap_error={"c-r1": RuntimeError("daemon hiccup")},
    )
    repo = LedgerRepo({"r1": "dead", "r2": "dead"})

    result = await tick(executor, repo)

    assert executor.reaped == ["c-r2"]
    assert result.reaped == 1 and result.failed == 1


async def test_reaped_orphans_are_counted_for_operators():
    before = metrics.REGISTRY.get_sample_value(
        "pipeline_process_orphan_containers_reaped_total"
    )
    executor = RecordingExecutor([launched("r1")])
    await tick(executor, LedgerRepo({"r1": "dead"}))
    after = metrics.REGISTRY.get_sample_value(
        "pipeline_process_orphan_containers_reaped_total"
    )
    assert after == (before or 0) + 1


# ---------------------------------------------------------------------------
# Backends implement the listing
# ---------------------------------------------------------------------------


def test_the_memory_executor_lists_what_it_launched_and_has_not_reaped():
    from pipeline.process.executor import RunSpec

    executor = MemoryExecutor()
    handle = executor.launch(RunSpec(run_id="r1", process_id="p1", image="img"))
    assert [entry.run_id for entry in executor.list_launched()] == ["r1"]

    executor.reap(handle)
    assert executor.list_launched() == []


class FakeApi:
    """Records Engine API calls and replays a scripted response."""

    def __init__(self, response):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def __call__(self, method, path, *, body=None, timeout=None, raw=False):
        self.calls.append((method, path))
        return self.response


def test_docker_lists_labelled_containers_including_exited_ones():
    """`all=1` is the point: a container that already exited is exactly the
    orphan we are looking for, and the default listing hides it."""
    api = FakeApi(
        [
            {
                "Id": "abc123",
                "Created": int(NOW.timestamp()) - 30,
                "Labels": {RUN_ID_LABEL: "r1", "stac-higher.process-id": "p1"},
            }
        ]
    )
    executor = DockerExecutor(docker_host="tcp://proxy:2375")
    executor._request = api  # type: ignore[method-assign]

    entries = executor.list_launched()

    method, path = api.calls[0]
    assert method == "GET"
    assert path.startswith("/containers/json?all=1&filters=")
    assert RUN_ID_LABEL in path
    assert entries[0].handle.id == "abc123"
    assert entries[0].run_id == "r1"
    assert entries[0].created_at == NOW - dt.timedelta(seconds=30)


def test_docker_ignores_a_container_whose_run_label_is_missing():
    """Belt and braces on the daemon's filter: an unlabelled container is
    something else's, and reaping it would be destroying a stranger's work."""
    api = FakeApi([{"Id": "abc123", "Created": 0, "Labels": {}}])
    executor = DockerExecutor(docker_host="tcp://proxy:2375")
    executor._request = api  # type: ignore[method-assign]

    assert executor.list_launched() == []


# ---------------------------------------------------------------------------
# C-2: scan containers are judged against image_scans, never process_runs
# ---------------------------------------------------------------------------


def launched_scan(scan_id: str, *, age_seconds: int = 10):
    return LaunchedRun(
        handle=RunHandle(id=f"c-{scan_id}", backend="fake"),
        run_id=scan_id,
        created_at=NOW - dt.timedelta(seconds=age_seconds),
        kind="image_scan",
    )


class ScanLedger:
    def __init__(self, statuses):
        self.statuses = statuses
        self.asked: list[str] = []

    async def __call__(self, scan_ids):
        self.asked = list(scan_ids)
        return {s: self.statuses[s] for s in scan_ids if s in self.statuses}


@pytest.mark.asyncio
async def test_a_running_scan_container_is_left_alone():
    executor = RecordingExecutor([launched_scan("s1")])
    repo = LedgerRepo({})
    ledger = ScanLedger({"s1": "running"})
    result = await process_reap_tick(
        executor=executor, repo=repo, scan_statuses=ledger, now=NOW
    )
    assert result.reaped == 0
    # The process ledger was never asked about a scan id.
    assert repo.asked == []
    assert ledger.asked == ["s1"]


@pytest.mark.asyncio
async def test_a_finished_or_unknown_scan_container_is_reaped():
    executor = RecordingExecutor([launched_scan("s1"), launched_scan("s2")])
    ledger = ScanLedger({"s1": "done"})
    result = await process_reap_tick(
        executor=executor, repo=LedgerRepo({}), scan_statuses=ledger, now=NOW
    )
    assert result.reaped == 2


@pytest.mark.asyncio
async def test_without_a_scan_ledger_scan_containers_are_never_judged():
    """The process reaper cannot know a scan's status, so it must not guess:
    no row in process_runs would otherwise read as 'orphan'."""
    executor = RecordingExecutor([launched_scan("s1"), launched("r1")])
    result = await process_reap_tick(executor=executor, repo=LedgerRepo({}), now=NOW)
    assert result.reaped == 1
    assert "reap:c-s1" not in executor.order
