"""Producer-neutral object-store seam for the finalize steps.

The steps need four primitives over the platform bucket — head / get / copy /
delete — expressed as a small protocol so unit tests drive the steps against
an in-memory fake. The production implementation wraps
``pipeline.storage.platform`` over the boto3 client (synchronous — the steps
call these via ``asyncio.to_thread``, matching the rest of the pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError

from pipeline.storage import platform


@dataclass(frozen=True)
class ObjectStat:
    etag: str
    size: int


class ObjectStore(Protocol):
    """Synchronous object primitives (wrap calls in ``asyncio.to_thread``)."""

    def head(self, key: str) -> ObjectStat | None:
        """ETag + size, or ``None`` when the object does not exist."""
        ...

    def get(self, key: str) -> bytes: ...

    def copy(self, src_key: str, dest_key: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def list_keys(self, prefix: str) -> list[str]:
        """Every object key under ``prefix``.

        Push finalize never needs this — the client declares its filenames at
        mint time. A process run does: user code decides what it wrote, so the
        outputs must be discovered rather than declared (ADR 0014).
        """
        ...


@dataclass
class PlatformObjectStore:
    """The platform-bucket implementation over an egress-pinned boto3 client."""

    client: Any
    bucket: str

    def head(self, key: str) -> ObjectStat | None:
        try:
            etag, size = platform.head_object(self.client, self.bucket, key)
        except ClientError as exc:  # pragma: no cover - boto error-shape plumbing
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectStat(etag=etag, size=size)

    def get(self, key: str) -> bytes:
        return platform.get_object(self.client, self.bucket, key)

    def copy(self, src_key: str, dest_key: str) -> None:
        platform.copy_object(self.client, self.bucket, src_key, dest_key)

    def delete(self, key: str) -> None:
        platform.delete_object(self.client, self.bucket, key)

    def list_keys(self, prefix: str) -> list[str]:
        return platform.list_keys(self.client, self.bucket, prefix)
