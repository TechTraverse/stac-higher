"""Flow monitor (M2-B): condition evaluation + alert lifecycle semantics.

``FakeMonitorRepo`` mirrors the Pg ``sync_alerts`` contract (partial-unique
dedup on open rows, last_seen bump, kind-scoped auto-resolve) so these tests
pin the lifecycle §3.3 promises; the SQL itself is rehearsal/DB-integration
territory like every other Pg repo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pipeline.config import Settings
from pipeline.flow.monitor import MONITOR_KINDS, monitor_tick
from pipeline.flow.repo import (
    AlertCondition,
    ErrorConnection,
    FlowCandidate,
    FlowMonitorRepo,
)
from pipeline.jobs.monitor import JOB_NAME
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue

NOW = dt.datetime(2026, 8, 18, 12, 0, 0, tzinfo=dt.UTC)


@dataclass
class FakeMonitorRepo(FlowMonitorRepo):
    candidates: list[FlowCandidate] = field(default_factory=list)
    #: association ids that have an outstanding (not-yet-delivered) breach.
    breaching: set[str] = field(default_factory=set)
    error_connections: list[ErrorConnection] = field(default_factory=list)
    dead_counts: list[tuple[str, int]] = field(default_factory=list)
    terminal_failures: list[tuple[str, int]] = field(default_factory=list)
    failed_backfills: list[tuple[str, str, str | None]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)

    async def list_flow_candidates(self) -> list[FlowCandidate]:
        return list(self.candidates)

    async def has_breaching_delivery(
        self, association_id: str, window_seconds: int
    ) -> bool:
        return association_id in self.breaching

    async def list_error_connections(self) -> list[ErrorConnection]:
        return list(self.error_connections)

    async def list_dead_delivery_counts(self) -> list[tuple[str, int]]:
        return list(self.dead_counts)

    async def list_terminal_ingest_failures(
        self, max_retries: int
    ) -> list[tuple[str, int]]:
        return list(self.terminal_failures)

    async def list_failed_backfills(self) -> list[tuple[str, str, str | None]]:
        return list(self.failed_backfills)

    async def sync_alerts(
        self, conditions: list[AlertCondition], owned_kinds: tuple[str, ...]
    ) -> tuple[int, int]:
        raised = 0
        seen: set[tuple[str, str, str, str]] = set()
        for c in conditions:
            key = (c.source, c.kind, c.connection_id or "", c.association_id or "")
            seen.add(key)
            open_row = next(
                (a for a in self.alerts if a["key"] == key and a["state"] != "resolved"),
                None,
            )
            if open_row is not None:
                open_row["last_seen"] = dt.datetime.now(dt.UTC)
                open_row["message"] = c.message
            else:
                self.alerts.append(
                    {
                        "key": key,
                        "kind": c.kind,
                        "state": "firing",
                        "message": c.message,
                        "last_seen": dt.datetime.now(dt.UTC),
                    }
                )
                raised += 1
        resolved = 0
        for a in self.alerts:
            if (
                a["state"] != "resolved"
                and a["kind"] in owned_kinds
                and a["key"] not in seen
            ):
                a["state"] = "resolved"
                resolved += 1
        return raised, resolved


def ingest_candidate(**overrides: Any) -> FlowCandidate:
    defaults: dict[str, Any] = dict(
        id="a1",
        collection_id="col",
        direction="ingest",
        connection_id="c1",
        expectation={"expect_activity_within_seconds": 3600},
        flow_stats={},
        edited_at=NOW - dt.timedelta(hours=2),
    )
    defaults.update(overrides)
    return FlowCandidate(**defaults)


def deliver_candidate(**overrides: Any) -> FlowCandidate:
    defaults: dict[str, Any] = dict(
        id="a2",
        collection_id="col",
        direction="deliver",
        connection_id="c2",
        expectation={"deliver_within_seconds": 30},
        flow_stats={},
        edited_at=NOW - dt.timedelta(hours=2),
    )
    defaults.update(overrides)
    return FlowCandidate(**defaults)


async def _tick(repo: FakeMonitorRepo) -> tuple[int, int]:
    return await monitor_tick(repo, ingest_max_retries=3, now=NOW)


def open_alerts(repo: FakeMonitorRepo) -> list[dict[str, Any]]:
    return [a for a in repo.alerts if a["state"] != "resolved"]


# --------------------------------------------------------------------------- #
# flow: ingest inactivity


async def test_ingest_inactivity_fires_when_activity_is_stale():
    stale = (NOW - dt.timedelta(hours=2)).isoformat()
    repo = FakeMonitorRepo(
        candidates=[ingest_candidate(flow_stats={"last_activity_at": stale})]
    )
    raised, _ = await _tick(repo)
    assert raised == 1
    assert open_alerts(repo)[0]["kind"] == "ingest_inactivity"


async def test_ingest_inactivity_quiet_inside_the_window():
    fresh = (NOW - dt.timedelta(minutes=10)).isoformat()
    repo = FakeMonitorRepo(
        candidates=[ingest_candidate(flow_stats={"last_activity_at": fresh})]
    )
    raised, _ = await _tick(repo)
    assert raised == 0
    assert repo.alerts == []


async def test_ingest_inactivity_window_restarts_at_association_edit():
    # No rollup activity yet, but the association was edited 10 minutes ago —
    # the window starts there, so nothing fires.
    repo = FakeMonitorRepo(
        candidates=[ingest_candidate(edited_at=NOW - dt.timedelta(minutes=10))]
    )
    raised, _ = await _tick(repo)
    assert raised == 0


async def test_ingest_inactivity_fires_with_no_reference_point():
    repo = FakeMonitorRepo(candidates=[ingest_candidate(edited_at=None)])
    raised, _ = await _tick(repo)
    assert raised == 1
    assert "no ingest activity observed" in open_alerts(repo)[0]["message"]


async def test_no_expectation_key_never_flow_alerts():
    # A declared-but-empty expectation reads as "no window" (lenient reader);
    # absence-of-data is only detectable against a declared window (§6.6).
    repo = FakeMonitorRepo(candidates=[ingest_candidate(expectation={})])
    raised, _ = await _tick(repo)
    assert raised == 0


async def test_unusable_expectation_skips_candidate_not_tick():
    stale = (NOW - dt.timedelta(hours=2)).isoformat()
    repo = FakeMonitorRepo(
        candidates=[
            ingest_candidate(
                id="bad", expectation={"expect_activity_within_seconds": "soon"}
            ),
            ingest_candidate(flow_stats={"last_activity_at": stale}),
        ]
    )
    raised, _ = await _tick(repo)
    assert raised == 1  # the good candidate still evaluated


# --------------------------------------------------------------------------- #
# flow: delivery SLO


async def test_delivery_slo_fires_on_slow_last_delivery():
    repo = FakeMonitorRepo(
        candidates=[deliver_candidate(flow_stats={"last_latency_seconds": 95.0})]
    )
    raised, _ = await _tick(repo)
    assert raised == 1
    alert = open_alerts(repo)[0]
    assert alert["kind"] == "delivery_slo"
    assert "95.0s" in alert["message"]


async def test_delivery_slo_fires_on_outstanding_late_rows():
    repo = FakeMonitorRepo(
        candidates=[deliver_candidate(flow_stats={"last_latency_seconds": 2.0})],
        breaching={"a2"},
    )
    raised, _ = await _tick(repo)
    assert raised == 1
    assert "undelivered items" in open_alerts(repo)[0]["message"]


async def test_delivery_slo_quiet_when_fast_and_current():
    repo = FakeMonitorRepo(
        candidates=[deliver_candidate(flow_stats={"last_latency_seconds": 2.0})]
    )
    raised, _ = await _tick(repo)
    assert raised == 0


# --------------------------------------------------------------------------- #
# health + job_failure sources


async def test_error_connection_raises_health_alert():
    repo = FakeMonitorRepo(
        error_connections=[ErrorConnection(id="c9", name="sftp src", last_error="boom")]
    )
    raised, _ = await _tick(repo)
    assert raised == 1
    alert = open_alerts(repo)[0]
    assert alert["kind"] == "connection_error"
    assert "sftp src" in alert["message"] and "boom" in alert["message"]


async def test_job_failure_conditions():
    repo = FakeMonitorRepo(
        dead_counts=[("a5", 3)],
        terminal_failures=[("a6", 2)],
        failed_backfills=[("a7", "bf1", "cursor lost")],
    )
    raised, _ = await _tick(repo)
    assert raised == 3
    kinds = {a["kind"] for a in open_alerts(repo)}
    assert kinds == {"delivery_dead", "ingest_failed", "backfill_failed"}


# --------------------------------------------------------------------------- #
# lifecycle (§3.3) — dedup, re-fire, auto-resolve


async def test_reobserved_condition_bumps_not_duplicates():
    repo = FakeMonitorRepo(
        error_connections=[ErrorConnection(id="c9", name="src", last_error="boom")]
    )
    first_raised, _ = await _tick(repo)
    second_raised, second_resolved = await _tick(repo)
    assert (first_raised, second_raised, second_resolved) == (1, 0, 0)
    assert len(repo.alerts) == 1


async def test_cleared_condition_auto_resolves():
    repo = FakeMonitorRepo(
        error_connections=[ErrorConnection(id="c9", name="src", last_error="boom")]
    )
    await _tick(repo)
    repo.error_connections = []  # connection recovered
    raised, resolved = await _tick(repo)
    assert (raised, resolved) == (0, 1)
    assert repo.alerts[0]["state"] == "resolved"


async def test_acknowledged_alert_keeps_tracking_and_can_auto_resolve():
    repo = FakeMonitorRepo(
        error_connections=[ErrorConnection(id="c9", name="src", last_error="boom")]
    )
    await _tick(repo)
    repo.alerts[0]["state"] = "acknowledged"  # operator ack (app-side)
    before = repo.alerts[0]["last_seen"]
    raised, _ = await _tick(repo)
    assert raised == 0  # bumped, not duplicated
    assert repo.alerts[0]["last_seen"] >= before
    repo.error_connections = []
    _, resolved = await _tick(repo)
    assert resolved == 1


async def test_resolve_then_refire_creates_a_new_row():
    repo = FakeMonitorRepo(
        error_connections=[ErrorConnection(id="c9", name="src", last_error="boom")]
    )
    await _tick(repo)
    repo.error_connections = []
    await _tick(repo)  # auto-resolved
    repo.error_connections = [ErrorConnection(id="c9", name="src", last_error="boom")]
    raised, _ = await _tick(repo)
    assert raised == 1  # NEW row — this is what re-notifies (§3.3)
    assert len(repo.alerts) == 2


async def test_foreign_kinds_never_auto_resolved():
    repo = FakeMonitorRepo()
    repo.alerts.append(
        {
            "key": ("job_failure", "webhook_failed", "", "a9"),
            "kind": "webhook_failed",  # M2-C's kind, not the monitor's
            "state": "firing",
            "message": "x",
            "last_seen": NOW,
        }
    )
    _, resolved = await _tick(repo)
    assert resolved == 0
    assert repo.alerts[0]["state"] == "firing"
    assert "webhook_failed" not in MONITOR_KINDS


# --------------------------------------------------------------------------- #
# job wiring


def test_monitor_job_registered():
    queue = InMemoryQueue()
    from pipeline.jobs import monitor

    monitor.register(queue, Settings.from_env(env={}))
    assert JOB_NAME in queue.periodic
    assert queue.periodic[JOB_NAME].cron == "* * * * *"


def test_build_queue_includes_monitor():
    queue = build_queue(Settings.from_env(env={}))
    assert JOB_NAME in {t for t in queue.app.tasks}
