# Derived stac-auth-proxy image carrying the ADR 0015 write-policy factory.
#
# The upstream 1.2.0 wheel ships no Postgres driver, so a bare volume mount of
# the factory cannot work — this thin layer installs services/proxy-policy
# (which pulls psycopg[binary]) into the upstream image's site-packages.
#
# The base tag is PINNED (not :latest): the factory consumes an internal,
# undocumented context shape (Cql2BuildFilterMiddleware.__call__), so upstream
# bumps must be deliberate — bump the tag here AND in docker-compose.yml
# together, then re-run the P7-Z live confirm (ADR 0015).
#
# Build context is the REPO ROOT (set by infra/compose.auth-enforced.yml):
#   docker build -f infra/proxy-policy/Dockerfile .
FROM ghcr.io/developmentseed/stac-auth-proxy:v1.2.0@sha256:3f0969d16badabd0212c0e7c462209490841ad4a342b9da1c2953ae56da21c11

# Upstream runs as uid 1001 ("user"); install system-wide as root, then drop
# back. The base is python:3.13-slim, so pip is available and the package
# lands in the same /usr/local site-packages the proxy's deps live in.
USER root

COPY services/proxy-policy /opt/stac-higher/proxy-policy
RUN pip install --no-cache-dir /opt/stac-higher/proxy-policy

USER user
