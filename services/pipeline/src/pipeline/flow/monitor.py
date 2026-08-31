"""Flow monitor evaluation (M2-B, ROADMAP §6.6; M2 spec §3).

One periodic tick gathers every currently-true alerting condition across the
three §6.6 sources and hands them to ``FlowMonitorRepo.sync_alerts``, which
raises / re-fires / auto-resolves in one transaction:

- ``flow`` — a declared §5.1 expectation is violated, evaluated against the
  M2-A ``flow_stats`` rollup: ``expect_activity_within_seconds`` (ingest — no
  settle/itemize activity inside the window) and ``deliver_within_seconds``
  (delivery — the last delivery blew the SLO, or a non-terminal row has been
  outstanding past the window).
- ``health`` — a connection the health sweep flipped to ``error``. Observed as
  state each tick; the dedup key makes that equivalent to alerting on the
  ok→error transition, and error→ok auto-resolves.
- ``job_failure`` — dead-lettered deliveries, ingest ledger rows failed at the
  retry cap, a latest-backfill-failed association, and (P7-H, Phase 7 §8)
  push items rejected at finalize: ``staged_uploads`` rows ``rejected``
  inside the lookback window, collection-anchored because push has no
  connection/association/channel. Finalize itself writes no alert rows — it
  writes the ledger; this monitor observes state (the ``ingest_failed``
  division). NOTE the deliberate blind spot: a rejection that never claims a
  ledger row (unknown session, session bound to another item, session minted
  for a different collection — ``claimed=False`` in
  ``pipeline/finalize/push.py``) stamps no ``rejected`` verdict and is
  therefore INVISIBLE to this condition; it surfaces only through
  ``pipeline_finalize_items_total{outcome="rejected"}`` and the structured
  warning log. Widening it would need new persistent state (there is no row
  to observe), which §8 deliberately does not add.

The monitor is the single writer for :data:`MONITOR_KINDS`; auto-resolve is
scoped to those kinds so other alert writers (M2-C webhook failures) are never
clobbered.
"""

from __future__ import annotations

import datetime as dt
import logging

from pipeline.flow.expectation import (
    ExpectationError,
    parse_delivery_expectation,
    parse_ingest_expectation,
)
from pipeline.flow.repo import AlertCondition, FlowCandidate, FlowMonitorRepo

logger = logging.getLogger(__name__)

#: The dedup `kind` values this monitor owns (raise AND auto-resolve).
MONITOR_KINDS = (
    "ingest_inactivity",
    "delivery_slo",
    "connection_error",
    "delivery_dead",
    "ingest_failed",
    "backfill_failed",
    "push_rejected",
    # Phase 9's three process kinds are NOT here yet, deliberately. Membership
    # is not a declaration — it grants this monitor auto-resolve authority
    # (sync_alerts closes open rows of an owned kind whose condition is absent
    # from the current tick), so claiming a kind before its evaluator exists
    # would silently resolve any alert another writer raised. They sit in the
    # fixture's `declared_kinds` until M5-E decides which writer owns each:
    # process_rate_limited in particular is an enqueue-time event, the same
    # shape as the deliberately notify-owned webhook_failed.
)


def _parse_iso(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def evaluate_ingest_inactivity(
    candidate: FlowCandidate, window_seconds: int, now: dt.datetime
) -> AlertCondition | None:
    """Fires when nothing has settled/itemized inside the window. The window
    restarts at the association's last edit (enable/config change) when the
    rollup has no activity stamp yet; with no reference point at all, a
    declared expectation with zero observed activity fires immediately."""
    last = _parse_iso(candidate.flow_stats.get("last_activity_at")) or candidate.edited_at
    if last is not None and (now - last).total_seconds() <= window_seconds:
        return None
    age = int((now - last).total_seconds()) if last is not None else None
    detail = f"no ingest activity for {age}s" if age is not None else "no ingest activity observed"
    return AlertCondition(
        source="flow",
        kind="ingest_inactivity",
        association_id=candidate.id,
        connection_id=candidate.connection_id,
        message=f"{detail} (expected within {window_seconds}s)",
    )


def evaluate_delivery_slo(
    candidate: FlowCandidate,
    window_seconds: int,
    outstanding_breach: bool,
) -> AlertCondition | None:
    """Fires when the most recent delivery exceeded the SLO, or a not-yet-
    delivered row (``outstanding_breach``, from the repo) is already older
    than the window. Clears once a delivery meets the SLO and nothing late is
    outstanding."""
    latency = candidate.flow_stats.get("last_latency_seconds")
    latency_breach = isinstance(latency, (int, float)) and latency > window_seconds
    if not latency_breach and not outstanding_breach:
        return None
    if outstanding_breach:
        detail = f"undelivered items older than {window_seconds}s"
    else:
        detail = f"last delivery took {latency:.1f}s"
    return AlertCondition(
        source="flow",
        kind="delivery_slo",
        association_id=candidate.id,
        connection_id=candidate.connection_id,
        message=f"{detail} (SLO: deliver within {window_seconds}s)",
    )


def evaluate_push_rejections(
    rejections: list[tuple[str, dt.datetime]],
    resolved_floors: dict[str, dt.datetime],
    lookback_seconds: int,
) -> list[AlertCondition]:
    """The §8 push-rejection condition, per collection: rejected
    ``staged_uploads`` rows inside the lookback AND newer than the dedup
    key's most recent ``resolved_at``. Rejected ledger rows are terminal, so
    the floor is what makes a manual resolve stick — without it the same
    rows would re-fire (and re-notify) on the very next tick for the rest of
    the lookback. Auto-resolve falls out of absence: once every rejection
    ages past the lookback (or sits under the floor) the condition vanishes
    and ``sync_alerts`` resolves the open row."""
    counts: dict[str, int] = {}
    for collection_id, finalized_at in rejections:
        floor = resolved_floors.get(collection_id)
        if floor is not None and finalized_at <= floor:
            continue
        counts[collection_id] = counts.get(collection_id, 0) + 1
    return [
        AlertCondition(
            source="job_failure",
            kind="push_rejected",
            collection_id=collection_id,
            message=(
                f"{count} push {'item' if count == 1 else 'items'} rejected at "
                f"finalize in the last {lookback_seconds}s"
            ),
        )
        for collection_id, count in sorted(counts.items())
    ]


async def monitor_tick(
    repo: FlowMonitorRepo,
    *,
    ingest_max_retries: int,
    push_lookback_seconds: int,
    now: dt.datetime | None = None,
) -> tuple[int, int]:
    """One evaluation pass. Returns ``(newly_raised, auto_resolved)``."""
    now = now or dt.datetime.now(dt.UTC)
    conditions: list[AlertCondition] = []

    # -- flow: declared expectations vs the flow_stats rollup ----------------
    for candidate in await repo.list_flow_candidates():
        try:
            condition = await _evaluate_candidate(repo, candidate, now)
        except ExpectationError:
            # An unusable stored expectation must not kill the whole tick —
            # log loudly and keep evaluating the rest.
            logger.exception(
                "flow monitor: unusable expectation",
                extra={"association_id": candidate.id},
            )
            continue
        if condition:
            conditions.append(condition)

    # -- health: connections the sweep flipped to error ----------------------
    for connection in await repo.list_error_connections():
        conditions.append(
            AlertCondition(
                source="health",
                kind="connection_error",
                connection_id=connection.id,
                message=(
                    f"connection '{connection.name}' failing health checks: "
                    f"{connection.last_error or 'unknown error'}"
                ),
            )
        )

    # -- job_failure: dead deliveries / terminal ingest failures / backfills --
    for association_id, count in await repo.list_dead_delivery_counts():
        plural = "delivery" if count == 1 else "deliveries"
        conditions.append(
            AlertCondition(
                source="job_failure",
                kind="delivery_dead",
                association_id=association_id,
                message=f"{count} dead-lettered {plural} awaiting redelivery",
            )
        )
    for association_id, count in await repo.list_terminal_ingest_failures(
        ingest_max_retries
    ):
        plural = "file" if count == 1 else "files"
        conditions.append(
            AlertCondition(
                source="job_failure",
                kind="ingest_failed",
                association_id=association_id,
                message=f"{count} source {plural} failed terminally (retry budget spent)",
            )
        )
    for association_id, backfill_id, error in await repo.list_failed_backfills():
        conditions.append(
            AlertCondition(
                source="job_failure",
                kind="backfill_failed",
                association_id=association_id,
                message=f"backfill {backfill_id} failed: {error or 'unknown error'}",
            )
        )

    # -- job_failure: push items rejected at finalize (P7-H, Phase 7 §8) -----
    rejections = await repo.list_recent_push_rejections(push_lookback_seconds)
    if rejections:
        floors = await repo.latest_push_rejected_resolved_at()
        conditions.extend(
            evaluate_push_rejections(rejections, floors, push_lookback_seconds)
        )

    return await repo.sync_alerts(conditions, MONITOR_KINDS)


async def _evaluate_candidate(
    repo: FlowMonitorRepo, candidate: FlowCandidate, now: dt.datetime
) -> AlertCondition | None:
    if candidate.direction == "ingest":
        window = parse_ingest_expectation(candidate.expectation)
        if window is None:
            return None
        return evaluate_ingest_inactivity(candidate, window, now)
    window = parse_delivery_expectation(candidate.expectation)
    if window is None:
        return None
    outstanding = await repo.has_breaching_delivery(candidate.id, window)
    return evaluate_delivery_slo(candidate, window, outstanding)
