# cube-server: tiles + EDR for registered virtual Icechunk cubes
# (virtual cube spec §8.1, ADR 0022).
#
# Build context is THIS directory (docker-compose.yml, containers.yml), not the
# repo root: the image needs pyproject.toml + uv.lock as well as the package,
# and infra/cube-server/.dockerignore keeps tests and the venv out.
#   docker build infra/cube-server
#
# Same digest-pinned bases as services/pipeline/Dockerfile; move them together.
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim@sha256:5ae92e4d35b8d586d50ddf4aba6ecdd9f744237284d31c86bf45077e4e17e4bd AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY stac_higher_cube_server ./stac_higher_cube_server
# --reinstall-package: uv caches a local project's built wheel keyed on
# pyproject.toml alone, so behind this cache mount a code change would install
# the previous wheel (tests/test_dockerfile.py).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --reinstall-package stac-higher-cube-server

FROM python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

# libexpat1: rasterio's wheel links it (xpublish-tiles imports rasterio); the
# slim base doesn't ship it — the pipeline image installs it for the same reason.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system cube && useradd --system --gid cube --home-dir /app cube
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# NUMBA_CACHE_DIR: xpublish-tiles JIT-compiles with numba; site-packages is not
# writable by the non-root user, so its on-disk cache goes to /tmp. Same for
# matplotlib's config/font cache (imported by xpublish-tiles).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8086
USER cube
EXPOSE 8086
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8086\")}/health', timeout=4)"]
CMD ["cube-server"]
