"""Batch ITEMIZE's flow_stats deltas into one rollup write per association (M3-D).

``bump_flow_stats`` takes the association row ``FOR UPDATE`` — measured flat at
~460 bumps/s per association (S-D), a row lock, not a throughput ceiling. At
concurrency 12 every ITEMIZE on one association would queue ~2 ms behind the
others, a serialization point in a ~44 ms stage. DISCOVER already writes one
rollup per tick; this gives ITEMIZE the same shape: deltas are summed here and
written once per association per ``FLOW_STATS_FLUSH_SECONDS`` (spec §7.4).

Counts are exact. ``last_activity_at`` / ``last_error_at`` are stamped at flush
time, so they trail the item by at most one interval — inside the M2-B
expectation windows (seconds to minutes). Telemetry only: a crash loses at most
one interval of deltas, never a row. Single-threaded by construction — every
caller is a coroutine on the worker's event loop — so no lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pipeline.ingest.repo import IngestRepo

logger = logging.getLogger(__name__)


@dataclass
class FlowDelta:
    files: int = 0
    bytes_added: int = 0
    items: int = 0
    failed: int = 0
    #: The LAST observed latency — what `apply_ingest_activity` stores.
    latency_seconds: float | None = None

    def add(
        self,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        self.files += files
        self.bytes_added += bytes_added
        self.items += items
        self.failed += failed
        if latency_seconds is not None:
            self.latency_seconds = latency_seconds


class FlowStatsBatcher:
    def __init__(self) -> None:
        self._pending: dict[str, FlowDelta] = {}

    def add(
        self,
        association_id: str,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        self._pending.setdefault(association_id, FlowDelta()).add(
            files=files,
            bytes_added=bytes_added,
            items=items,
            failed=failed,
            latency_seconds=latency_seconds,
        )

    @property
    def pending(self) -> dict[str, FlowDelta]:
        return dict(self._pending)

    def drain(self) -> dict[str, FlowDelta]:
        drained, self._pending = self._pending, {}
        return drained

    async def flush(self, repo: IngestRepo) -> int:
        """One ``bump_flow_stats`` per association with a pending delta.

        A failed write puts that delta back (merged with whatever arrived in
        the meantime) for the next flush and logs — the counts are not lost,
        only late.
        """
        written = 0
        for association_id, delta in self.drain().items():
            try:
                await repo.bump_flow_stats(
                    association_id,
                    files=delta.files,
                    bytes_added=delta.bytes_added,
                    items=delta.items,
                    failed=delta.failed,
                    latency_seconds=delta.latency_seconds,
                )
                written += 1
            except Exception:
                logger.exception(
                    "flow_stats flush failed; delta retained",
                    extra={"association_id": association_id},
                )
                self._pending.setdefault(association_id, FlowDelta()).add(
                    files=delta.files,
                    bytes_added=delta.bytes_added,
                    items=delta.items,
                    failed=delta.failed,
                    latency_seconds=delta.latency_seconds,
                )
        return written

    async def run(self, repo_factory: Callable[[], IngestRepo], interval_seconds: float) -> None:
        """Flush every ``interval_seconds`` until cancelled, then once more."""
        repo = repo_factory()
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.flush(repo)
        finally:
            try:
                await self.flush(repo)
            except Exception:
                logger.warning("final flow_stats flush failed", exc_info=True)


#: The process's one batcher — the itemize handler adds to it, `main.run()`
#: flushes it. Tests construct their own.
FLOW_BATCHER = FlowStatsBatcher()
