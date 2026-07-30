"""Slice B-iii: ingest crash recovery (ISSUES I-52) — the stuck-`fetching`
sweep and the bounded `failed` retry, plus the periodic job wiring.

I-55 additions: the stuck-`stored` sweep (an itemize crash whose queue retries
are exhausted strands the ledger at `stored`, a state neither I-52 sweep
covered) and the end-to-end crash → sweep → re-drive → `itemized` path."""

import datetime as dt

import pytest

from _ingest_fake import EPOCH, FakeAdapter, FakeIngestRepo, FakeS3
from pipeline.config import Settings
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.fetch import fetch_stage
from pipeline.ingest.itemize import run_itemize
from pipeline.ingest.repo import IngestAssociation, LedgerEntry
from pipeline.jobs import ingest as ingest_jobs
from pipeline.jobs.ingest import JOB_POLL, JOB_RECOVERY_SWEEP
from pipeline.queue.memory import InMemoryQueue
from pipeline.stac.pgstac_writer import PgstacWriter

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


async def test_sweep_resettles_stalled_stored_rows():
    repo = FakeIngestRepo(now=EPOCH + dt.timedelta(hours=2))
    repo.rows = {
        "stale": _entry("stale", "stored", EPOCH),  # 2h old — itemize never landed
        "fresh": _entry("fresh", "stored", repo.now - dt.timedelta(seconds=30)),
        "done": _entry("done", "itemized", EPOCH),
    }
    resettled, dead = await repo.sweep_stuck_stored(3, older_than_seconds=1800)
    assert (resettled, dead) == (1, 0)
    assert repo.rows["stale"].status == "settled"
    assert repo.retries["stale"] == 1
    assert repo.rows["fresh"].status == "stored"  # still within the window
    assert repo.rows["done"].status == "itemized"


async def test_stored_sweep_dead_ends_at_retry_cap():
    repo = FakeIngestRepo(now=EPOCH + dt.timedelta(hours=2))
    repo.rows = {"s": _entry("s", "stored", EPOCH)}
    repo.retries["s"] = 3
    resettled, dead = await repo.sweep_stuck_stored(3, older_than_seconds=1800)
    assert (resettled, dead) == (0, 1)
    assert repo.rows["s"].status == "failed"
    # budget already spent — the failed sweep must not loop it back either
    repo.rows["s"].updated_at = EPOCH
    assert await repo.sweep_failed_for_retry(3, older_than_seconds=300) == 0
    assert repo.rows["s"].status == "failed"


class _CrashingWriter(PgstacWriter):
    """Transient failure mode: upsert dies mid-run (e.g. DB connection drop)."""

    async def upsert_items(self, items):
        raise RuntimeError("connection refused")

    async def get_collection_bbox(self, collection_id):
        return None


class _OkWriter(PgstacWriter):
    def __init__(self):
        self.items: list = []

    async def upsert_items(self, items):
        self.items.extend(items)

    async def get_collection_bbox(self, collection_id):
        return None


async def test_itemize_crash_re_drives_to_itemized():
    # The full I-55 story: itemize crashes → members stranded at `stored` →
    # the stall sweep re-settles them → the normal FETCH → ITEMIZE chain
    # (idempotent) lands the item on the re-drive.
    conn = ConnectionRow(
        id="c1", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    assoc = IngestAssociation(
        id="assoc1",
        collection_id="col",
        config={
            "source_path": "in",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        },
        connection=conn,
    )
    config = parse_ingest_config(assoc.config)
    repo = FakeIngestRepo(now=EPOCH)
    eid = await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status="stored", size=1, fingerprint="f"
    )

    with pytest.raises(RuntimeError):
        await run_itemize(
            repo, _CrashingWriter(), FakeAdapter(), FakeS3(),
            association=assoc, config=config, item_id="scene",
            source_paths=["scene.bin"], bucket="b", asset_href_base="/api/assets",
        )
    assert repo.rows[eid].status == "stored"  # the stranded state I-55 closes

    # stall window passes → the sweep re-enters the row at `settled`
    repo.now = EPOCH + dt.timedelta(hours=1)
    assert await repo.sweep_stuck_stored(3, older_than_seconds=1800) == (1, 0)
    assert repo.rows[eid].status == "settled"

    # normal chain re-drive: FETCH re-copies, ITEMIZE upserts and marks
    adapter = FakeAdapter(blobs={"in/scene.bin": b"x"})
    s3 = FakeS3()
    assert await fetch_stage(
        repo, assoc, config, adapter, s3, "b", "scene", ["scene.bin"]
    ) == 1
    writer = _OkWriter()
    out = await run_itemize(
        repo, writer, adapter, s3,
        association=assoc, config=config, item_id="scene",
        source_paths=["scene.bin"], bucket="b", asset_href_base="/api/assets",
    )
    assert out.status == "itemized"
    assert len(writer.items) == 1
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == "itemized"


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
        async def sweep_stuck_stored(self, max_retries, older_than_seconds):
            calls.append(("stored", max_retries, older_than_seconds))
            return (1, 0)

    monkeypatch.setattr(ingest_jobs, "PgIngestRepo", _Repo)
    await queue.periodic[JOB_RECOVERY_SWEEP].func(timestamp=0)
    assert calls == [
        ("stuck", settings.ingest_fetch_stall_seconds),
        ("failed", settings.ingest_max_retries, settings.ingest_failed_retry_seconds),
        ("stored", settings.ingest_max_retries, settings.ingest_stored_stall_seconds),
    ]
