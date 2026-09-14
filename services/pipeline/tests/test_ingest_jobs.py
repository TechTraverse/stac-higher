"""Ingest job wiring: register() attaches the poll periodic + stage tasks."""

from __future__ import annotations

import datetime as dt

from _ingest_fake import FakeIngestRepo
from pipeline.config import Settings
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.itemize import ItemizeOutcome
from pipeline.ingest.repo import IngestAssociation, LedgerEntry
from pipeline.jobs import ingest
from pipeline.jobs.ingest import (
    CRON,
    JOB_DISCOVER,
    JOB_FETCH,
    JOB_GROUP,
    JOB_ITEMIZE,
    JOB_POLL,
    JOB_RECOVERY_SWEEP,
)
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue


def test_register_wires_poll_periodic_and_stage_tasks():
    queue = InMemoryQueue()
    ingest.register(queue, Settings.from_env(env={}))
    assert set(queue.tasks) == {JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE}
    assert JOB_POLL in queue.periodic
    assert queue.periodic[JOB_POLL].cron == CRON
    # I-55: every chain stage carries a queue-level retry for transient faults
    assert set(queue.retry_specs) == {JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE}


def test_build_queue_includes_ingest_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {JOB_POLL, JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE} <= registered
    # I-55: the retry spec must survive the real backend mapping
    assert queue.app.tasks[JOB_ITEMIZE].retry_strategy is not None


def test_register_includes_itemize_task():
    queue = InMemoryQueue()
    ingest.register(queue, Settings.from_env(env={}))
    assert JOB_ITEMIZE in queue.tasks


async def test_fetch_handler_enqueues_itemize_when_stored(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest.register(queue, settings)

    assoc = IngestAssociation(
        id="a1", collection_id="col", config={"source_path": "/o"}, connection=None
    )
    config = parse_ingest_config({"source_path": "/o"})

    async def _fake_load(_settings, _aid):
        return (object(), assoc, config)

    async def _fake_fetch_stage(*_a, **_k):
        return 1  # one file stored → must enqueue itemize

    monkeypatch.setattr(ingest, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(ingest, "_load_association", _fake_load)
    monkeypatch.setattr(ingest, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(ingest, "build_platform_client", lambda _s: object())
    # M3-C: fetch() builds the transfer policy from the adapter before
    # fetch_stage runs; the adapter here is a bare object() stand-in, so
    # stub the policy builder rather than give it a real protocol/endpoint.
    monkeypatch.setattr(ingest, "transfer_policy", lambda _a, _s: None)
    monkeypatch.setattr(ingest, "fetch_stage", _fake_fetch_stage)

    await queue.tasks["pipeline.ingest_fetch"](
        association_id="a1", item_id="scene", source_paths=["scene.tif"]
    )

    itemize = [j for j in queue.jobs if j.name == JOB_ITEMIZE]
    assert len(itemize) == 1
    assert itemize[0].payload == {
        "association_id": "a1",
        "item_id": "scene",
        "source_paths": ["scene.tif"],
    }


async def test_fetch_handler_skips_itemize_when_nothing_stored(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest.register(queue, settings)
    assoc = IngestAssociation(
        id="a1", collection_id="col", config={"source_path": "/o"}, connection=None
    )
    config = parse_ingest_config({"source_path": "/o"})

    async def _fake_load(_settings, _aid):
        return (object(), assoc, config)

    async def _fake_fetch_stage(*_a, **_k):
        return 0  # nothing stored → no itemize enqueue

    monkeypatch.setattr(ingest, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(ingest, "_load_association", _fake_load)
    monkeypatch.setattr(ingest, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(ingest, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(ingest, "transfer_policy", lambda _a, _s: None)
    monkeypatch.setattr(ingest, "fetch_stage", _fake_fetch_stage)

    await queue.tasks["pipeline.ingest_fetch"](
        association_id="a1", item_id="scene", source_paths=["scene.tif"]
    )
    assert not [j for j in queue.jobs if j.name == JOB_ITEMIZE]


async def test_itemize_handler_bumps_nothing_when_the_group_goes_to_an_extractor(monkeypatch):
    # G-6: "extracting" is not a terminal outcome — the extract finalize
    # branch bumps the rollup when the item actually lands.
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest.register(queue, settings)

    assoc = IngestAssociation(
        id="a1", collection_id="col", config={"source_path": "/o"}, connection=None
    )
    config = parse_ingest_config({"source_path": "/o"})
    repo = FakeIngestRepo()

    async def _fake_load(_settings, _aid):
        return (repo, assoc, config)

    async def _fake_run_itemize(*_a, **_k):
        return ItemizeOutcome("extracting", "scene", "run-1")

    monkeypatch.setattr(ingest, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(ingest, "_load_association", _fake_load)
    monkeypatch.setattr(ingest, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(ingest, "build_platform_client", lambda _s: object())
    # platform_s3_access resolves the staging endpoint host (like
    # build_platform_client above) — not exercised by run_itemize, which is
    # faked below, so stub it out rather than resolving "minio" live (M3-C).
    monkeypatch.setattr(ingest, "platform_s3_access", lambda _s: None)
    monkeypatch.setattr(ingest, "PgPgstacWriter", lambda _u: object())
    monkeypatch.setattr(ingest, "PgProcessRepo", lambda _u: object())
    monkeypatch.setattr(ingest, "run_itemize", _fake_run_itemize)

    await queue.tasks[JOB_ITEMIZE](
        association_id="a1", item_id="scene", source_paths=["scene.nc"]
    )

    assert repo.flow_stats == {}


async def test_recovery_sweep_fails_extracting_rows_whose_run_vanished(monkeypatch):
    # G-6: recovery_sweep must also drive sweep_stuck_extracting — an
    # `extracting` row whose process run never got queued (or closed without
    # finalizing) has no other path back to a terminal status.
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    ingest.register(queue, settings)

    repo = FakeIngestRepo()
    stale = repo.now - dt.timedelta(seconds=settings.ingest_stored_stall_seconds + 1)
    repo.rows["e1"] = LedgerEntry(
        id="e1",
        association_id="a1",
        source_path="scene.nc",
        version=1,
        size=10,
        fingerprint="f",
        checksum=None,
        status="extracting",
        item_id="scene",
        extract_run_id=None,
        created_at=stale,
        updated_at=stale,
    )

    monkeypatch.setattr(ingest, "PgIngestRepo", lambda _url: repo)

    await queue.periodic[JOB_RECOVERY_SWEEP].func(timestamp=0)

    assert repo.rows["e1"].status == "failed"
    assert repo.rows["e1"].reason == "extractor run was never queued"
