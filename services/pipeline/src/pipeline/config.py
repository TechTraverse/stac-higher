"""Environment-driven settings.

Env contract (documented in README.md):

- ``DATABASE_URL``  — Postgres DSN for the job queue.
  Default targets the compose-exposed pgstac instance from the host.
- ``HEALTH_PORT``   — port for the /health HTTP server.
- ``QUEUE_SCHEMA``  — PostgreSQL schema owned by Procrastinate.
- ``LOG_LEVEL``     — root log level.
- ``CREDENTIALS_MASTER_KEY`` — base64-encoded 32-byte AES-256-GCM key, shared
  with the app. Only the connection drain/health-sweep jobs need it; absence is
  tolerated at startup (those ticks fail loudly instead of killing the process).
- ``EGRESS_ALLOW_HOSTS`` — comma-separated allowlist of hostnames the egress
  policy permits even when they resolve to private/loopback addresses (e.g. the
  compose-internal test servers). Matched case-insensitively.

Platform object storage (Phase 3 — the platform's OWN bucket, MinIO locally /
S3 in cloud; distinct from per-connection endpoints):

- ``STAGING_S3_ENDPOINT`` — S3/MinIO endpoint URL; default is the compose MinIO.
  Set empty for real AWS (boto3 resolves the regional endpoint).
- ``STAGING_S3_REGION`` / ``STAGING_S3_ACCESS_KEY_ID`` /
  ``STAGING_S3_SECRET_ACCESS_KEY`` — client region + credentials.
- ``STAGING_BUCKET`` — platform bucket name.
- ``STAGING_S3_FORCE_PATH_STYLE`` — path-style addressing (MinIO needs it).
- ``STAGING_TTL_SECONDS`` — age after which a ``staging/`` upload is swept.
- ``ASSET_HREF_BASE`` — root-relative base path for asset hrefs the pipeline
  writes into ``item.assets[*].href`` (ingest EXTRACT/ITEMIZE). Must match the
  app's asset route prefix (default ``/api/assets``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DATABASE_URL = "postgresql://username:password@localhost:5433/postgis"
DEFAULT_HEALTH_PORT = 8083
DEFAULT_QUEUE_SCHEMA = "procrastinate"

# Platform storage defaults target the compose MinIO (minioadmin, bucket
# `stac-higher`); a cloud deployment overrides all of these via env.
DEFAULT_STAGING_S3_ENDPOINT = "http://minio:9000"
DEFAULT_STAGING_S3_REGION = "us-east-1"
DEFAULT_STAGING_S3_ACCESS_KEY = "minioadmin"
DEFAULT_STAGING_S3_SECRET_KEY = "minioadmin"
DEFAULT_STAGING_BUCKET = "stac-higher"
DEFAULT_STAGING_TTL_SECONDS = 86400  # 24h

DEFAULT_ASSET_HREF_BASE = "/api/assets"

# Ingest crash recovery (ISSUES I-52). A FETCH stalled longer than the stall
# threshold is presumed crashed (idempotent to re-run); failed rows retry after
# the cool-off, at most max-retries times. A `stored` row stalled longer than
# its threshold means ITEMIZE never landed despite queue retries (ISSUES I-55);
# it re-enters at `settled` against the same max-retries budget.
DEFAULT_INGEST_FETCH_STALL_SECONDS = 1800  # 30 min
DEFAULT_INGEST_FAILED_RETRY_SECONDS = 300  # 5 min cool-off
DEFAULT_INGEST_MAX_RETRIES = 3
DEFAULT_INGEST_STORED_STALL_SECONDS = 1800  # 30 min

# Delivery crash recovery (M2-0). A delivery_log row still `pending`/`delivering`
# this long after its last update is presumed crashed — the deliver job died
# between the pre-record and `deliver_item`, or a worker died mid-transfer. The
# stall sweep re-enters it into the retry path. Widen this for deployments whose
# single transfers legitimately run longer than the window.
DEFAULT_DELIVERY_STALL_SECONDS = 1800  # 30 min

# Retention & GC (M2-F, ADR 0011). Batch size bounds one sweep tick's work;
# large backlogs drain across the five-minute ticks.
DEFAULT_GC_BATCH_ITEMS = 500

# Webhook notification dispatch (M2-C). Attempts include the first; failed
# rows retry after the cool-off until the cap, then dead-letter (which raises
# a `webhook_failed` alert). A claim stranded `delivering` past the stall
# window is presumed crashed and re-enters the retry path.
DEFAULT_WEBHOOK_MAX_ATTEMPTS = 5
DEFAULT_WEBHOOK_RETRY_SECONDS = 60
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10
DEFAULT_WEBHOOK_STALL_SECONDS = 900  # 15 min


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw == "1" or raw.strip().lower() == "true"


def _parse_allow_hosts(raw: str | None) -> frozenset[str]:
    """Split a comma-separated host list into a lowercased set (empties dropped)."""
    if not raw:
        return frozenset()
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    health_port: int = DEFAULT_HEALTH_PORT
    queue_schema: str = DEFAULT_QUEUE_SCHEMA
    log_level: str = "INFO"
    #: None when unset — the drain/sweep jobs raise a clear error on their tick.
    credentials_master_key: str | None = None
    egress_allow_hosts: frozenset[str] = field(default_factory=frozenset)
    #: Platform object storage (Phase 3). Empty endpoint => real-AWS resolution.
    staging_s3_endpoint: str | None = DEFAULT_STAGING_S3_ENDPOINT
    staging_s3_region: str = DEFAULT_STAGING_S3_REGION
    staging_s3_access_key: str = DEFAULT_STAGING_S3_ACCESS_KEY
    staging_s3_secret_key: str = DEFAULT_STAGING_S3_SECRET_KEY
    staging_bucket: str = DEFAULT_STAGING_BUCKET
    staging_s3_force_path_style: bool = True
    staging_ttl_seconds: int = DEFAULT_STAGING_TTL_SECONDS
    asset_href_base: str = DEFAULT_ASSET_HREF_BASE
    #: Ingest crash recovery (I-52) — see the DEFAULT_INGEST_* constants.
    ingest_fetch_stall_seconds: int = DEFAULT_INGEST_FETCH_STALL_SECONDS
    ingest_failed_retry_seconds: int = DEFAULT_INGEST_FAILED_RETRY_SECONDS
    ingest_max_retries: int = DEFAULT_INGEST_MAX_RETRIES
    ingest_stored_stall_seconds: int = DEFAULT_INGEST_STORED_STALL_SECONDS
    #: Delivery crash recovery (M2-0) — see DEFAULT_DELIVERY_STALL_SECONDS.
    delivery_stall_seconds: int = DEFAULT_DELIVERY_STALL_SECONDS
    #: Retention & GC sweep batch size (M2-F).
    gc_batch_items: int = DEFAULT_GC_BATCH_ITEMS
    #: Webhook notification dispatch (M2-C) — see the DEFAULT_WEBHOOK_* constants.
    webhook_max_attempts: int = DEFAULT_WEBHOOK_MAX_ATTEMPTS
    webhook_retry_seconds: int = DEFAULT_WEBHOOK_RETRY_SECONDS
    webhook_timeout_seconds: int = DEFAULT_WEBHOOK_TIMEOUT_SECONDS
    webhook_stall_seconds: int = DEFAULT_WEBHOOK_STALL_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            database_url=env.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            health_port=int(env.get("HEALTH_PORT", str(DEFAULT_HEALTH_PORT))),
            queue_schema=env.get("QUEUE_SCHEMA", DEFAULT_QUEUE_SCHEMA),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            credentials_master_key=env.get("CREDENTIALS_MASTER_KEY") or None,
            egress_allow_hosts=_parse_allow_hosts(env.get("EGRESS_ALLOW_HOSTS")),
            staging_s3_endpoint=env.get("STAGING_S3_ENDPOINT", DEFAULT_STAGING_S3_ENDPOINT)
            or None,
            staging_s3_region=env.get("STAGING_S3_REGION", DEFAULT_STAGING_S3_REGION),
            staging_s3_access_key=env.get(
                "STAGING_S3_ACCESS_KEY_ID", DEFAULT_STAGING_S3_ACCESS_KEY
            ),
            staging_s3_secret_key=env.get(
                "STAGING_S3_SECRET_ACCESS_KEY", DEFAULT_STAGING_S3_SECRET_KEY
            ),
            staging_bucket=env.get("STAGING_BUCKET", DEFAULT_STAGING_BUCKET),
            staging_s3_force_path_style=_parse_bool(
                env.get("STAGING_S3_FORCE_PATH_STYLE"), True
            ),
            staging_ttl_seconds=int(
                env.get("STAGING_TTL_SECONDS", str(DEFAULT_STAGING_TTL_SECONDS))
            ),
            asset_href_base=env.get("ASSET_HREF_BASE", DEFAULT_ASSET_HREF_BASE),
            ingest_fetch_stall_seconds=int(
                env.get(
                    "INGEST_FETCH_STALL_SECONDS",
                    str(DEFAULT_INGEST_FETCH_STALL_SECONDS),
                )
            ),
            ingest_failed_retry_seconds=int(
                env.get(
                    "INGEST_FAILED_RETRY_SECONDS",
                    str(DEFAULT_INGEST_FAILED_RETRY_SECONDS),
                )
            ),
            ingest_max_retries=int(
                env.get("INGEST_MAX_RETRIES", str(DEFAULT_INGEST_MAX_RETRIES))
            ),
            delivery_stall_seconds=int(
                env.get(
                    "DELIVERY_STALL_SECONDS",
                    str(DEFAULT_DELIVERY_STALL_SECONDS),
                )
            ),
            ingest_stored_stall_seconds=int(
                env.get(
                    "INGEST_STORED_STALL_SECONDS",
                    str(DEFAULT_INGEST_STORED_STALL_SECONDS),
                )
            ),
            gc_batch_items=int(env.get("GC_BATCH_ITEMS", str(DEFAULT_GC_BATCH_ITEMS))),
            webhook_max_attempts=int(
                env.get("WEBHOOK_MAX_ATTEMPTS", str(DEFAULT_WEBHOOK_MAX_ATTEMPTS))
            ),
            webhook_retry_seconds=int(
                env.get("WEBHOOK_RETRY_SECONDS", str(DEFAULT_WEBHOOK_RETRY_SECONDS))
            ),
            webhook_timeout_seconds=int(
                env.get("WEBHOOK_TIMEOUT_SECONDS", str(DEFAULT_WEBHOOK_TIMEOUT_SECONDS))
            ),
            webhook_stall_seconds=int(
                env.get("WEBHOOK_STALL_SECONDS", str(DEFAULT_WEBHOOK_STALL_SECONDS))
            ),
        )
