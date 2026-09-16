# Derived titiler-pgstac image carrying the stac-higher href mapper
# (GOES spec §7.1, ISSUES I-68).
#
# The base tag is PINNED and must move in lockstep with docs/serving.md. The
# wrapper patches rio-tiler 7's `_get_asset_info`; titiler-pgstac 3.x
# (rio-tiler 9) changed that method's return shape — re-run
# infra/titiler/tests before bumping.
#
# Build context is the REPO ROOT (set in docker-compose.yml):
#   docker build -f infra/titiler/Dockerfile .
FROM ghcr.io/stac-utils/titiler-pgstac:1.7.2

COPY infra/titiler/stac_higher_titiler /opt/stac-higher/titiler/stac_higher_titiler
ENV PYTHONPATH=/opt/stac-higher/titiler \
    MODULE_NAME=stac_higher_titiler.main \
    VARIABLE_NAME=app

# DS-0002 (trivy HIGH): the titiler-pgstac base defaults to root and ships no
# non-root account, so the user has to be created here before it can be used.
# Nothing binds a privileged port — the base CMD is gunicorn on :8000 and
# docker-compose.yml overrides it to uvicorn on :8084 — so dropping privileges
# costs nothing here, and it matches every other Dockerfile in this repo
# (app, pipeline, process-runtime and proxy-policy all set USER).
RUN useradd --system --create-home --uid 1001 titiler
USER titiler
