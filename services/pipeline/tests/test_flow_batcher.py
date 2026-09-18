"""FlowStatsBatcher (M3-D): ITEMIZE's per-item deltas become one rollup write
per association per flush — the DISCOVER shape, applied to ITEMIZE."""

from __future__ import annotations

import asyncio
import contextlib

from _ingest_fake import FakeIngestRepo
from pipeline.flow.batcher import FlowDelta, FlowStatsBatcher


def test_deltas_sum_and_latency_keeps_the_last_value():
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1, bytes_added=10, latency_seconds=1.5)
    batcher.add("a1", items=1, bytes_added=5)
    batcher.add("a1", failed=1, latency_seconds=2.5)
    batcher.add("a2", items=1)

    assert batcher.pending == {
        "a1": FlowDelta(items=2, bytes_added=15, failed=1, latency_seconds=2.5),
        "a2": FlowDelta(items=1),
    }


def test_drain_swaps_the_pending_map_out():
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)
    drained = batcher.drain()
    assert drained == {"a1": FlowDelta(items=1)}
    assert batcher.pending == {}


async def test_flush_writes_one_bump_per_association_with_the_summed_delta():
    repo = FakeIngestRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1, bytes_added=10, latency_seconds=1.5)
    batcher.add("a1", items=1, bytes_added=5, latency_seconds=2.5)
    batcher.add("a2", failed=1)

    written = await batcher.flush(repo)

    assert written == 2
    assert repo.flow_stats["a1"]["items"] == 2
    assert repo.flow_stats["a1"]["bytes"] == 15
    assert repo.flow_stats["a1"]["last_latency_seconds"] == 2.5
    assert "last_activity_at" in repo.flow_stats["a1"]
    assert repo.flow_stats["a2"]["failed"] == 1
    assert "last_error_at" in repo.flow_stats["a2"]
    assert batcher.pending == {}


async def test_flush_with_nothing_pending_writes_nothing():
    repo = FakeIngestRepo()
    assert await FlowStatsBatcher().flush(repo) == 0
    assert repo.flow_stats == {}


async def test_a_failed_write_keeps_the_delta_for_the_next_flush(caplog):
    class _FlakyRepo(FakeIngestRepo):
        fail = True

        async def bump_flow_stats(self, association_id, **kw):
            if self.fail:
                raise RuntimeError("db away")
            await super().bump_flow_stats(association_id, **kw)

    repo = _FlakyRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)

    assert await batcher.flush(repo) == 0
    assert batcher.pending == {"a1": FlowDelta(items=1)}
    assert any("flow_stats flush failed" in r.message for r in caplog.records)

    repo.fail = False
    batcher.add("a1", items=1)
    assert await batcher.flush(repo) == 1
    assert repo.flow_stats["a1"]["items"] == 2


async def test_run_flushes_on_the_interval_and_once_more_on_cancel():
    repo = FakeIngestRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)

    task = asyncio.create_task(batcher.run(lambda: repo, 0.01))
    await asyncio.sleep(0.05)
    assert repo.flow_stats["a1"]["items"] == 1

    batcher.add("a1", items=1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert repo.flow_stats["a1"]["items"] == 2


async def test_a_failed_write_does_not_regress_a_newer_latency():
    """Fix round 4: a delta retained after a failed flush merges UNDER what
    arrived during the failed write — the newer latency stays 'last'."""

    class _AddsMidWriteThenFails(FakeIngestRepo):
        batcher: FlowStatsBatcher

        async def bump_flow_stats(self, association_id, **kw):
            self.batcher.add("a1", items=1, latency_seconds=12.0)  # an ITEMIZE lands mid-write
            raise RuntimeError("db away")

    repo = _AddsMidWriteThenFails()
    batcher = FlowStatsBatcher()
    repo.batcher = batcher
    batcher.add("a1", items=1, latency_seconds=61.0)

    assert await batcher.flush(repo) == 0
    assert batcher.pending == {"a1": FlowDelta(items=2, latency_seconds=12.0)}
