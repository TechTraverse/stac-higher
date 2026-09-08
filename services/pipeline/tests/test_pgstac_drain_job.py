"""Job wiring for the pgstac queue drain (M3-A): registered as a one-minute
periodic, the tick built from settings, the outcome logged in structured fields."""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.jobs import pgstac_drain
from pipeline.queue.memory import InMemoryQueue
from pipeline.stac.query_queue import DrainOutcome, QueueSample, TickResult


def test_registers_a_one_minute_periodic():
    queue = InMemoryQueue()
    pgstac_drain.register(queue, Settings.from_env(env={}))
    assert pgstac_drain.JOB_NAME in queue.periodic
    assert queue.periodic[pgstac_drain.JOB_NAME].cron == "* * * * *"


async def test_tick_uses_settings_and_logs_the_result(monkeypatch, caplog):
    captured: dict = {}

    async def _fake_tick(repo, *, mode, stale_after_seconds, history_days):
        captured.update(
            repo_url=repo.database_url,
            mode=mode,
            stale_after_seconds=stale_after_seconds,
            history_days=history_days,
        )
        return TickResult(
            mode=mode,
            before=QueueSample(3, 12.0),
            after=QueueSample(0, None),
            drained=DrainOutcome(executed=3, errors=0),
            pruned=0,
            stale=False,
        )

    monkeypatch.setattr(pgstac_drain, "drain_tick", _fake_tick)
    queue = InMemoryQueue()
    settings = Settings.from_env(
        env={
            "DATABASE_URL": "postgresql://x",
            "PGSTAC_QUEUE_DRAINER": "database",
            "PGSTAC_QUEUE_STALE_SECONDS": "60",
            "PGSTAC_QUEUE_HISTORY_DAYS": "2",
        }
    )
    pgstac_drain.register(queue, settings)

    with caplog.at_level(logging.INFO, logger="pipeline.jobs.pgstac_drain"):
        await queue.periodic[pgstac_drain.JOB_NAME].func(timestamp=123)

    assert captured == {
        "repo_url": "postgresql://x",
        "mode": "database",
        "stale_after_seconds": 60,
        "history_days": 2,
    }
    record = next(r for r in caplog.records if r.levelno == logging.INFO)
    assert record.depth_before == 3
    assert record.depth_after == 0
    assert record.executed == 3
    assert record.scheduled_timestamp == 123


async def test_idle_tick_is_quiet(monkeypatch, caplog):
    async def _fake_tick(repo, **kwargs):
        return TickResult(
            mode="pipeline",
            before=QueueSample(0, None),
            after=QueueSample(0, None),
            drained=DrainOutcome(0, 0),
            pruned=0,
            stale=False,
        )

    monkeypatch.setattr(pgstac_drain, "drain_tick", _fake_tick)
    queue = InMemoryQueue()
    pgstac_drain.register(queue, Settings.from_env(env={}))
    with caplog.at_level(logging.INFO, logger="pipeline.jobs.pgstac_drain"):
        await queue.periodic[pgstac_drain.JOB_NAME].func(timestamp=1)
    # An empty queue every minute is the normal state; don't log it.
    assert not [r for r in caplog.records if r.levelno == logging.INFO]
