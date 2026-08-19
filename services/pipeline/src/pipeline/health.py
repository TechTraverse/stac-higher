"""Health endpoint: GET /health on HEALTH_PORT (default 8083).

200 when the queue backend (and therefore the database, for Procrastinate)
is reachable; 503 otherwise. Suitable as a compose/K8s healthcheck target.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from pipeline import __version__
from pipeline.jobs.heartbeat import STATE, HeartbeatState
from pipeline.metrics import METRICS_CONTENT_TYPE, render_metrics
from pipeline.queue.interface import QueueBackend, QueueConnectionError


def create_health_app(queue: QueueBackend, heartbeat_state: HeartbeatState = STATE) -> FastAPI:
    app = FastAPI(title="stac-higher-pipeline", version=__version__, docs_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        queue_error: str | None = None
        try:
            await queue.check_connection()
        except QueueConnectionError as exc:
            queue_error = str(exc)

        reachable = queue_error is None
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
            },
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        # M2-H (spec §8): Prometheus exposition. No scraper in compose —
        # curl-verifiable; see pipeline/metrics.py for the instrument map.
        return Response(content=render_metrics(), media_type=METRICS_CONTENT_TYPE)

    return app
