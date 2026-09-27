# The platform-built image scanner (container-images spec §6.1, ADR 0021).
#
# python:3.12-slim + pinned Syft and Grype release binaries (versions and
# per-architecture sha256 in the ARG block, verified at build) + the Grype
# vulnerability DB baked at build time, so an air-gapped deployment has a
# usable DB the day the image was built (spec §14 decision 5). It runs as a
# platform run through the pipeline's Executor seam with the process
# posture: its own container, limits, no platform credentials, and a
# run-scoped storage credential for its scan prefix (spec §6.2).
#
# Build (repo root):
#   docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner
# or:
#   docker build -t stac-higher-image-scanner:local services/image-scanner
FROM python:3.12-slim-bookworm

ARG SYFT_VERSION=1.52.0
ARG SYFT_SHA256_AMD64=caeedb81fb0491615f1ebd1761e4145d41ee86dd2cc7bf80669f9f5ad9d6133d
ARG SYFT_SHA256_ARM64=c46d5e4c28e12aa4c5becfaa343ef1c7f89045b6b895f2c21d471c62db09c706
ARG GRYPE_VERSION=0.119.0
ARG GRYPE_SHA256_AMD64=3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b
ARG GRYPE_SHA256_ARM64=29f0ec7c549ddb0e2b6a0ca714851f7399438afc399b80c12808e065edc9a8f8
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SYFT_VERSION=${SYFT_VERSION} \
    GRYPE_VERSION=${GRYPE_VERSION} \
    GRYPE_DB_CACHE_DIR=/opt/grype-db \
    GRYPE_DB_AUTO_UPDATE=false \
    GRYPE_DB_VALIDATE_AGE=false \
    GRYPE_CHECK_FOR_APP_UPDATE=false \
    SYFT_CHECK_FOR_APP_UPDATE=false \
    PYTHONPATH=/opt/stac-higher

# Release assets, checksum-verified; curl leaves in the same layer.
RUN set -eu; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
      amd64) syft_sum="$SYFT_SHA256_AMD64"; grype_sum="$GRYPE_SHA256_AMD64" ;; \
      arm64) syft_sum="$SYFT_SHA256_ARM64"; grype_sum="$GRYPE_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    cd /tmp; \
    curl -fsSLo syft.tar.gz "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_${arch}.tar.gz"; \
    echo "${syft_sum}  syft.tar.gz" | sha256sum -c -; \
    curl -fsSLo grype.tar.gz "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_${arch}.tar.gz"; \
    echo "${grype_sum}  grype.tar.gz" | sha256sum -c -; \
    tar -xzf syft.tar.gz -C /usr/local/bin syft; \
    tar -xzf grype.tar.gz -C /usr/local/bin grype; \
    rm -f syft.tar.gz grype.tar.gz; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# boto3 is the runtime image's pin (a test holds them equal). The DB is
# baked here; `STAC_HIGHER_DB_UPDATE` (set from the pipeline's
# `IMAGE_SCANNER_DB_UPDATE`) refreshes it at scan start where the deployment
# allows egress (spec §6.1).
RUN pip install --no-cache-dir "boto3==1.43.49" \
 && useradd --create-home --uid 10001 scanner \
 && mkdir -p /opt/grype-db \
 && grype db update \
 && chown -R scanner:scanner /opt/grype-db

COPY stac_higher_scanner /opt/stac-higher/stac_higher_scanner

USER scanner
WORKDIR /home/scanner
ENTRYPOINT ["python", "-m", "stac_higher_scanner.scan"]
