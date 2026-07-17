"""Ingest job wiring: register() attaches the poll periodic + stage tasks."""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.jobs import ingest
from pipeline.jobs.ingest import (
    CRON,
    JOB_DISCOVER,
    JOB_FETCH,
    JOB_GROUP,
    JOB_POLL,
)
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue


def test_register_wires_poll_periodic_and_stage_tasks():
    queue = InMemoryQueue()
    ingest.register(queue, Settings.from_env(env={}))
    assert set(queue.tasks) == {JOB_DISCOVER, JOB_GROUP, JOB_FETCH}
    assert JOB_POLL in queue.periodic
    assert queue.periodic[JOB_POLL].cron == CRON


def test_build_queue_includes_ingest_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {JOB_POLL, JOB_DISCOVER, JOB_GROUP, JOB_FETCH} <= registered
