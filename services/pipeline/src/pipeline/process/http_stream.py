"""A minimal HTTP/1.1 response reader over an asyncio stream (K-4).

The executors' long-lived streams — Docker's ``GET /events`` today, a
Kubernetes watch or ``pods/log?follow`` with K-5 — are newline-delimited JSON
over a chunked (or close-delimited) response. ``urllib`` cannot read one
incrementally from async code, and a client library is a dependency these
security-critical components do not need, so this is the whole of it: read
the head, then yield body lines as they arrive. No redirects, no keep-alive,
no compression: callers send ``Connection: close`` and never ask for them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

#: A head or a chunk-size line longer than this is not a server we talk to.
MAX_LINE_BYTES = 64 * 1024


class HttpStreamError(Exception):
    """The response was not well-formed HTTP/1.1."""


async def _readline(reader: asyncio.StreamReader) -> bytes:
    line = await reader.readline()
    if len(line) > MAX_LINE_BYTES:
        raise HttpStreamError("line too long")
    return line


async def read_http_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    """The status code and the headers (lower-cased names)."""
    status_line = await _readline(reader)
    parts = status_line.decode("latin-1").split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise HttpStreamError(f"not an HTTP status line: {status_line[:80]!r}")
    headers: dict[str, str] = {}
    while True:
        line = await _readline(reader)
        if not line:
            raise HttpStreamError("connection closed inside the response head")
        if line in (b"\r\n", b"\n"):
            break
        name, sep, value = line.decode("latin-1").partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers


def is_chunked(headers: dict[str, str]) -> bool:
    return "chunked" in headers.get("transfer-encoding", "").lower()


async def _iter_chunks(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    while True:
        size_line = await _readline(reader)
        if not size_line:
            return  # the peer closed mid-stream: the caller reconnects
        size_text = size_line.split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as err:
            raise HttpStreamError(f"bad chunk size {size_text[:20]!r}") from err
        if size == 0:
            return
        data = await reader.readexactly(size)
        await reader.readexactly(2)  # the CRLF after each chunk
        yield data


async def _iter_identity(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    while True:
        data = await reader.read(65536)
        if not data:
            return
        yield data


async def iter_body_lines(
    reader: asyncio.StreamReader, *, chunked: bool
) -> AsyncIterator[bytes]:
    """Yield each non-empty newline-terminated body line as it completes. A
    line split across chunks is joined; several lines in one chunk are each
    yielded. A trailing unterminated line is yielded at the end."""
    pending = b""
    source = _iter_chunks(reader) if chunked else _iter_identity(reader)
    async for data in source:
        pending += data
        *lines, pending = pending.split(b"\n")
        for line in lines:
            if line.strip():
                yield line.strip()
        if len(pending) > MAX_LINE_BYTES * 16:
            raise HttpStreamError("a body line is too long")
    if pending.strip():
        yield pending.strip()
