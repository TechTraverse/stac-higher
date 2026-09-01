"""Process-run crash recovery (spec §6, the M2-0 sweep pattern).

Two things a tick does, and one it deliberately does not:

- **Stall recovery.** A run stranded `running` past
  ``PROCESS_RUN_STALL_SECONDS`` is returned to `queued`. The crash direction
  stays safe: a run that actually finished has already left `running`, so the
  worst case is a duplicate execution, never a lost one.
- **Nothing else re-drives due work.** `claim_due_runs` already picks up both
  deferred runs whose time has come and `failed` runs whose retry is due, so
  the sweep does not need a second, subtly different notion of "due" that
  could drift from the claim's.

What it does NOT do is reap orphaned CONTAINERS. That needs the executor, and
wiring one into a DB sweep would couple two independent failure domains. That
job is `process/reaper.py` (M3-W-1), which finds them by the
`stac-higher.run-id` label and reads this ledger without writing it — and it
composes with the stall recovery above: a container whose row this sweep
returns to `queued` stops being `running`, which is exactly the reaper's
orphan signal.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from pipeline.process.repo import ProcessRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    requeued: int = 0


async def process_sweep_tick(
    repo: ProcessRepo,
    *,
    stall_seconds: int,
    batch_limit: int,
    now: dt.datetime | None = None,
) -> SweepResult:
    at = now or dt.datetime.now(dt.UTC)
    requeued = await repo.reset_stalled_runs(
        at - dt.timedelta(seconds=stall_seconds), batch_limit
    )
    if requeued:
        logger.warning(
            "process runs requeued after a stall",
            extra={"requeued": requeued, "stall_seconds": stall_seconds},
        )
    return SweepResult(requeued=requeued)
