# Platform-built process runtime image (ADR 0013 slice-1 scope, spec §4).
#
# Slice 1 runs `inline_python` on THIS image only — user-supplied images are
# refused at the app's write gate, which keeps image supply-chain review and
# registry policy out of the first accreditation surface while still
# exercising the full executor boundary.
#
# The blessed library set is deliberately small. Every addition is a
# dependency the platform vouches for inside its own trust boundary, and
# "operators will ask for more packages" is answered by versioning THIS image,
# never by per-process installs at run time (spec §14 risk list).
# Debian trixie, pinned by index digest (GitHub #60). This image is scanned
# under infra/image-policy/default.json like any user image (C-5). On bookworm
# it FAILED that policy (fixed CRITICAL CVE-2026-75803 in libssl3/openssl,
# plus unfixed CRITICALs in perl-base/libc/sqlite older than 30 days that no
# bookworm rebuild clears); on trixie it scans 0 CRITICAL. Dependabot bumps
# the digest; `rasterio`'s wheel and the stactools variant's smoke were
# re-proved on this base (plan 2026-09-30-c-pin-base-images).
FROM python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

# No pip cache, no root, no shell tools the run does not need. The executor
# also drops all capabilities and sets no-new-privileges at create time; this
# is the same posture from the image side.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# rasterio's manylinux wheel bundles GDAL but NOT libexpat, which GDAL's driver
# registry links against — on this slim base `import rasterio` died with
# `libexpat.so.1: cannot open shared object file`, so EVERY process using the
# blessed raster stack failed at import (found 2026-09-02 by the demo pipeline;
# nothing in the unit suites imports rasterio inside this image). Installed
# without recommends and with the apt lists dropped in the same layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "boto3==1.43.49" \
      "pystac==1.15.1" \
      "rio-stac==0.12.0" \
      "rasterio>=1.5,<2" \
 && useradd --create-home --uid 10001 runner

COPY entrypoint.py /opt/stac-higher/entrypoint.py

USER runner
WORKDIR /home/runner

# The revision's source arrives base64 in STAC_HIGHER_PROCESS_CODE_B64 rather
# than as a bind mount: under docker-out-of-docker a bind path is resolved by
# the DAEMON on the host, not inside the pipeline container, so a mount would
# be wrong or dangerous — and this way the socket proxy never needs to permit
# mounts at all. See pipeline/process/docker_executor.py.
ENTRYPOINT ["python", "/opt/stac-higher/entrypoint.py"]
