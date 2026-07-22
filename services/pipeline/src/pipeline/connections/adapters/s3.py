"""S3 adapter — boto3 driven, run off the event loop via ``asyncio.to_thread``.

Honors a custom ``endpoint`` (e.g. MinIO), ``region``, and
``force_path_style``. ``test()`` performs a HEAD-bucket (falling back to a
1-key list) so it proves both reachability and auth without listing the world.

Egress hardening: :func:`resolve_pinned` resolves+validates the endpoint host
once (fail-closed on any internal/metadata address). For a **custom http**
endpoint (e.g. MinIO — no TLS) the endpoint URL is rewritten to the validated
IP so a DNS rebind between check and connect cannot reach an internal address.
For an **https** custom endpoint or the default AWS endpoint the hostname is
kept (rewriting to an IP would break TLS SNI / certificate validation); the
pre-connect ``resolve_pinned`` check still applies, leaving only a narrow
rebind window against a public/TLS endpoint (low risk).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from pipeline.connections.adapters.base import FileEntry, StorageAdapter, TestResult
from pipeline.connections.egress import EgressBlocked, resolve_pinned


def _endpoint_host(endpoint: str | None, region: str | None) -> str:
    """The hostname the egress policy must vet before any S3 call.

    With a custom endpoint, use its host. Otherwise use AWS's public regional
    endpoint (a global/public address — the policy will allow it).
    """
    if endpoint:
        parsed = urlparse(endpoint)
        host = parsed.hostname
        if not host:
            raise EgressBlocked(f"S3 endpoint has no host: {endpoint!r}")
        return host
    if region:
        return f"s3.{region}.amazonaws.com"
    return "s3.amazonaws.com"


class S3Adapter(StorageAdapter):
    protocol = "s3"

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, Any],
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self._bucket = config["bucket"]
        self._region = config.get("region")
        self._endpoint = config.get("endpoint")
        self._force_path_style = bool(config.get("force_path_style", False))
        self._creds = credentials
        self._allow_hosts = allow_hosts

    def _host(self) -> str:
        return _endpoint_host(self._endpoint, self._region)

    def public_object_url(self, path: str) -> str:
        """Construct a stable object URL from config (no credentials read).
        Path-style for a custom endpoint or force_path_style (MinIO); otherwise
        virtual-hosted against the AWS regional host."""
        key = path.lstrip("/")
        if self._endpoint or self._force_path_style:
            base = (self._endpoint or f"https://s3.{self._region}.amazonaws.com").rstrip("/")
            return f"{base}/{self._bucket}/{key}"
        region = self._region or "us-east-1"
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"

    def _pinned_endpoint(self) -> str | None:
        """Resolve+validate the endpoint host and return the ``endpoint_url`` to
        pass to boto3 (IP-pinned for custom http endpoints, unchanged
        otherwise). Raises :class:`EgressBlocked` for a disallowed host.
        """
        pinned = resolve_pinned(self._host(), self._allow_hosts)
        if not self._endpoint:
            # default AWS endpoint: keep boto3's own resolution (public host).
            return None
        parsed = urlparse(self._endpoint)
        if pinned and parsed.scheme == "http":
            # plaintext (MinIO) — safe to dial the validated IP; no TLS/SNI.
            netloc = pinned[0] if not parsed.port else f"{pinned[0]}:{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
        # https custom endpoint or allowlisted host: keep the hostname so TLS
        # validates; resolve_pinned already rejected internal addresses.
        return self._endpoint

    def _make_client(self, endpoint_url: str | None) -> Any:
        boto_config = Config(
            s3={"addressing_style": "path" if self._force_path_style else "auto"},
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 2},
        )
        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=endpoint_url,
            aws_access_key_id=self._creds.get("access_key_id"),
            aws_secret_access_key=self._creds.get("secret_access_key"),
            aws_session_token=self._creds.get("session_token"),
            config=boto_config,
        )

    async def test(self) -> TestResult:
        started = time.monotonic()
        try:
            endpoint_url = self._pinned_endpoint()
        except EgressBlocked as exc:
            return {"ok": False, "message": str(exc)}

        def _probe() -> None:
            client = self._make_client(endpoint_url)
            try:
                client.head_bucket(Bucket=self._bucket)
            except ClientError:
                # HEAD may be denied even when the bucket is usable; a bounded
                # list still proves reachability + auth.
                client.list_objects_v2(Bucket=self._bucket, MaxKeys=1)

        try:
            await asyncio.to_thread(_probe)
        except (ClientError, BotoCoreError) as exc:
            return {"ok": False, "message": f"S3 test failed: {exc}"}
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "message": f"bucket {self._bucket!r} reachable",
            "latency_ms": latency_ms,
        }

    async def list(self, prefix: str = "") -> list[FileEntry]:
        endpoint_url = self._pinned_endpoint()

        def _list() -> list[FileEntry]:
            client = self._make_client(endpoint_url)
            paginator = client.get_paginator("list_objects_v2")
            entries: list[FileEntry] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    last_modified = obj.get("LastModified")
                    # boto3 quotes ETags; a multipart etag has a "-N" suffix but
                    # still changes with content, so it's a valid change signal.
                    etag = (obj.get("ETag") or "").strip('"') or None
                    entries.append(
                        FileEntry(
                            path=key,
                            size=obj.get("Size"),
                            mtime=last_modified.timestamp() if last_modified else None,
                            etag=etag,
                            # zero-byte "folder" placeholder keys end in "/".
                            is_dir=key.endswith("/"),
                        )
                    )
            return entries

        return await asyncio.to_thread(_list)

    async def get(self, path: str) -> bytes:
        endpoint_url = self._pinned_endpoint()

        def _get() -> bytes:
            client = self._make_client(endpoint_url)
            resp = client.get_object(Bucket=self._bucket, Key=path)
            return resp["Body"].read()

        return await asyncio.to_thread(_get)

    async def put(self, path: str, data: bytes) -> None:
        endpoint_url = self._pinned_endpoint()

        def _put() -> None:
            client = self._make_client(endpoint_url)
            client.put_object(Bucket=self._bucket, Key=path, Body=data)

        await asyncio.to_thread(_put)

    async def delete(self, path: str) -> None:
        endpoint_url = self._pinned_endpoint()

        def _delete() -> None:
            client = self._make_client(endpoint_url)
            client.delete_object(Bucket=self._bucket, Key=path)

        await asyncio.to_thread(_delete)

    async def move(self, src: str, dst: str) -> None:
        endpoint_url = self._pinned_endpoint()

        def _move() -> None:
            client = self._make_client(endpoint_url)
            client.copy_object(
                Bucket=self._bucket,
                Key=dst,
                CopySource={"Bucket": self._bucket, "Key": src},
            )
            client.delete_object(Bucket=self._bucket, Key=src)

        await asyncio.to_thread(_move)

    async def put_atomic(self, path: str, data: bytes) -> None:
        # S3 PUT is atomically visible; skip the base .part+move dance.
        await self.put(path, data)
