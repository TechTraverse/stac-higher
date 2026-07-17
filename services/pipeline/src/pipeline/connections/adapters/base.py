"""StorageAdapter ABC and the shared TestResult shape.

Every protocol adapter opens outbound connections only after passing its config
host through the egress policy. ``test()`` returns a JSON-serializable dict that
the drain/health-sweep jobs persist into ``connection_checks.result`` and the
parent connection's health columns.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TypedDict


class TestResult(TypedDict, total=False):
    ok: bool
    message: str
    #: SSH-family only: the observed server host key (stable string form). The
    #: app never sees this — it is consumed by TOFU and surfaced only as a
    #: fingerprint on the connection resource.
    host_key: str
    latency_ms: int


@dataclass(frozen=True)
class FileEntry:
    """A single entry from :meth:`StorageAdapter.list`.

    Carries the metadata the ingest DISCOVER stage needs: ``size`` and a change
    signal (``mtime`` and/or ``etag``). The settled-check compares these across
    two polls; a change after itemization means a new product version. Fields
    are ``None`` when the protocol doesn't expose them (e.g. FTP servers without
    MLSD facts). ``path`` is the entry name relative to the listed prefix, as
    each adapter's native listing returns it.
    """

    path: str
    #: size in bytes, or None if the server didn't report it
    size: int | None = None
    #: last-modified time as epoch seconds (UTC), or None
    mtime: float | None = None
    #: content etag (S3), or None — a strong change signal when present
    etag: str | None = None
    #: True for directory/collection entries (not ingestible files)
    is_dir: bool = False


class StorageAdapter(abc.ABC):
    """Uniform async surface over S3/SFTP/FTP/FTPS endpoints."""

    #: protocol identifier this adapter serves ("s3", "sftp", ...)
    protocol: str

    @abc.abstractmethod
    async def test(self) -> TestResult:
        """Probe connectivity + auth. Never raises for an expected failure —
        returns ``{ok: False, message}``; only truly unexpected bugs propagate.
        """

    @abc.abstractmethod
    async def list(self, prefix: str = "") -> list[FileEntry]:
        """List entries under ``prefix`` (relative to root) with size/mtime/etag
        metadata where the protocol exposes it."""

    @abc.abstractmethod
    async def get(self, path: str) -> bytes:
        """Fetch the bytes at ``path``."""

    @abc.abstractmethod
    async def put(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``."""

    @abc.abstractmethod
    async def delete(self, path: str) -> None:
        """Delete the object/entry at ``path``."""
