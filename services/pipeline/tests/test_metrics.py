"""M2-H service telemetry: instrument wrapper, counters, /metrics exposition."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import metrics
from pipeline.health import create_health_app
from pipeline.jobs.heartbeat import HeartbeatState
from pipeline.queue.memory import InMemoryQueue


def counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def test_alerts_total_help_text_names_both_writers():
    """Item 7 (final-review fix wave): pipeline_alerts_total is written by
    both the flow monitor (jobs/monitor.py) and the image alert writer
    (jobs/image_scans.py); the help text must not claim only the former."""
    assert "flow monitor" in metrics.ALERTS._documentation
    assert "image alert" in metrics.ALERTS._documentation


class TestInstrumentHandler:
    async def test_counts_ok_runs_and_observes_duration(self):
        async def handler(timestamp: int) -> None:
            pass

        wrapped = metrics.instrument_handler(handler, "pipeline.test_ok")
        before = counter_value(metrics.JOB_RUNS, job="pipeline.test_ok", outcome="ok")
        await wrapped(timestamp=1)
        await wrapped(timestamp=2)
        assert (
            counter_value(metrics.JOB_RUNS, job="pipeline.test_ok", outcome="ok")
            == before + 2
        )

    async def test_counts_errors_and_reraises(self):
        async def handler() -> None:
            raise RuntimeError("boom")

        wrapped = metrics.instrument_handler(handler, "pipeline.test_err")
        with pytest.raises(RuntimeError):
            await wrapped()
        assert (
            counter_value(metrics.JOB_RUNS, job="pipeline.test_err", outcome="error")
            == 1.0
        )
        assert (
            counter_value(metrics.JOB_RUNS, job="pipeline.test_err", outcome="ok")
            == 0.0
        )

    async def test_wraps_sync_handlers_too(self):
        calls: list[int] = []

        def handler(timestamp: int) -> None:
            calls.append(timestamp)

        wrapped = metrics.instrument_handler(handler, "pipeline.test_sync")
        await wrapped(timestamp=9)
        assert calls == [9]
        assert (
            counter_value(metrics.JOB_RUNS, job="pipeline.test_sync", outcome="ok")
            == 1.0
        )


def test_metrics_endpoint_serves_exposition_format():
    metrics.DELIVERIES.labels(outcome="delivered").inc()
    metrics.INGEST_EVENTS.labels(stage="settled_file").inc(3)

    client = TestClient(create_health_app(InMemoryQueue(), heartbeat_state=HeartbeatState()))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert 'pipeline_deliveries_total{outcome="delivered"}' in body
    assert 'pipeline_ingest_events_total{stage="settled_file"}' in body
    assert "pipeline_job_seconds" in body  # histogram family is registered


async def test_instrument_handler_tracks_jobs_in_flight():
    import asyncio

    from pipeline.metrics import instrument_handler, render_metrics

    release = asyncio.Event()

    async def handler():
        await release.wait()

    wrapped = instrument_handler(handler, "jobs.inflight")
    task = asyncio.create_task(wrapped())
    await asyncio.sleep(0)
    assert b'pipeline_jobs_in_flight{job="jobs.inflight"} 1.0' in render_metrics()
    release.set()
    await task
    assert b'pipeline_jobs_in_flight{job="jobs.inflight"} 0.0' in render_metrics()
