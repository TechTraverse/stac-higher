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
        )
