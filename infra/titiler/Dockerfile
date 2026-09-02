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
