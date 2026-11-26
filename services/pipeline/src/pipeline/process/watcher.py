"""The run watcher: the executor's event stream wakes the Reconciler (K-4).

The ``run_dispatch_listener`` shape (``dispatcher/listener.py``) for a
different source: one singleton task per worker process consumes
``Executor.watch()`` and hands each event to ``Reconciler.on_event``. Two
differences from the dispatch listener, both deliberate:

- A handler failure is logged PER EVENT and the stream continues, so one bad
  row never tears down the stream every other run's wake-ups ride on.
- The stream itself failing (the daemon restarted, the socket proxy's idle
  timeout closed it) is logged and reopened after a delay; the reopened
  stream's RESYNC re-reconciles everything, so nothing that happened while it
  was down is missed. The run tick remains the backstop either way.

Events are wake-ups only (plan R1): the Reconciler re-reads ``status()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from pipeline.process.executor import RunEvent

logger = logging.getLogger(__name__)

WATCHER_RECONNECT_SECONDS = 5.0
#: Consecutive failed connects back off to this (a deployment with no event
#: source at all — the Helm chart until K-5, I-160 — logs once a minute,
#: not every 5 s).
WATCHER_MAX_RECONNECT_SECONDS = 60.0


async def run_watcher(
    watch: Callable[[], AsyncIterator[RunEvent]],
    on_event: Callable[[RunEvent], Awaitable[None]],
    *,
    reconnect_delay: float = WATCHER_RECONNECT_SECONDS,
    max_reconnect_delay: float = WATCHER_MAX_RECONNECT_SECONDS,
    max_connects: int | None = None,
) -> None:
    """Consume ``watch()`` until cancelled, reconnecting after any failure:
    after ``reconnect_delay`` when the stream had opened, doubling up to
    ``max_reconnect_delay`` while it keeps failing to open. ``max_connects``
    is a test seam (a bounded number of streams)."""
    connects = 0
    delay = reconnect_delay
    while True:
        opened = False
        try:
            async for event in watch():
                opened = True
                try:
                    await on_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "run watcher could not apply an event",
                        extra={"run_id": event.run_id, "kind": event.kind},
                    )
            logger.warning(
                "run watcher stream ended; reconnecting",
                extra={"reconnect_delay": reconnect_delay},
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.warning(
                "run watcher stream failed; reconnecting",
                extra={"reconnect_delay": delay, "error": str(err)},
            )
        connects += 1
        if max_connects is not None and connects >= max_connects:
            return
        delay = reconnect_delay if opened else min(delay * 2, max_reconnect_delay)
        await asyncio.sleep(delay)
