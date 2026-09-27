"""Object storage for the scanner: the run-scoped STS credential the drain
minted (spec §6.2, the ADR 0014 path) arrives in the standard AWS variables,
and the bucket in STAC_HIGHER_OUTPUT_BUCKET. Writes succeed only under the
scan's own prefix; the credential makes that true, not this code."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ObjectStore:  # pragma: no cover
        import boto3
        from botocore.client import Config

        env = dict(os.environ if env is None else env)
        endpoint = env.get("AWS_ENDPOINT_URL_S3") or env.get("AWS_ENDPOINT_URL") or None
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=env.get("AWS_REGION") or "us-east-1",
            aws_access_key_id=env.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=env.get("AWS_SESSION_TOKEN"),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if endpoint else "auto"},
            ),
        )
        return cls(client, env["STAC_HIGHER_OUTPUT_BUCKET"])

    def put_file(self, key: str, path: Path, content_type: str = "application/json") -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def put_json(self, key: str, doc: dict[str, Any]) -> None:
        body = json.dumps(doc, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json"
        )

    def get_file(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))
