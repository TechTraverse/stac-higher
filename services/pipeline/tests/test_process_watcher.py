"""K-4 run watcher: the Docker events stream (parsing, framing, a real local
socket), the reconnecting watcher loop, and main.py's wiring."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

import pytest

from pipeline.process.docker_executor import (
    EVENT_FILTERS,
    RUN_ID_LABEL,
    RUN_KIND_LABEL,
    DockerExecutor,
    _parse_event,
)
from pipeline.process.executor import RESYNC, ExecutorUnavailable, RunEvent
from pipeline.process.http_stream import iter_body_lines, read_http_head
from pipeline.process.watcher import run_watcher


def _event(run_id="r1", *, kind=None, status="die", type_="container"):
    attributes = {RUN_ID_LABEL: run_id}
    if kind:
        attributes[RUN_KIND_LABEL] = kind
    return {"Type": type_, "Action": status, "Actor": {"ID": "c1", "Attributes": attributes}}


def test_parse_event_reads_the_run_id_handle_and_kind():
    assert _parse_event(_event(kind="image_scan")) == RunEvent("r1", "c1", "image_scan")
    assert _parse_event(_event()) == RunEvent("r1", "c1", "process")


@pytest.mark.parametrize(
    "obj",
    [
        {"Type": "container", "Actor": {"ID": "c", "Attributes": {"name": "x"}}},
        _event(type_="network"),
        "not a dict",
        {"Type": "container", "Actor": "nope"},
        {"Type": "container", "Actor": {"ID": "c", "Attributes": {RUN_ID_LABEL: ""}}},
    ],
)
def test_parse_event_ignores_strangers_and_garbage(obj):
    assert _parse_event(obj) is None


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def _lines(data: bytes, *, chunked: bool) -> list[bytes]:
    return [line async for line in iter_body_lines(_reader(data), chunked=chunked)]


async def test_body_lines_decode_chunked_framing():
    one, two, three = b'{"a":1}', b'{"b":2}', b'{"c":3}'
    split = one + b"\n" + two[:3]
    rest = two[3:] + b"\n" + three + b"\n"
    body = (
        f"{len(split):x}\r\n".encode() + split + b"\r\n"
        + f"{len(rest):x}\r\n".encode() + rest + b"\r\n"
        + b"0\r\n\r\n"
    )
    assert await _lines(body, chunked=True) == [one, two, three]


async def test_body_lines_read_an_identity_body_to_eof():
    assert await _lines(b'{"a":1}\n\n{"b":2}', chunked=False) == [b'{"a":1}', b'{"b":2}']


async def test_read_http_head():
    status, headers = await read_http_head(
        _reader(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX: y\r\n\r\n")
    )
    assert status == 200 and headers["transfer-encoding"] == "chunked"


async def _serve(response: bytes):
    """A one-shot HTTP server on 127.0.0.1; returns (server, port, requests)."""
    requests: list[bytes] = []

    async def handle(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], requests


def _chunked(*objs) -> bytes:
    body = b""
    for obj in objs:
        line = json.dumps(obj).encode() + b"\n"
        body += f"{len(line):x}\r\n".encode() + line + b"\r\n"
    return b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body + b"0\r\n\r\n"


async def test_watch_yields_resync_first_then_events():
    server, port, requests = await _serve(
        _chunked(_event("r1"), {"Type": "network"}, _event("r2", kind="image_scan"))
    )
    async with server:
        executor = DockerExecutor(docker_host=f"tcp://127.0.0.1:{port}")
        assert executor.supports_watch
        events = [event async for event in executor.watch()]
    assert events == [RESYNC, RunEvent("r1", "c1"), RunEvent("r2", "c1", "image_scan")]
    request_line = requests[0].split(b"\r\n", 1)[0].decode()
    assert request_line.startswith("GET /v1.43/events?filters=")
    query = urllib.parse.urlparse(request_line.split(" ")[1]).query
    assert json.loads(urllib.parse.parse_qs(query)["filters"][0]) == EVENT_FILTERS


async def test_watch_raises_unavailable_on_a_refused_status():
    server, port, _requests = await _serve(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    async with server:
        executor = DockerExecutor(docker_host=f"tcp://127.0.0.1:{port}")
        with pytest.raises(ExecutorUnavailable, match="403"):
            async for _ in executor.watch():
                pass


async def test_watch_raises_unavailable_when_nothing_listens():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(ExecutorUnavailable):
        async for _ in DockerExecutor(docker_host=f"tcp://127.0.0.1:{port}").watch():
            pass


# -- the watcher loop -----------------------------------------------------------


def _stream(*batches):
    """A watch() factory: each call yields RESYNC then the next batch."""
    calls = iter(batches)

    async def watch():
        yield RESYNC
        for event in next(calls):
            if isinstance(event, Exception):
                raise event
            yield event

    return watch


async def test_each_connect_delivers_resync_then_events():
    seen: list[RunEvent] = []

    async def on_event(event):
        seen.append(event)

    await run_watcher(_stream([RunEvent("a")], [RunEvent("b")]), on_event,
                      reconnect_delay=0, max_connects=2)
    assert seen == [RESYNC, RunEvent("a"), RESYNC, RunEvent("b")]


async def test_a_failing_event_handler_does_not_drop_the_stream(caplog):
    seen: list[str | None] = []

    async def on_event(event):
        if event.run_id == "bad":
            raise RuntimeError("row exploded")
        seen.append(event.run_id)

    with caplog.at_level(logging.ERROR):
        await run_watcher(_stream([RunEvent("bad"), RunEvent("good")]), on_event,
                          reconnect_delay=0, max_connects=1)
    assert seen == [None, "good"]
    message = "run watcher could not apply an event"
    record = next(r for r in caplog.records if r.getMessage() == message)
    assert record.run_id == "bad"


async def test_a_dropped_stream_reconnects_after_the_delay():
    connects = 0

    async def watch():
        nonlocal connects
        connects += 1
        yield RESYNC
        raise ExecutorUnavailable("stream broke")

    async def on_event(event):
        return None

    await run_watcher(watch, on_event, reconnect_delay=0, max_connects=2)
    assert connects == 2


async def test_cancellation_propagates():
    async def watch():
        yield RESYNC
        await asyncio.Event().wait()

    async def on_event(event):
        return None

    task = asyncio.create_task(run_watcher(watch, on_event, reconnect_delay=0))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_stream_that_never_opens_backs_off(monkeypatch):
    """A deployment with no event source (I-160) must not log every 5 s."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def watch():
        raise ExecutorUnavailable("no proxy")
        yield  # pragma: no cover - makes this an async generator

    async def on_event(event):
        return None

    await run_watcher(watch, on_event, reconnect_delay=5, max_reconnect_delay=60,
                      max_connects=6)
    assert sleeps == [10, 20, 40, 60, 60]
