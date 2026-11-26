"""Health endpoint: GET /health on HEALTH_PORT (default 8083).

200 when the queue backend (and therefore the database, for Procrastinate)
is reachable; 503 otherwise. Suitable as a compose/K8s healthcheck target.

``image_policy`` (C-2) and ``executor`` (K-5: ``{name, reachable, error}``,
from ``process.factory.ExecutorProbe``) are informational: neither turns the
answer into a 503, because the readiness probe would otherwise pull a
pipeline whose ingest is fine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from pipeline import __version__
from pipeline.db.pool import pool_stats
from pipeline.images.policy import ImagePolicyError, image_policy_path, load_image_policy
from pipeline.jobs.heartbeat import STATE, HeartbeatState
from pipeline.metrics import METRICS_CONTENT_TYPE, render_metrics
from pipeline.queue.interface import QueueBackend, QueueConnectionError


def image_policy_status(path: Path | None = None) -> dict[str, Any]:
    """C-2 (spec §7.2): the policy fails closed, so an operator must be able to
    see WHY no image is scanned or launched. Informational: a broken policy
    does not make the pipeline unhealthy (inline processes are unaffected)."""
    where = path or image_policy_path()
    try:
        policy = load_image_policy(where)
    except ImagePolicyError as err:
        return {"file": str(where), "ok": False, "version": None, "error": str(err)}
    return {"file": str(where), "ok": True, "version": policy.version, "error": None}


def create_health_app(
    queue: QueueBackend,
    heartbeat_state: HeartbeatState = STATE,
    *,
    image_policy_file: Path | None = None,
    executor_status: Callable[[], Awaitable[dict[str, Any]]] | None = None,
) -> FastAPI:
    app = FastAPI(title="stac-higher-pipeline", version=__version__, docs_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        queue_error: str | None = None
        try:
            await queue.check_connection()
        except QueueConnectionError as exc:
            queue_error = str(exc)

        reachable = queue_error is None
        executor = await executor_status() if executor_status is not None else None
        return JSONResponse(
            status_code=200 if reachable else 503,
            content={
                "service": "pipeline",
                "version": __version__,
                "status": "ok" if reachable else "degraded",
                "queue": {
                    "backend": queue.name,
                    "reachable": reachable,
                    "error": queue_error,
                },
                "heartbeat": heartbeat_state.as_dict(),
                # M3-B: the repo pool is session-scoped and otherwise
                # invisible from outside the process. `{}` means no pool has
                # been opened yet, which is the truthful answer for a process
                # that has not touched Postgres. Cumulative counters
                # (connections_num, requests_num, …) appear only once non-zero.
                "db_pool": pool_stats(),
                # C-2: the image policy's file and whether it parses.
                "image_policy": image_policy_status(image_policy_file),
                # K-5: which process-run executor, and can we reach it.
                "executor": executor,
            },
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        # M2-H (spec §8): Prometheus exposition. No scraper in compose —
        # curl-verifiable; see pipeline/metrics.py for the instrument map.
        return Response(content=render_metrics(), media_type=METRICS_CONTENT_TYPE)

    return app
