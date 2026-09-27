"""adapter_for — build the right StorageAdapter for a connection row.

Keyed on ``protocol``. ``ssh`` and ``sftp`` share :class:`SftpAdapter` (the SSH
transport carries the SFTP subsystem). ``stac-api`` is reserved and raises.
``registry`` holds pull credentials and raises too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.adapters.ftp import FtpAdapter
from pipeline.connections.adapters.ftps import FtpsAdapter
from pipeline.connections.adapters.s3 import S3Adapter
from pipeline.connections.adapters.sftp import SftpAdapter


def adapter_for(
    connection_row: Mapping[str, Any],
    credentials: dict[str, Any],
    allow_hosts: frozenset[str] = frozenset(),
) -> StorageAdapter:
    """Construct an adapter from a ``connections`` row + decrypted credentials.

    ``connection_row`` needs at least ``protocol`` and ``config`` (a dict).
    """
    protocol = connection_row["protocol"]
    config = connection_row.get("config") or {}

    if protocol == "s3":
        return S3Adapter(config, credentials, allow_hosts=allow_hosts)
    if protocol in ("ssh", "sftp"):
        return SftpAdapter(config, credentials, protocol=protocol, allow_hosts=allow_hosts)
    if protocol == "ftp":
        return FtpAdapter(config, credentials, allow_hosts=allow_hosts)
    if protocol == "ftps":
        return FtpsAdapter(config, credentials, allow_hosts=allow_hosts)
    if protocol == "registry":
        raise NotImplementedError(
            "registry connections hold image pull credentials; they carry no files"
        )
    if protocol == "stac-api":
        raise NotImplementedError("reserved for a future release")
    raise ValueError(f"unknown connection protocol: {protocol!r}")
