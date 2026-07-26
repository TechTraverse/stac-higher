"""NOTIFY-woken dispatch wake loop (Slice C, ROADMAP §6.4).

The outbox trigger fires a payload-less ``pg_notify('item_events', '')`` per
captured row (ADR 0007 — identical notifications coalesce per transaction, so
a bulk upsert is one wake). This loop LISTENs on that channel over a dedicated
connection and calls ``on_wake`` (a drain-until-empty dispatch) for each
notification, giving single-digit-second delivery latency. The minute poll
(jobs.dispatch) stays registered as the fallback wake path: a dropped
notification or a dead listener connection delays dispatch by ≤1 minute,
never loses it — the payload is always the outbox row, never the NOTIFY.

The stream yields one synthetic wake right after LISTEN succeeds, so events
that arrived while the listener was down are drained immediately on
(re)connect instead of waiting for the poll.

Single-instance assumption (documented per ISSUES I-40): one pipeline process
runs one listener. Overlapping wakes (listener + poll fallback) are safe —
the outbox claim is atomic — so multi-instance HA is a Phase 8 throughput
topic (ROADMAP §10 scheduler-HA), not a correctness one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

CHANNEL = "item_events"
DEFAULT_RECONNECT_SECONDS = 5.0

#: factory returning a fresh notification stream (one per (re)connect).
StreamFactory = Callable[[], AsyncIterator[object]]


async def _pg_notify_stream(
    database_url: str, channel: str
) -> AsyncIterator[object]:  # pragma: no cover - thin psycopg wrapper
    import psycopg

    conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    try:
        await conn.execute(f'LISTEN "{channel}"')
        logger.info("dispatch listener connected", extra={"channel": channel})
        # Catch-up wake: drain whatever arrived while we were not listening.
        yield "connected"
        while True:
            # Block until at least one notification arrives...
            async for _notify in conn.notifies(stop_after=1):
                pass
            # ...then absorb the rest of the burst without waiting
            # (timeout=0 delivers only what is already buffered). Identical
            # notifies coalesce per transaction server-side, but N separate
            # committed transactions still queue N — this makes a burst cost
            # one drain, not N-1 empty claim round trips.
            async for _notify in conn.notifies(timeout=0):
                pass
            yield "notify"
    finally:
        await conn.close()


async def run_dispatch_listener(
    database_url: str,
    on_wake: Callable[[], Awaitable[None]],
    *,
    channel: str = CHANNEL,
    reconnect_delay: float = DEFAULT_RECONNECT_SECONDS,
    stream_factory: StreamFactory | None = None,
    max_connects: int | None = None,
) -> None:
    """Run the LISTEN wake loop until cancelled.

    Any failure — connect, the stream dying, ``on_wake`` raising (queue down) —
    is logged and answered with a fresh connection after ``reconnect_delay``;
    the poll fallback covers the gap. ``stream_factory`` and ``max_connects``
    are test seams (a fake stream / a bounded number of connection attempts).
    """
    factory = stream_factory or (lambda: _pg_notify_stream(database_url, channel))
    connects = 0
    while True:
        try:
            async for _notify in factory():
                await on_wake()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "dispatch listener failed; reconnecting",
                extra={"channel": channel, "reconnect_delay": reconnect_delay},
            )
        connects += 1
        if max_connects is not None and connects >= max_connects:
            return
        await asyncio.sleep(reconnect_delay)
