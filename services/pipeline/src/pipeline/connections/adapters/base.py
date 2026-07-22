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
    MLSD facts).

    ``path`` is **not uniform across adapters** (a known contract gap, ISSUES
    I-4): S3 returns the full key, SFTP/FTP the name relative to the listed
    prefix. Consumers must normalize it — ``ingest.discover.relative_source_path``
    /``source_fetch_path`` do this (strip/rejoin the prefix) so the divergence
    doesn't leak past DISCOVER. A future uniform (source-relative) contract would
    let that normalization be deleted.
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

    @abc.abstractmethod
    async def move(self, src: str, dst: str) -> None:
        """Rename ``src`` to ``dst`` on the destination (atomic where the
        protocol supports it). SFTP/FTP use a server-side rename; S3 has no
        rename primitive, so ``S3Adapter`` implements copy + delete."""

    async def put_atomic(self, path: str, data: bytes) -> None:
        """Write ``data`` so ``path`` never appears partially written.

        Default: write to ``path + ".part"`` then ``move`` it into place — the
        atomic-visibility pattern for streaming protocols (§6.4). ``S3Adapter``
        overrides this with a direct ``put`` (S3 objects become visible
        atomically on PUT, so the rename would only cost an extra copy+delete).
        """
        tmp = f"{path}.part"
        await self.put(tmp, data)
        await self.move(tmp, path)

    def public_object_url(self, path: str) -> str:
        """Stable, credential-free URL for a source object (reference storage
        mode, §5.1). Only object-store adapters implement this; the base raises
        so a non-s3 adapter reaching reference mode fails loudly (the app also
        restricts reference associations to s3)."""
        raise NotImplementedError(
            f"{self.protocol} connections do not support storage_mode 'reference'"
        )

    async def copy_object_from(self, src_bucket: str, src_key: str, dst_path: str) -> None:
        """Server-side copy from another bucket on the same endpoint into this
        adapter's storage (delivery §6.4). Only object-store adapters implement
        this; callers gate on ``delivery.transfer.can_server_side_copy``, which
        is s3-only — the base raises so a misrouted call fails loudly."""
        raise NotImplementedError(
            f"{self.protocol} connections do not support server-side copy"
        )
