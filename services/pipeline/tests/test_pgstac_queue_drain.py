"""The pgstac query-queue drain tick (M3-A, spec §4.3 / §4.6 / §5).

Against a fake repo: the tick drains only when this deployment owns the drain,
always samples (the metric is the one outside-the-process proof the session
GUCs are in effect), prunes history in both modes, and flags a stale queue —
a drainer that silently stopped is otherwise invisible. A raising `drain()`
must not silence that same alarm — see
`test_drain_failure_still_publishes_before_sample_prunes_and_is_attributed`.

`TestPgPgstacQueueRepoConnection` covers the controller ruling (and its fix
round) on the concrete repo's drain connection: `update_collection_extent`
must be set TRUE on it (or the extent refresh `update_partition_stats` queues
never runs, spec §4.4), and `use_queue` must be set explicitly FALSE on it —
not merely left unset, since an unset GUC would inherit whatever
`pgstac_settings`/`ALTER DATABASE`/`ALTER ROLE` says (or that same refresh
gets re-queued instead of executed, forever) — the opposite pairing from the
writer's `pipeline.db.pgstac_session` hook, which this module deliberately
does not reuse. Also covers: a failing SET must not leak the connection, and
`drain()` issues the full statement sequence in order, with `CALL` (not
`SELECT`) for the procedure.
"""

from __future__ import annotations

import logging

import pytest

from pipeline import metrics
from pipeline.stac.query_queue import (
    DRAIN_CONNECTION_SQL,
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


class _RaisingDrainRepo(FakeQueueRepo):
    async def drain(self) -> DrainOutcome:
        self.drain_calls += 1
        raise RuntimeError("connection refused")


async def test_drain_failure_still_publishes_before_sample_prunes_and_is_attributed(caplog):
    # Only ONE sample is ever taken: drain() raises before a second sample
    # would be requested, so `after` stays None and the before-or-after
    # gauge fallback publishes `before` — the queue growing during an outage
    # is exactly what must stay visible.
    repo = _RaisingDrainRepo(samples=[QueueSample(depth=7, oldest_age_seconds=12.0)])
    err_before = _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error")
    fail_before = metrics.PGSTAC_QUEUE_DRAIN_FAILURES._value.get()

    with caplog.at_level(logging.ERROR, logger="pipeline.stac.query_queue"):
        result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)

    assert repo.drain_calls == 1
    assert result.before == QueueSample(7, 12.0)
    assert result.after is None
    assert result.drained is None
    # prune_history still ran despite the drain failure.
    assert result.pruned == 3 and repo.prune_calls == [7]
    assert _gauge(metrics.PGSTAC_QUEUE_DEPTH) == 7
    assert _gauge(metrics.PGSTAC_QUEUE_OLDEST_SECONDS) == 12.0
    # A failed CALL ran NO statements, so the per-statement counter must not
    # move; the drain-failure counter is what carries it.
    assert _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error") == err_before
    assert metrics.PGSTAC_QUEUE_DRAIN_FAILURES._value.get() == fail_before + 1
    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.mode == "pipeline"
    assert record.depth == 7
    assert record.oldest_age_seconds == 12.0
    assert record.exc_info is not None  # logger.exception captured the traceback


class _FakeAsyncConn:
    def __init__(self, fetch_results=None, fail_on=None):
        self.executed: list[tuple[str, object]] = []
        self.closed = False
        self._fetch_results = list(fetch_results or [])
        self._fail_on = fail_on

    async def execute(self, sql, params=None):
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("SET failed")
        self.executed.append((sql, params))
        return self

    async def fetchone(self):
        return self._fetch_results.pop(0)

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False


class TestPgPgstacQueueRepoConnection:
    """Controller ruling (and its fix round) on the concrete repo's drain
    connection: it must carry the OPPOSITE GUC pairing from the writer's
    (pipeline.db.pgstac_session) connections — update_collection_extent ON so
    the extent-refresh `run_or_queue` call nested inside `update_partition_stats`
    executes here rather than never running (spec §4.4); use_queue explicitly
    FALSE (not merely unset — get_setting COALESCEs an unset GUC through
    pgstac_settings/ALTER DATABASE/ALTER ROLE, so "never set" is not
    equivalent to "false") or that same nested call would re-queue the extent
    UPDATE instead of running it, deferring it one hop further on every
    drain, forever. This is why the shared configure_pgstac_session_async
    hook (which sets both GUCs, use_queue TRUE) is deliberately not reused
    here. A failing SET must not leak the connection."""

    def test_drain_connection_sql_is_the_writer_pairing_reversed(self):
        assert DRAIN_CONNECTION_SQL == (
            "SET pgstac.update_collection_extent TO TRUE",
            "SET pgstac.use_queue TO FALSE",
        )

    async def test_connect_sets_update_collection_extent_true_and_use_queue_false(
        self, monkeypatch
    ):
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
        assert fake_conn.executed == [
            ("SET pgstac.update_collection_extent TO TRUE", None),
            ("SET pgstac.use_queue TO FALSE", None),
        ]

    async def test_connect_closes_the_connection_if_a_set_fails(self, monkeypatch):
        import psycopg

        fake_conn = _FakeAsyncConn(fail_on="use_queue")

        async def fake_connect(conninfo, **kwargs):
            return fake_conn

        monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)

        repo = PgPgstacQueueRepo(database_url="postgresql://ignored")
        with pytest.raises(RuntimeError):
            await repo._connect()

        assert fake_conn.closed is True

    async def test_drain_issues_setup_then_call_then_history_query_in_order(self, monkeypatch):
        import psycopg

        fake_conn = _FakeAsyncConn(fetch_results=[("2026-09-08T00:00:00Z",), (5, 1)])

        async def fake_connect(conninfo, **kwargs):
            return fake_conn

        monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)

        repo = PgPgstacQueueRepo(database_url="postgresql://ignored")
        outcome = await repo.drain()

        assert outcome == DrainOutcome(executed=5, errors=1)
        statements = [sql for sql, _params in fake_conn.executed]
        assert statements == [
            "SET pgstac.update_collection_extent TO TRUE",
            "SET pgstac.use_queue TO FALSE",
            "SELECT clock_timestamp()",
            "CALL pgstac.run_queued_queries()",
            "SELECT count(*), count(error) FROM pgstac.query_queue_history WHERE finished >= %s",
        ]
        # A PROCEDURE that COMMITs internally: CALL, never SELECT.
        assert statements[3].startswith("CALL ")

    def test_is_a_queue_repo(self):
        assert issubclass(PgPgstacQueueRepo, PgstacQueueRepo)
