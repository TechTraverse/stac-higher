"""Platform object-store client + staging cleanup primitive (Phase 3, §5.3).

Egress parity with the adapter layer: the endpoint host is vetted through
:func:`resolve_pinned` before any call. For a plaintext (http) custom endpoint —
MinIO — the URL is rewritten to the validated IP (DNS-rebind defence), unless the
host is allowlisted (the compose-internal ``minio``, which resolves privately by
design), in which case the hostname is kept. This mirrors ``S3Adapter`` but for
the platform's own bucket rather than a user connection.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol
from urllib.parse import urlparse

import boto3
from botocore.client import Config

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked, resolve_pinned


class S3Like(Protocol):
    """The slice of a boto3 S3 client the platform primitives use."""

    def get_paginator(self, operation_name: str) -> Any: ...
    def delete_objects(self, **kwargs: Any) -> Any: ...
    def put_object(self, **kwargs: Any) -> Any: ...


def _pinned_endpoint_url(
    endpoint: str | None,
    region: str,
    allow_hosts: frozenset[str],
) -> str | None:
    """Vet the endpoint host and return the ``endpoint_url`` for boto3.

    ``None`` => no custom endpoint (real AWS): let boto3 resolve the regional
    host. A custom http endpoint is IP-pinned; an allowlisted or https host
    keeps its hostname. Raises :class:`EgressBlocked` for a disallowed host.
    """
    host = urlparse(endpoint).hostname if endpoint else f"s3.{region}.amazonaws.com"
    if not host:
        raise EgressBlocked(f"staging S3 endpoint has no host: {endpoint!r}")
    pinned = resolve_pinned(host, allow_hosts)
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if pinned and parsed.scheme == "http":
        netloc = pinned[0] if not parsed.port else f"{pinned[0]}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    return endpoint


def build_platform_client(settings: Settings) -> Any:
    """Construct a boto3 S3 client for the platform bucket (egress-pinned)."""
    endpoint_url = _pinned_endpoint_url(
        settings.staging_s3_endpoint,
        settings.staging_s3_region,
        settings.egress_allow_hosts,
    )
    boto_config = Config(
        s3={
            "addressing_style": "path"
            if settings.staging_s3_force_path_style
            else "auto"
        },
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 2},
    )
    return boto3.client(
        "s3",
        region_name=settings.staging_s3_region,
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.staging_s3_access_key,
        aws_secret_access_key=settings.staging_s3_secret_key,
        config=boto_config,
    )


def put_object(
    client: S3Like,
    bucket: str,
    key: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> None:
    """Write ``data`` to the platform bucket at ``key`` (ingest FETCH → canonical).

    Pure over an injected client (no network in tests). Synchronous boto3 —
    callers on the event loop wrap it in ``asyncio.to_thread``. The bytes are
    fully buffered by the caller; streaming/multipart for envelope-scale assets
    is deferred (ISSUES I-19).
    """
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    client.put_object(**kwargs)


def cleanup_expired(
    client: S3Like,
    bucket: str,
    prefix: str,
    cutoff: dt.datetime,
) -> int:
    """Delete objects under ``prefix`` last modified before ``cutoff``.

    Pure over an injected client (no network in tests). Returns the number of
    objects deleted. ``cutoff`` must be timezone-aware (boto3 ``LastModified``
    is tz-aware UTC).
    """
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        # list_objects_v2 caps a page at 1000 keys and DeleteObjects caps a call
        # at 1000, so one page is at most one delete — delete as we go rather
        # than buffering every expired key across all pages in memory.
        batch = [
            {"Key": obj["Key"]}
            for obj in page.get("Contents", [])
            if obj["LastModified"] < cutoff
        ]
        if batch:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    return deleted
