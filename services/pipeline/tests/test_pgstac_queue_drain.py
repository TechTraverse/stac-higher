"""The pgstac query-queue drain tick (M3-A, spec §4.3 / §4.6 / §5).

Against a fake repo: the tick drains only when this deployment owns the drain,
always samples (the metric is the one outside-the-process proof the session
GUCs are in effect), prunes history in both modes, and flags a stale queue —
a drainer that silently stopped is otherwise invisible.

`TestPgPgstacQueueRepoConnection` covers the controller ruling on the concrete
repo's drain connection: `update_collection_extent` must be set TRUE on it
(or the extent refresh `update_partition_stats` queues never runs, spec §4.4),
and `use_queue` must NEVER be set on it (or that same refresh gets re-queued
instead of executed, forever) — the opposite pairing from the writer's
`pipeline.db.pgstac_session` hook, which this module deliberately does not
reuse.
"""

from __future__ import annotations

import logging

from pipeline import metrics
from pipeline.stac.query_queue import (
    DrainOutcome,
    PgPgstacQueueRepo,
    PgstacQueueRepo,
    QueueSample,
    drain_tick,
)


class FakeQueueRepo(PgstacQueueRepo):
    def __init__(self, samples: list[QueueSample], drained: DrainOutcome | None = None):
        self.samples = list(samples)
        self.drained = drained or DrainOutcome(executed=0, errors=0)
        self.drain_calls = 0
        self.prune_calls: list[int] = []

    async def sample(self) -> QueueSample:
        return self.samples.pop(0)

    async def drain(self) -> DrainOutcome:
        self.drain_calls += 1
        return self.drained

    async def prune_history(self, older_than_days: int) -> int:
        self.prune_calls.append(older_than_days)
        return 3


def _gauge(g) -> float:
    return g._value.get()


def _counter(c, **labels) -> float:
    return c.labels(**labels)._value.get()


async def test_pipeline_mode_drains_samples_twice_and_reports():
    repo = FakeQueueRepo(
        samples=[QueueSample(depth=5, oldest_age_seconds=42.0), QueueSample(0, None)],
        drained=DrainOutcome(executed=5, errors=1),
    )
    ok_before = _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="ok")
    err_before = _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error")

    result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)

    assert repo.drain_calls == 1
    assert result.before == QueueSample(5, 42.0)
    assert result.after == QueueSample(0, None)
    assert result.drained == DrainOutcome(executed=5, errors=1)
    assert result.pruned == 3 and repo.prune_calls == [7]
    assert result.stale is False
    # Gauges reflect the AFTER sample — what is left for the next tick.
    assert _gauge(metrics.PGSTAC_QUEUE_DEPTH) == 0
    assert _gauge(metrics.PGSTAC_QUEUE_OLDEST_SECONDS) == 0
    assert _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="ok") == ok_before + 4
    assert _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error") == err_before + 1


async def test_database_mode_never_drains_but_still_samples_and_prunes():
    repo = FakeQueueRepo(samples=[QueueSample(depth=2, oldest_age_seconds=10.0)])

    result = await drain_tick(repo, mode="database", stale_after_seconds=300, history_days=7)

    assert repo.drain_calls == 0
    assert result.after is None and result.drained is None
    assert result.pruned == 3
    assert _gauge(metrics.PGSTAC_QUEUE_DEPTH) == 2
    assert _gauge(metrics.PGSTAC_QUEUE_OLDEST_SECONDS) == 10.0


async def test_stale_queue_is_flagged_and_logged(caplog):
    # Pipeline mode, but the drain left the oldest entry behind (an error kept
    # it queued? no — errors are recorded and removed; this models a CALL that
    # hit queue_timeout with work left) → oldest survives past the bound.
    repo = FakeQueueRepo(
        samples=[QueueSample(depth=40, oldest_age_seconds=900.0), QueueSample(30, 700.0)],
        drained=DrainOutcome(executed=10, errors=0),
    )
    with caplog.at_level(logging.WARNING, logger="pipeline.stac.query_queue"):
        result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)

    assert result.stale is True
    record = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert record.oldest_age_seconds == 700.0
    assert record.stale_after_seconds == 300
    assert record.mode == "pipeline"


async def test_database_mode_stale_means_the_external_drainer_stopped(caplog):
    repo = FakeQueueRepo(samples=[QueueSample(depth=1, oldest_age_seconds=301.0)])
    with caplog.at_level(logging.WARNING, logger="pipeline.stac.query_queue"):
        result = await drain_tick(repo, mode="database", stale_after_seconds=300, history_days=7)
    assert result.stale is True
    assert any(r.mode == "database" for r in caplog.records if r.levelno == logging.WARNING)


async def test_empty_queue_is_not_stale():
    repo = FakeQueueRepo(samples=[QueueSample(0, None), QueueSample(0, None)])
    result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)
    assert result.stale is False


class _FakeAsyncConn:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self


class TestPgPgstacQueueRepoConnection:
    """Controller ruling on the concrete repo's drain connection: it must
    carry the OPPOSITE GUC pairing from the writer's
    (pipeline.db.pgstac_session) connections — update_collection_extent ON so
    the extent-refresh `run_or_queue` call nested inside `update_partition_stats`
    executes here rather than never running (spec §4.4); use_queue left OFF
    (never set) or that same nested call would re-queue the extent UPDATE
    instead of running it, deferring it one hop further on every drain,
    forever. This is why the shared configure_pgstac_session_async hook
    (which sets both GUCs) is deliberately not reused here."""

    async def test_connect_sets_update_collection_extent_and_never_use_queue(self, monkeypatch):
        import psycopg

        fake_conn = _FakeAsyncConn()
        connect_kwargs: dict = {}

        async def fake_connect(conninfo, **kwargs):
            connect_kwargs.update(kwargs)
            connect_kwargs["conninfo"] = conninfo
            return fake_conn

        monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)

        repo = PgPgstacQueueRepo(database_url="postgresql://ignored")
        conn = await repo._connect()

        assert conn is fake_conn
        assert connect_kwargs == {"autocommit": True, "conninfo": "postgresql://ignored"}
        assert fake_conn.executed == [("SET pgstac.update_collection_extent TO TRUE", None)]
        assert not any("use_queue" in sql for sql, _params in fake_conn.executed)

    def test_is_a_queue_repo(self):
        assert issubclass(PgPgstacQueueRepo, PgstacQueueRepo)
