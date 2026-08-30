"""Finalize crash-recovery + expiry sweep (Phase 7 spec §6.4).

Two passes over the ``staged_uploads`` ledger, pure over the repo seam and an
``enqueue`` callable so the tick is fully unit-testable:

- **Expire**: ``pending`` rows past ``created_at + STAGING_TTL_SECONDS`` flip
  to ``expired`` — the §4.1 governing clock (minted but never pushed; the
  ledger's answer to the byte-level sweep).
- **Requeue stale claims**: ``finalizing`` rows whose claim is older than the
  stale threshold are presumed crashed. Flip back to ``pending`` FIRST (so the
  next tick cannot double-enqueue the same rows while this batch is queued —
  the delivery retry-sweep idiom), then re-enqueue one finalize job per row.
  The re-enqueued payload carries ``event_op: None``: the triggering outbox
  event's op is not persisted on the row, so a recovery run's recorder takes
  the conservative rejection branch (restore if a snapshot exists, otherwise
  leave — never delete without knowing the op). Rows whose claim never bound
  an item id cannot be re-driven and are left to the TTL expiry.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pipeline.finalize.repo import FinalizeRepo

logger = logging.getLogger(__name__)

#: payloads → the queue (the jobs layer binds this to enqueue_batch).
EnqueueFinalize = Callable[[list[dict[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class FinalizeSweepResult:
    expired: int
    requeued: int


async def finalize_sweep_tick(
    repo: FinalizeRepo,
    enqueue: EnqueueFinalize,
    *,
    ttl_seconds: int,
    stale_seconds: int,
    batch_limit: int,
) -> FinalizeSweepResult:
    expired = await repo.expire_pending(ttl_seconds)

    stale = await repo.list_stale_finalizing(stale_seconds, batch_limit)
    requeued = 0
    if stale:
        drivable = [s for s in stale if s.item_id]
        await repo.requeue_stale([s.id for s in stale])
        if drivable:
            await enqueue(
                [
                    {
                        "upload_id": s.id,
                        "collection_id": s.collection_id,
                        "item_id": s.item_id,
                        "event_op": None,
                    }
                    for s in drivable
                ]
            )
            requeued = len(drivable)
        for s in stale:
            if not s.item_id:
                logger.warning(
                    "stale finalize claim had no bound item; left to TTL expiry",
                    extra={"upload_id": s.id, "collection_id": s.collection_id},
                )
    return FinalizeSweepResult(expired=expired, requeued=requeued)
