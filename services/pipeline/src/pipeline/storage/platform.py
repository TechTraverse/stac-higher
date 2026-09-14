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
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol
from urllib.parse import urlparse

import boto3
from botocore.client import Config

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.ingest.raster_io import RasterLocation


class S3Like(Protocol):
    """The slice of a boto3 S3 client the platform primitives use."""

    def get_paginator(self, operation_name: str) -> Any: ...
    def delete_objects(self, **kwargs: Any) -> Any: ...
    def put_object(self, **kwargs: Any) -> Any: ...
    def get_object(self, **kwargs: Any) -> Any: ...
    def head_object(self, **kwargs: Any) -> Any: ...
    def copy_object(self, **kwargs: Any) -> Any: ...
    def upload_fileobj(self, Fileobj: Any, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def copy(self, CopySource: dict[str, str], Bucket: str, Key: str, **kwargs: Any) -> None: ...


def pinned_endpoint_url(
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
    endpoint_url = pinned_endpoint_url(
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


@dataclass(frozen=True)
class PlatformS3Access:
    """What a GDAL session needs to read the platform bucket in place (M3-C):
    the PINNED endpoint (egress parity with `build_platform_client`) and the
    platform's own keys. Frozen and never logged."""

    endpoint_url: str | None
    region: str
    access_key: str
    secret_key: str
    force_path_style: bool


def platform_s3_access(settings: Settings) -> PlatformS3Access:
    return PlatformS3Access(
        endpoint_url=pinned_endpoint_url(
            settings.staging_s3_endpoint, settings.staging_s3_region, settings.egress_allow_hosts
        ),
        region=settings.staging_s3_region,
        access_key=settings.staging_s3_access_key,
        secret_key=settings.staging_s3_secret_key,
        force_path_style=settings.staging_s3_force_path_style,
    )


def gdal_endpoint(endpoint_url: str | None) -> str | None:
    """`AWSSession(endpoint_url=...)` wants ``host[:port]`` with no scheme."""
    if not endpoint_url:
        return None
    parsed = urlparse(endpoint_url)
    return parsed.netloc or None


def gdal_session_options(
    endpoint_url: str | None, force_path_style: bool, gdal_cachemax_mb: int | None
) -> dict[str, str]:
    """The `rasterio.Env` kwargs a custom endpoint needs. rasterio refuses raw
    `AWS_*` options except these two (S-E caveat): plaintext endpoints need
    `AWS_HTTPS=NO`, path-style ones `AWS_VIRTUAL_HOSTING=FALSE`."""
    options: dict[str, str] = {}
    if endpoint_url and urlparse(endpoint_url).scheme == "http":
        options["AWS_HTTPS"] = "NO"
    if endpoint_url and force_path_style:
        options["AWS_VIRTUAL_HOSTING"] = "FALSE"
    if gdal_cachemax_mb is not None:
        options["GDAL_CACHEMAX"] = str(gdal_cachemax_mb)
    return options


def raster_location(
    access: PlatformS3Access, bucket: str, key: str, *, gdal_cachemax_mb: int
) -> RasterLocation:
    """`/vsis3/<bucket>/<key>` under the platform's session (copy-mode EXTRACT)."""
    from rasterio.session import AWSSession

    session = AWSSession(
        aws_access_key_id=access.access_key,
        aws_secret_access_key=access.secret_key,
        region_name=access.region,
        endpoint_url=gdal_endpoint(access.endpoint_url),
    )
    return RasterLocation(
        uri=f"/vsis3/{bucket}/{key}",
        session=session,
        options=gdal_session_options(
            access.endpoint_url, access.force_path_style, gdal_cachemax_mb
        ),
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


def get_object(client: S3Like, bucket: str, key: str) -> bytes:
    """Read the object at ``key`` back as bytes (ingest EXTRACT reads what FETCH
    stored). Pure over an injected client; synchronous boto3 — wrap in
    ``asyncio.to_thread`` on the event loop. Fully buffered (ISSUES I-19).
    """
    resp = client.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def head_object(client: S3Like, bucket: str, key: str) -> tuple[str, int]:
    """Quote-stripped ETag + size of the object at ``key`` — the server-side
    copy path's fingerprint source (delivery B-ii; no byte read). Pure over an
    injected client; synchronous boto3 — wrap in ``asyncio.to_thread``."""
    resp = client.head_object(Bucket=bucket, Key=key)
    return (resp.get("ETag") or "").strip('"'), int(resp["ContentLength"])


def copy_object(client: S3Like, bucket: str, src_key: str, dest_key: str) -> None:
    """Same-bucket server-side ``CopyObject`` — finalize's staging→canonical
    move primitive (Phase 7 §6.1 step 4). No bytes stream through the worker,
    so the I-19 buffering ceiling does not apply to the move. Pure over an
    injected client; synchronous boto3 — wrap in ``asyncio.to_thread``."""
    client.copy_object(
        Bucket=bucket,
        Key=dest_key,
        CopySource={"Bucket": bucket, "Key": src_key},
    )


def upload_stream(
    client: S3Like, bucket: str, key: str, body: BinaryIO, *, chunk_bytes: int, concurrency: int
) -> tuple[str, int]:
    """Streamed multipart upload of ``body`` (M3-C): boto3's transfer manager
    reads `chunk_bytes` parts with at most `concurrency` in flight, so worker
    memory is bounded by their product, not by the object. Returns the sha256
    hex digest and byte count of what went through. Synchronous — wrap in
    ``asyncio.to_thread``."""
    from boto3.s3.transfer import TransferConfig

    from pipeline.ingest.transfer import HashingStream

    hashing = HashingStream(body)
    client.upload_fileobj(
        hashing,
        bucket,
        key,
        Config=TransferConfig(
            multipart_threshold=chunk_bytes,
            multipart_chunksize=chunk_bytes,
            max_concurrency=concurrency,
            use_threads=True,
        ),
    )
    return hashing.hexdigest(), hashing.size


def copy_from_bucket(
    client: S3Like,
    src_bucket: str,
    src_key: str,
    bucket: str,
    key: str,
    *,
    chunk_bytes: int,
    concurrency: int,
) -> None:
    """Server-side copy from ANOTHER bucket on the platform endpoint into the
    platform bucket (M3-C FETCH fast path). boto3's managed `copy` switches to
    multipart copy above the threshold, so objects over 5 GB work. Raises when
    the platform keys cannot read ``src_bucket`` — the caller streams instead.
    Synchronous — wrap in ``asyncio.to_thread``."""
    from boto3.s3.transfer import TransferConfig

    client.copy(
        {"Bucket": src_bucket, "Key": src_key},
        bucket,
        key,
        Config=TransferConfig(
            multipart_threshold=chunk_bytes,
            multipart_chunksize=chunk_bytes,
            max_concurrency=concurrency,
        ),
    )


def delete_object(client: S3Like, bucket: str, key: str) -> None:
    """Delete ONE object — finalize's post-move staged-original removal (the
    §6.4 named deletion call site: only ever invoked after the canonical copy
    is checksum-verified and the item is upserted). Pure over an injected
    client; synchronous boto3 — wrap in ``asyncio.to_thread``."""
    client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key}]})


def list_keys(client: S3Like, bucket: str, prefix: str) -> list[str]:
    """Every object key under ``prefix``, paginated.

    Pure over an injected client (no network in tests). Callers on the event
    loop wrap it in ``asyncio.to_thread``. Unbounded by design at this layer —
    the caller bounds it (a process run's output prefix is one run's work);
    a cap here would silently truncate someone's outputs.
    """
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def cleanup_expired(
    client: S3Like,
    bucket: str,
    prefix: str,
    cutoff: dt.datetime,
    *,
    protected_prefixes: frozenset[str] = frozenset(),
) -> int:
    """Delete objects under ``prefix`` last modified before ``cutoff``.

    Pure over an injected client (no network in tests). Returns the number of
    objects deleted. ``cutoff`` must be timezone-aware (boto3 ``LastModified``
    is tz-aware UTC). ``protected_prefixes`` (Phase 7 §4.1 — the ledger clock):
    keys under any of these prefixes are never deleted regardless of mtime —
    the staged_uploads ledger row's age, not object mtime, governs a live
    upload session's expiry.
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
            and not any(obj["Key"].startswith(p) for p in protected_prefixes)
        ]
        if batch:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    return deleted


def delete_prefix(client: S3Like, bucket: str, prefix: str) -> int:
    """Delete EVERY object under ``prefix`` (asset GC's collector primitive —
    M2-F, ADR 0011). Pure over an injected client; synchronous boto3 — wrap in
    ``asyncio.to_thread``. Returns the number of objects deleted; a prefix
    with nothing under it (reference-mode item, already-collected key) is a
    normal zero, not an error.
    """
    if not prefix or prefix == "/":  # never let a mangled mark empty the bucket
        raise ValueError(f"refusing to delete unscoped prefix: {prefix!r}")
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        batch = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if batch:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)
    return deleted
