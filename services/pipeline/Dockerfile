# services/pipeline — worker + scheduler + /health in one process (Phase 0).
# Build context: services/pipeline/

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# dependency layer (cached until pyproject/lock change)
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
# project layer
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
# libpq5: runtime C library for psycopg (pure-python build links against libpq).
# libexpat1: rasterio's bundled-GDAL wheels dynamically link libexpat at runtime
# (XML/GML drivers); without it `import rasterio` fails with
# `ImportError: libexpat.so.1` and in-container raster_auto EXTRACT can't run
# (surfaced by the first in-container scheduler-driven itemize — ISSUE I-35).
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libexpat1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system pipeline && useradd --system --gid pipeline pipeline
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HEALTH_PORT=8083
USER pipeline
EXPOSE 8083
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"HEALTH_PORT\", \"8083\")}/health', timeout=4)"]
# entrypoint applies the procrastinate schema idempotently, then runs the
# worker (with the periodic heartbeat) and the health server
CMD ["pipeline"]
