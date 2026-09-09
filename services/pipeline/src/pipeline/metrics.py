"""Prometheus exposition for the pipeline service (M2-H, ROADMAP §8).

One module-level registry (never the client library's global default — tests
import this module repeatedly) holding:

- ``pipeline_job_runs_total{job, outcome}`` / ``pipeline_job_seconds{job}`` —
  every queue task and periodic tick, instrumented centrally where the
  production backend registers handlers (the in-memory test backend stays
  bare so unit tests observe handlers unwrapped), covering new jobs
  automatically.
- ingest stage counters (``files``/``items``/``bytes``/``failures``) —
  incremented at the same choke points as the M2-A flow_stats hooks.
- delivery counters + latency histogram — incremented at the worker's
  terminal transitions (delivered / failed / dead).
- webhook + alert counters — incremented in the notify / monitor jobs.

Scrape target: ``GET :8083/metrics``. No scraper ships in docker-compose
(spec §8) — the endpoint is curl-verifiable and operator-facing numbers reach
the UI through flow_stats instead.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST as METRICS_CONTENT_TYPE
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

__all__ = [
    "ALERTS",
    "DELIVERIES",
    "DELIVERY_BYTES",
    "DELIVERY_SECONDS",
    "FINALIZE_BYTES",
    "FINALIZE_ITEMS",
    "INGEST_BYTES",
    "INGEST_EVENTS",
    "JOB_RUNS",
    "JOB_SECONDS",
    "METRICS_CONTENT_TYPE",
    "PGSTAC_QUEUE_DEPTH",
    "PGSTAC_QUEUE_DRAIN_FAILURES",
    "PGSTAC_QUEUE_OLDEST_SECONDS",
    "PGSTAC_QUEUE_QUERIES",
    "REGISTRY",
    "WEBHOOK_DELIVERIES",
    "instrument_handler",
    "render_metrics",
]

REGISTRY = CollectorRegistry()

JOB_RUNS = Counter(
    "pipeline_job_runs_total",
    "Queue task / periodic tick executions",
    ["job", "outcome"],  # outcome: ok | error
    registry=REGISTRY,
)
JOB_SECONDS = Histogram(
    "pipeline_job_seconds",
    "Queue task / periodic tick duration",
    ["job"],
    registry=REGISTRY,
)

INGEST_EVENTS = Counter(
    "pipeline_ingest_events_total",
    "Ingest stage events (files settled by DISCOVER, items itemized, terminal failures)",
    ["stage"],  # settled_file | itemized_item | failed
    registry=REGISTRY,
)
INGEST_BYTES = Counter(
    "pipeline_ingest_bytes_total",
    "Bytes settled/itemized through ingest",
    registry=REGISTRY,
)

DELIVERIES = Counter(
    "pipeline_deliveries_total",
    "Delivery terminal transitions",
    ["outcome"],  # delivered | failed | dead
    registry=REGISTRY,
)
DELIVERY_BYTES = Counter(
    "pipeline_delivery_bytes_total",
    "Bytes written to delivery destinations (successful deliveries)",
    registry=REGISTRY,
)
DELIVERY_SECONDS = Histogram(
    "pipeline_delivery_seconds",
    "Wall-clock duration of one item delivery (success path)",
    registry=REGISTRY,
)

FINALIZE_ITEMS = Counter(
    "pipeline_finalize_items_total",
    "Finalize per-item terminal outcomes (Phase 7 §9). Labeled by producer so "
    "Phase 9 process runs get their telemetry for free (the ADR 0014 seam).",
    # outcome: upserted | rejected, plus the §6.3 no-op paths:
    #   stale_claim  — claim on an already-terminal/claimed ledger row
    #   superseded   — the item no longer references the claimed session
    #   item_missing — the item vanished between the event and finalize
    ["producer", "outcome"],
    registry=REGISTRY,
)
FINALIZE_BYTES = Counter(
    "pipeline_finalize_bytes_total",
    "Bytes moved staging → canonical by finalize (Phase 7 §9)",
    ["producer"],
    registry=REGISTRY,
)

WEBHOOK_DELIVERIES = Counter(
    "pipeline_webhook_deliveries_total",
    "Webhook notification outcomes (M2-C ledger statuses)",
    ["outcome"],  # delivered | failed | dead | skipped
    registry=REGISTRY,
)
# --- Phase 9 processes (M5-E, §8) ------------------------------------------
# Per-JOB run/duration/outcome already comes free from instrument_handler; the
# counters below are about RUNS (the domain object), which is a different
# thing from the job that drove them — a job that succeeds can carry a run
# that died.
PROCESS_RUNS = Counter(
    "pipeline_process_runs_total",
    "Process run terminal outcomes (Phase 9 §6 ledger statuses)",
    ["outcome"],  # succeeded | dead | failed | queued (infrastructure requeue)
    registry=REGISTRY,
)
PROCESS_RUN_SECONDS = Histogram(
    "pipeline_process_run_seconds",
    "Wall-clock duration of a process run, executor launch to exit",
    registry=REGISTRY,
)
PROCESS_OUTPUT_ITEMS = Counter(
    "pipeline_process_output_items_total",
    "Items a process run published (Phase 9 §2 / ADR 0014)",
    registry=REGISTRY,
)
PROCESS_ORPHANS_REAPED = Counter(
    "pipeline_process_orphan_containers_reaped_total",
    "Run containers a dead worker left behind, removed by the reaper (M3-W-1)",
    registry=REGISTRY,
)
PROCESS_RATE_DEFERRALS = Counter(
    "pipeline_process_rate_deferrals_total",
    "Runs deferred by the §7 per-process run-rate ceiling",
    registry=REGISTRY,
)

ALERTS = Counter(
    "pipeline_alerts_total",
    "Alert lifecycle events written by the flow monitor",
    ["event"],  # raised | auto_resolved
    registry=REGISTRY,
)

# --- M3-A pgstac query queue --------------------------------------------------
# `use_queue` is a SESSION GUC on the writer's connections, so nothing outside
# the process can see it is in effect — except the queue it feeds. Depth and
# age together are that evidence, and a drainer that silently stops shows up
# here as a rising age — which matters more than a performance number: a
# partition queued but not yet drained is not merely slower to search, it is
# ABSENT from datetime-ordered STAC search results until it drains (I-114).
# A stopped drainer turns that one-tick blind window unbounded.
PGSTAC_QUEUE_DEPTH = Gauge(
    "pipeline_pgstac_query_queue_depth",
    "Statements waiting in pgstac.query_queue after the last drain tick",
    registry=REGISTRY,
)
PGSTAC_QUEUE_OLDEST_SECONDS = Gauge(
    "pipeline_pgstac_query_queue_oldest_seconds",
    "Age of the oldest statement in pgstac.query_queue after the last drain tick (0 when empty)",
    registry=REGISTRY,
)
PGSTAC_QUEUE_QUERIES = Counter(
    "pipeline_pgstac_query_queue_queries_total",
    "Queued pgstac statements executed by the pipeline's drain tick",
    ["outcome"],  # ok | error (pgstac records the error in query_queue_history)
    registry=REGISTRY,
)
PGSTAC_QUEUE_DRAIN_FAILURES = Counter(
    "pipeline_pgstac_query_queue_drain_failures_total",
    "Drain ticks whose CALL pgstac.run_queued_queries() did not complete "
    "(connection refused, permissions, a transaction-block error). Distinct "
    "from PGSTAC_QUEUE_QUERIES{outcome=\"error\"}, which counts individual "
    "QUEUED STATEMENTS pgstac executed and recorded an error for: one is "
    "'the drainer is broken', the other is 'a statement failed', and an "
    "operator needs to tell them apart.",
    registry=REGISTRY,
)


def instrument_handler(
    func: Callable[..., Awaitable[None] | None], name: str
) -> Callable[..., Awaitable[None]]:
    """Wrap a queue handler with run/duration/outcome metrics.

    Returns an async callable either way (both backends accept async or sync
    handlers); errors are counted and re-raised so backend retry semantics
    are untouched.
    """

    @wraps(func)
    async def wrapped(*args: Any, **kwargs: Any) -> None:
        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception:
            JOB_RUNS.labels(job=name, outcome="error").inc()
            raise
        finally:
            JOB_SECONDS.labels(job=name).observe(time.monotonic() - start)
        JOB_RUNS.labels(job=name, outcome="ok").inc()

    return wrapped


def render_metrics() -> bytes:
    """The exposition-format payload for GET /metrics."""
    return generate_latest(REGISTRY)
