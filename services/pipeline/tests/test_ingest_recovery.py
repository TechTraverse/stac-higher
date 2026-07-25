"""Slice B-iii: ingest crash recovery (ISSUES I-52) — the stuck-`fetching`
sweep and the bounded `failed` retry, plus the periodic job wiring."""

import datetime as dt

import pytest

from _ingest_fake import EPOCH, FakeIngestRepo
from pipeline.config import Settings
from pipeline.ingest.repo import LedgerEntry
from pipeline.jobs import ingest as ingest_jobs
from pipeline.jobs.ingest import JOB_POLL, JOB_RECOVERY_SWEEP
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio


def _entry(entry_id: str, status: str, updated_at: dt.datetime) -> LedgerEntry:
    return LedgerEntry(
        id=entry_id,
        association_id="a1",
        source_path=f"in/{entry_id}.tif",
        version=1,
        size=3,
        fingerprint="fp",
        checksum=None,
        status=status,
        item_id=None,
        created_at=updated_at,
        updated_at=updated_at,
    )


async def test_sweep_resets_stale_fetching_only():
    repo = FakeIngestRepo(now=EPOCH + dt.timedelta(hours=2))
    repo.rows = {
        "stale": _entry("stale", "fetching", EPOCH),  # 2h old — presumed crashed
        "fresh": _entry("fresh", "fetching", repo.now - dt.timedelta(seconds=30)),
        "done": _entry("done", "itemized", EPOCH),
    }
    reset = await repo.sweep_stuck_fetching(older_than_seconds=1800)
    assert reset == 1
    assert repo.rows["stale"].status == "settled"
    assert repo.rows["fresh"].status == "fetching"  # still within the window
    assert repo.rows["done"].status == "itemized"


async def test_failed_retry_is_bounded():
    repo = FakeIngestRepo(now=EPOCH + dt.timedelta(hours=1))
    repo.rows = {"f": _entry("f", "failed", EPOCH)}

    for expected_retries in (1, 2, 3):
        assert await repo.sweep_failed_for_retry(3, older_than_seconds=300) == 1
        assert repo.rows["f"].status == "settled"
        assert repo.retries["f"] == expected_retries
        # the stage fails again, cooled off long enough to be swept again
        repo.rows["f"].status = "failed"
        repo.rows["f"].updated_at = EPOCH

    # budget exhausted — the row stays failed (terminal until operator action)
    assert await repo.sweep_failed_for_retry(3, older_than_seconds=300) == 0
    assert repo.rows["f"].status == "failed"


async def test_failed_retry_waits_for_cooloff():
    repo = FakeIngestRepo(now=EPOCH + dt.timedelta(seconds=60))
    repo.rows = {"f": _entry("f", "failed", EPOCH)}
    # only 60s since the failure — a 300s cool-off must not sweep it yet
    assert await repo.sweep_failed_for_retry(3, older_than_seconds=300) == 0
    assert repo.rows["f"].status == "failed"


async def test_recovery_sweep_job_registered_and_calls_both_sweeps(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest_jobs.register(queue, settings)
    assert JOB_POLL in queue.periodic
    assert JOB_RECOVERY_SWEEP in queue.periodic

    calls: list[tuple] = []

    class _Repo:
        def __init__(self, _url): ...
        async def sweep_stuck_fetching(self, older_than_seconds):
            calls.append(("stuck", older_than_seconds))
            return 2
        async def sweep_failed_for_retry(self, max_retries, older_than_seconds):
            calls.append(("failed", max_retries, older_than_seconds))
            return 1

    monkeypatch.setattr(ingest_jobs, "PgIngestRepo", _Repo)
    await queue.periodic[JOB_RECOVERY_SWEEP].func(timestamp=0)
    assert calls == [
        ("stuck", settings.ingest_fetch_stall_seconds),
        ("failed", settings.ingest_max_retries, settings.ingest_failed_retry_seconds),
    ]
