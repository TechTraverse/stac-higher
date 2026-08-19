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
    Histogram,
    generate_latest,
)

__all__ = [
    "ALERTS",
    "DELIVERIES",
    "DELIVERY_BYTES",
    "DELIVERY_SECONDS",
    "INGEST_BYTES",
    "INGEST_EVENTS",
    "JOB_RUNS",
    "JOB_SECONDS",
    "METRICS_CONTENT_TYPE",
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

WEBHOOK_DELIVERIES = Counter(
    "pipeline_webhook_deliveries_total",
    "Webhook notification outcomes (M2-C ledger statuses)",
    ["outcome"],  # delivered | failed | dead | skipped
    registry=REGISTRY,
)
ALERTS = Counter(
    "pipeline_alerts_total",
    "Alert lifecycle events written by the flow monitor",
    ["event"],  # raised | auto_resolved
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
