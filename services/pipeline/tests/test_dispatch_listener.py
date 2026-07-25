"""Slice C: the NOTIFY-woken dispatch wake loop (fake streams, no Postgres)."""

import asyncio

import pytest

from pipeline.dispatcher.listener import run_dispatch_listener

pytestmark = pytest.mark.asyncio


async def test_wakes_on_connect_and_per_notification():
    wakes = 0

    async def on_wake():
        nonlocal wakes
        wakes += 1

    async def stream():
        yield "connected"  # catch-up drain
        yield "n1"
        yield "n2"

    await run_dispatch_listener(
        "db", on_wake, stream_factory=stream, reconnect_delay=0, max_connects=1
    )
    assert wakes == 3


async def test_reconnects_after_stream_failure():
    factory_calls = 0
    wakes = 0

    async def on_wake():
        nonlocal wakes
        wakes += 1

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        first = factory_calls == 1

        async def gen():
            yield "connected"
            if first:
                raise RuntimeError("connection dropped")

        return gen()

    await run_dispatch_listener(
        "db", on_wake, stream_factory=factory, reconnect_delay=0, max_connects=2
    )
    assert factory_calls == 2
    assert wakes == 2  # one catch-up wake per (re)connect


async def test_on_wake_failure_reconnects_instead_of_dying():
    """A dispatch failure (e.g. queue down) must not kill the listener."""
    wakes = 0

    async def on_wake():
        nonlocal wakes
        wakes += 1
        if wakes == 1:
            raise RuntimeError("queue down")

    async def stream():
        yield "connected"

    await run_dispatch_listener(
        "db", on_wake, stream_factory=stream, reconnect_delay=0, max_connects=2
    )
    assert wakes == 2  # the second connect woke again after the failure


async def test_cancellation_propagates():
    started = asyncio.Event()

    async def on_wake():
        pass

    async def stream():
        yield "connected"
        started.set()
        await asyncio.Event().wait()  # block until cancelled
        yield "never"  # pragma: no cover

    task = asyncio.create_task(
        run_dispatch_listener("db", on_wake, stream_factory=stream, reconnect_delay=0)
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
