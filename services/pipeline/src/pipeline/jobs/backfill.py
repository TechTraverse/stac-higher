"""Periodic drain of the app→pipeline delivery-backfill bridge (Slice C).

Each tick claims open ``stac_higher.delivery_backfills`` rows (queued, plus
stale-running crash resume) and runs each through ``run_backfill`` — chunked
bulk ``pipeline.deliver`` jobs, progress recorded per chunk. A backfill whose
association is deleted/disabled (or not a deliver association) fails cleanly
with that reason instead of enqueueing anything. Backfill is bulk work, not
latency-sensitive — the minute cron is the only wake path (no NOTIFY).
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.config import Settings
from pipeline.delivery.backfill import (
    STALE_RUNNING_SECONDS,
    BackfillRepo,
    PgBackfillRepo,
    blocked_reason,
    run_backfill,
)
from pipeline.dispatcher.loop import EnqueueDeliveries
from pipeline.jobs.dispatch import JOB_DELIVER
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.delivery_backfill_sweep"
CRON = "* * * * *"
#: backfills claimed per tick — each runs to completion, so keep the tick's
#: worst-case bounded; the next tick claims more.
SWEEP_BATCH = 10


async def backfill_sweep_tick(
    repo: BackfillRepo,
    enqueue: EnqueueDeliveries,
    *,
    batch_size: int = SWEEP_BATCH,
    stale_running_seconds: int = STALE_RUNNING_SECONDS,
) -> int:
    """Claim and run open backfills; returns how many reached a terminal
    state this tick. One backfill's failure never aborts its siblings."""
    jobs = await repo.claim_open_backfills(batch_size, stale_running_seconds)
    finished = 0
    for job in jobs:
        reason = blocked_reason(job)
        if reason is not None:
            await repo.mark_failed(job.id, reason)
            logger.warning(
                "backfill blocked",
                extra={"backfill_id": job.id, "reason": reason},
            )
            finished += 1
            continue
        try:
            total = await run_backfill(repo, enqueue, job)
        except Exception as exc:
            logger.exception(
                "backfill failed",
                extra={"backfill_id": job.id, "association_id": job.association_id},
            )
            await repo.mark_failed(job.id, str(exc))
        else:
            logger.info(
                "backfill completed",
                extra={
                    "backfill_id": job.id,
                    "association_id": job.association_id,
                    "items_enqueued": total,
                },
            )
        finished += 1
    return finished


def register(queue: QueueBackend, settings: Settings) -> None:
    async def backfill_sweep(timestamp: int) -> None:
        repo = PgBackfillRepo(settings.database_url)

        async def _enqueue(batches: list[dict[str, Any]]) -> None:
            await queue.enqueue_batch(JOB_DELIVER, batches)

        finished = await backfill_sweep_tick(repo, _enqueue)
        if finished:
            logger.info(
                "backfill sweep tick done",
                extra={"finished": finished, "scheduled_timestamp": timestamp},
            )

    queue.register_periodic(backfill_sweep, name=JOB_NAME, cron=CRON)
