# Derived stac-auth-proxy image carrying the ADR 0015 write-policy factory.
#
# The upstream wheel (through 1.4.0) ships no Postgres driver, so a bare
# volume mount of the factory cannot work — this thin layer installs
# services/proxy-policy (which pulls psycopg[binary]) into the upstream image's
# site-packages.
#
# The base tag is PINNED (not :latest): the factory consumes an internal,
# undocumented context shape (Cql2BuildFilterMiddleware.__call__), so upstream
# bumps must be deliberate — bump the tag here AND in docker-compose.yml
# together, then re-run the P7-Z live confirm (ADR 0015). The same tag also
# moves in lockstep with the `stac-auth-proxy` dev pin in
# services/proxy-policy/pyproject.toml (test_dev_pin_matches_the_dockerfile_base
# enforces that pairing), and with that extra's starlette / fastapi / pyjwt /
# cql2 pins, which equal upstream's uv.lock at the tag
# (test_dev_extra_runs_the_base_images_locked_version).
#
# The image also carries the AUTH_BEARER_AUDIENCE_CLAIM check (spec §8, R-3):
# its process is `python -m stac_higher_proxy_policy`, upstream's app with the
# auth middleware swapped for the claim-aware one.
#
# Build context is the REPO ROOT (set by infra/compose.auth-enforced.yml):
#   docker build -f infra/proxy-policy/Dockerfile .
FROM ghcr.io/developmentseed/stac-auth-proxy:v1.4.0@sha256:65e367a6bfb2f148a44d5688b82d24f055b2f3cd95979824fe2ae31512a84889

# Upstream runs as uid 1001 ("user"); install system-wide as root, then drop
# back. The base is python:3.13-slim, so the package lands in the same
# /usr/local site-packages the proxy's deps live in. Since v1.4.0 upstream
# removes pip from its runtime image (and says derived images should
# bootstrap it): ensurepip, install, then remove pip again so the runtime
# keeps upstream's no-pip posture.
USER root

COPY services/proxy-policy /opt/stac-higher/proxy-policy
RUN python -m ensurepip \
    && python -m pip install --no-cache-dir /opt/stac-higher/proxy-policy \
    && python -m pip uninstall -y pip

USER user

CMD ["python", "-m", "stac_higher_proxy_policy"]
