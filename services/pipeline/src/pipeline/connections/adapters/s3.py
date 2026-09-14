"""S3 adapter — boto3 driven, run off the event loop via ``asyncio.to_thread``.

Honors a custom ``endpoint`` (e.g. MinIO), ``region``, and
``force_path_style``. ``anonymous: true`` in the config (G-1) sends unsigned
requests — public buckets such as NODD's need no credentials. ``test()``
performs a HEAD-bucket (falling back to a 1-key list) so it proves both
reachability and auth without listing the world.

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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import urlparse

import boto3
from botocore import UNSIGNED
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from pipeline.connections.adapters.base import FileEntry, StorageAdapter, TestResult
from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.ingest.raster_io import RasterLocation
from pipeline.storage.platform import gdal_endpoint, gdal_session_options


@dataclass(frozen=True)
class S3Config:
    """The parsed s3 connection ``config`` (cross-runtime contract —
    ``tests/contract-fixtures/s3-connection-config.json``). Lenient reader:
    unknown keys are ignored; the app's Zod schema is the strict writer."""

    bucket: str
    region: str | None = None
    endpoint: str | None = None
    force_path_style: bool = False
    #: Public bucket: sign nothing, credentials may be empty (G-1).
    anonymous: bool = False


def parse_s3_config(raw: Any) -> S3Config:
    if not isinstance(raw, dict):
        raise ValueError("s3 config must be an object")
    bucket = raw.get("bucket")
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("s3 config.bucket is required")
    anonymous = raw.get("anonymous", False)
    if not isinstance(anonymous, bool):
        raise ValueError("s3 config.anonymous must be a boolean")
    force_path_style = raw.get("force_path_style", False)
    if not isinstance(force_path_style, bool):
        raise ValueError("s3 config.force_path_style must be a boolean")
    region = raw.get("region")
    endpoint = raw.get("endpoint")
    return S3Config(
        bucket=bucket,
        region=str(region) if region else None,
        endpoint=str(endpoint) if endpoint else None,
        force_path_style=force_path_style,
        anonymous=anonymous,
    )


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
        cfg = parse_s3_config(config)
        self._bucket = cfg.bucket
        self._region = cfg.region
        self._endpoint = cfg.endpoint
        self._force_path_style = cfg.force_path_style
        self._anonymous = cfg.anonymous
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
        config_kwargs: dict[str, Any] = {
            "s3": {"addressing_style": "path" if self._force_path_style else "auto"},
            "connect_timeout": 10,
            "read_timeout": 30,
            "retries": {"max_attempts": 2},
        }
        if self._anonymous:
            # Public bucket (NODD): botocore skips signing entirely. Keys are
            # deliberately NOT passed even if the envelope has some — an
            # "anonymous" connection must behave identically whatever was
            # stored, or the flag would lie.
            config_kwargs["signature_version"] = UNSIGNED
            return boto3.client(
                "s3",
                region_name=self._region,
                endpoint_url=endpoint_url,
                config=Config(**config_kwargs),
            )
        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=endpoint_url,
            aws_access_key_id=self._creds.get("access_key_id"),
            aws_secret_access_key=self._creds.get("secret_access_key"),
            aws_session_token=self._creds.get("session_token"),
            config=Config(**config_kwargs),
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

    async def copy_object_from(self, src_bucket: str, src_key: str, dst_path: str) -> None:
        """Server-side CopyObject from another bucket on the SAME endpoint into
        this adapter's bucket (delivery §6.4 — no bytes through the worker).
        Callers gate on ``delivery.transfer.can_server_side_copy`` and fall
        back to streaming when the copy fails (e.g. these credentials cannot
        read ``src_bucket``)."""
        endpoint_url = self._pinned_endpoint()

        def _copy() -> None:
            client = self._make_client(endpoint_url)
            client.copy_object(
                Bucket=self._bucket,
                Key=dst_path,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )

        await asyncio.to_thread(_copy)

    @property
    def endpoint(self) -> str | None:
        return self._endpoint

    def copy_source(self, path: str) -> tuple[str, str] | None:
        return (self._bucket, path)

    def gdal_location(
        self, path: str, *, options: Mapping[str, str] | None = None
    ) -> RasterLocation:
        """`/vsis3/<bucket>/<path>` under THIS connection's credentials (M3-C).
        The session is built here and handed out opaque — the decrypted keys
        now also live inside a GDAL session (spec §5 review point). The
        endpoint is the pinned one, like every boto3 client this adapter makes."""
        from rasterio.session import AWSSession

        endpoint_url = self._pinned_endpoint()
        if self._anonymous:
            session = AWSSession(
                aws_unsigned=True,
                region_name=self._region,
                endpoint_url=gdal_endpoint(endpoint_url),
            )
        else:
            session = AWSSession(
                aws_access_key_id=self._creds.get("access_key_id"),
                aws_secret_access_key=self._creds.get("secret_access_key"),
                aws_session_token=self._creds.get("session_token"),
                region_name=self._region,
                endpoint_url=gdal_endpoint(endpoint_url),
            )
        merged = gdal_session_options(endpoint_url, self._force_path_style, None)
        merged.update(options or {})
        return RasterLocation(uri=f"/vsis3/{self._bucket}/{path}", session=session, options=merged)

    async def open(self, path: str) -> BinaryIO:
        """A streaming read of ``path`` (botocore `StreamingBody`): nothing is
        buffered beyond what the caller reads. The GetObject call itself runs
        in a thread; the returned body is read by FETCH's upload thread."""
        endpoint_url = self._pinned_endpoint()

        def _open() -> BinaryIO:
            client = self._make_client(endpoint_url)
            return client.get_object(Bucket=self._bucket, Key=path)["Body"]

        return await asyncio.to_thread(_open)

    async def put_atomic(self, path: str, data: bytes) -> None:
        # S3 PUT is atomically visible; skip the base .part+move dance.
        await self.put(path, data)
