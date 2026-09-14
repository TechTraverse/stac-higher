"""How FETCH moves bytes (M3-C, spec §3 / S-E).

Three strategies, tried in this order per member:

1. **Server-side copy** — when the source is an s3 bucket the platform's own
   credentials can read on the same endpoint (`can_server_side_copy`, the gate
   the delivery path already uses in the other direction): `CopyObject`,
   zero bytes through the worker, 7.5x faster than today (S-E).
2. **Streamed multipart** — otherwise, or when the copy fails (the platform
   keys may not be able to read the source bucket): the adapter's `open()`
   stream is hashed on the way through and uploaded with boto3's transfer
   manager, whose buffers are bounded by `chunk_bytes x concurrency`.
3. The buffered `get()` is what `StorageAdapter.open()` falls back to for
   SFTP/FTP (I-83) — same code path here, the adapter decides.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from pipeline.config import (
    DEFAULT_FETCH_CHUNK_BYTES,
    DEFAULT_FETCH_TRANSFER_CONCURRENCY,
    Settings,
)
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.delivery.transfer import can_server_side_copy


@dataclass(frozen=True)
class TransferPolicy:
    server_side_copy: bool = False
    chunk_bytes: int = DEFAULT_FETCH_CHUNK_BYTES
    concurrency: int = DEFAULT_FETCH_TRANSFER_CONCURRENCY


def transfer_policy(adapter: StorageAdapter, settings: Settings) -> TransferPolicy:
    """Decided once per FETCH job from the adapter and the platform endpoint."""
    return TransferPolicy(
        server_side_copy=can_server_side_copy(
            adapter.protocol, adapter.endpoint, settings.staging_s3_endpoint
        ),
        chunk_bytes=settings.fetch_chunk_bytes,
        concurrency=settings.fetch_transfer_concurrency,
    )


class HashingStream:
    """A read-only wrapper that sha256s everything read through it. Exposes
    only `read`, so boto3 treats it as non-seekable and buffers per part.

    `read()` gets called two different ways depending on which s3transfer
    path a given upload takes: the multipart path always passes an explicit
    amount (bounded by `chunk_bytes`), but the NON-multipart path — taken
    whenever the body is under `multipart_threshold`, i.e. `chunk_bytes` —
    calls `read()` with no argument at all
    (`s3transfer.upload.UploadNonSeekableInputManager.get_put_object_body`).
    An unbounded call is drained here in bounded pieces rather than by one
    `self._raw.read()`, so a real `StreamingBody` is never pulled whole into
    RAM; in practice s3transfer only reaches this branch below the multipart
    threshold, so the drain is bounded by that threshold in the FETCH path,
    not by the object."""

    def __init__(self, raw: BinaryIO, *, drain_chunk_bytes: int = 1 << 20) -> None:
        self._raw = raw
        self._sha = hashlib.sha256()
        self.size = 0
        self._drain_chunk_bytes = drain_chunk_bytes

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            parts: list[bytes] = []
            while chunk := self._raw.read(self._drain_chunk_bytes):
                self._sha.update(chunk)
                self.size += len(chunk)
                parts.append(chunk)
            return b"".join(parts)
        chunk = self._raw.read(n)
        self._sha.update(chunk)
        self.size += len(chunk)
        return chunk

    def hexdigest(self) -> str:
        return self._sha.hexdigest()
