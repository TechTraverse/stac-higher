"""A source connection, configured for both cube libraries (virtual cube spec §6.1).

The cube writer reads a NODD header with obstore (VirtualiZarr's parser) and
records a ``VirtualChunkContainer`` for Icechunk. Both come from the S3
connection of the reference-mode association that produced the item.

Always an explicit endpoint, always path style. Without an endpoint obstore
dials the virtual-hosted ``{bucket}.s3.{region}.amazonaws.com`` while the
egress check vets ``s3.{region}.amazonaws.com`` (spike Q7); with path style
the host the check vets is the host obstore dials. obstore gets the PINNED
endpoint: a plaintext endpoint is rewritten to its validated IP, as the S3
adapter does. The Icechunk container keeps the hostname, because it is
persisted in the repository config and only readers dial it.

v1 sources are anonymous (spec §13: the sink PUT refuses signed sources and
the cube server authorizes anonymous prefixes only), so a signed connection is
refused here rather than decrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import icechunk as ic
from obstore.store import S3Store

from pipeline.connections.adapters.s3 import parse_s3_config
from pipeline.connections.repo import ConnectionRow
from pipeline.storage.platform import pinned_endpoint_url

DEFAULT_REGION = "us-east-1"
REASON_SIGNED_SOURCE = "signed_source_unsupported"


class SourceConnectionError(Exception):
    """A connection the cube writer cannot read from (caller-safe message)."""


@dataclass(frozen=True)
class SourceLibs:
    """One source bucket, configured for both libraries."""

    #: the VirtualChunkContainer url_prefix, ``s3://{bucket}/`` (never ``s3://``)
    prefix: str
    #: the ObjectStoreRegistry key VirtualiZarr resolves source URLs against
    registry_key: str
    #: obstore store rooted at the bucket; dials the pinned endpoint
    store: Any
    #: Icechunk ObjectStoreConfig persisted on the container (hostname endpoint)
    container_store: Any
    #: Icechunk credentials that authorize the container when the repo opens
    credentials: Any

    def url(self, key: str) -> str:
        return f"{self.prefix}{key.lstrip('/')}"


def explicit_endpoint(endpoint: str | None, region: str | None) -> str:
    """The connection's endpoint, else AWS's regional endpoint (spec §6.1)."""
    if endpoint:
        return endpoint.rstrip("/")
    return f"https://s3.{region or DEFAULT_REGION}.amazonaws.com"


def libs_from_connection(connection: ConnectionRow, allow_hosts: frozenset[str]) -> SourceLibs:
    """Raises :class:`SourceConnectionError` for a connection the writer cannot
    use, and ``EgressBlocked`` for an endpoint the egress policy refuses."""
    if connection.protocol != "s3":
        raise SourceConnectionError(
            f"cube sources must be s3 connections, not {connection.protocol!r}"
        )
    try:
        cfg = parse_s3_config(connection.config)
    except ValueError as exc:
        raise SourceConnectionError(str(exc)) from exc
    if not cfg.anonymous:
        raise SourceConnectionError(REASON_SIGNED_SOURCE)
    region = cfg.region or DEFAULT_REGION
    endpoint = explicit_endpoint(cfg.endpoint, region)
    dial = pinned_endpoint_url(endpoint, region, allow_hosts) or endpoint
    store = S3Store(
        bucket=cfg.bucket,
        region=region,
        endpoint=dial,
        virtual_hosted_style_request=False,
        skip_signature=True,
        client_options={"allow_http": dial.startswith("http://")},
    )
    container_store = ic.s3_store(
        region=region,
        endpoint_url=endpoint,
        allow_http=endpoint.startswith("http://"),
        anonymous=True,
        force_path_style=True,
    )
    return SourceLibs(
        prefix=f"s3://{cfg.bucket}/",
        registry_key=f"s3://{cfg.bucket}",
        store=store,
        container_store=container_store,
        credentials=ic.s3_credentials(anonymous=True),
    )
