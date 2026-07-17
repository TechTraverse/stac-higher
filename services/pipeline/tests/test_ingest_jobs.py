"""Ingest job wiring: register() attaches the poll periodic + stage tasks."""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.repo import IngestAssociation
from pipeline.jobs import ingest
from pipeline.jobs.ingest import (
    CRON,
    JOB_DISCOVER,
    JOB_FETCH,
    JOB_GROUP,
    JOB_ITEMIZE,
    JOB_POLL,
)
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue


def test_register_wires_poll_periodic_and_stage_tasks():
    queue = InMemoryQueue()
    ingest.register(queue, Settings.from_env(env={}))
    assert set(queue.tasks) == {JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE}
    assert JOB_POLL in queue.periodic
    assert queue.periodic[JOB_POLL].cron == CRON


def test_build_queue_includes_ingest_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {JOB_POLL, JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE} <= registered


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
    monkeypatch.setattr(ingest, "fetch_stage", _fake_fetch_stage)

    await queue.tasks["pipeline.ingest_fetch"](
        association_id="a1", item_id="scene", source_paths=["scene.tif"]
    )
    assert not [j for j in queue.jobs if j.name == JOB_ITEMIZE]
