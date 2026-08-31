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
FROM python:3.12-slim-bookworm

# No pip cache, no root, no shell tools the run does not need. The executor
# also drops all capabilities and sets no-new-privileges at create time; this
# is the same posture from the image side.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

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
