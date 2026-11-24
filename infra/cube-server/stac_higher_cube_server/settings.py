"""cube-server environment (virtual cube spec §8.1)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

_REQUIRED = (
    "CUBE_DB_DSN",
    "CUBE_S3_ENDPOINT",
    "PLATFORM_ASSET_BUCKET",
)


@dataclass(frozen=True)
class Settings:
    #: a read-only role: SELECT on the cube_sinks columns of migration 033
    db_dsn: str
    s3_endpoint: str
    #: both None = the AWS default credential chain (Pod Identity / IRSA)
    s3_access_key_id: str | None
    s3_secret_access_key: str | None = field(repr=False)
    platform_bucket: str
    s3_region: str = "us-east-1"
    poll_seconds: float = 15
    cors_origins: tuple[str, ...] = ("*",)
    port: int = 8086

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        missing = [name for name in _REQUIRED if not env.get(name)]
        if missing:
            raise ValueError(f"cube-server: missing required env {', '.join(missing)}")
        key_id = (env.get("CUBE_S3_ACCESS_KEY_ID") or "").strip() or None
        secret = (env.get("CUBE_S3_SECRET_ACCESS_KEY") or "").strip() or None
        if (key_id is None) != (secret is None):
            raise ValueError(
                "cube-server: set CUBE_S3_ACCESS_KEY_ID and CUBE_S3_SECRET_ACCESS_KEY "
                "together, or neither (default AWS credential chain)"
            )
        origins = tuple(
            o.strip() for o in env.get("CUBE_CORS_ORIGINS", "*").split(",") if o.strip()
        )
        return cls(
            db_dsn=env["CUBE_DB_DSN"],
            s3_endpoint=env["CUBE_S3_ENDPOINT"],
            s3_access_key_id=key_id,
            s3_secret_access_key=secret,
            platform_bucket=env["PLATFORM_ASSET_BUCKET"],
            s3_region=env.get("CUBE_S3_REGION") or "us-east-1",
            poll_seconds=float(env.get("CUBE_POLL_SECONDS") or 15),
            cors_origins=origins or ("*",),
            port=int(env.get("PORT") or 8086),
        )
