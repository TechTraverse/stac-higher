"""Orphaned run-container reaper (M3-W-1; the deferral `process/sweep.py` names).

`launch.execute_run` reaps in a ``finally``, which covers every failure the
worker survives. It cannot cover the worker not surviving: a process killed
between `launch` and `reap` leaves a container running (or exited but
undeleted) with nothing left that knows about it. The DB sweep deliberately
does not touch containers — that would couple the ledger's failure domain to
the executor's — so the reconciliation lives here, on the executor side, and
uses the ledger only as a read.

Two rules decide an orphan, and the order they are evaluated in is the
correctness argument:

1. **The ledger rule.** `claim_due_runs` flips a row to `running` BEFORE the
   container is created, so `running` is the only status a live container can
   legitimately have. Anything else — terminal, requeued by stall recovery,
   or no row at all — means whoever owned this container is gone.
2. **The age rule**, a backstop for a row wedged `running` that stall recovery
   cannot reach. The ceiling sits above the runtime config's own maximum
   timeout, so a legitimately long run is never killed by it.

**The listing is taken BEFORE the ledger is read.** Reversed, a run that was
`queued` at read time could be claimed and launched a moment later, and the
reaper would judge a brand-new live container against a stale status. Taken in
this order, every container the reaper acts on demonstrably predates the status
that condemned it — and because reaping is by container ID, a newer container
for the same run can never be caught by an older observation.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from pipeline.metrics import PROCESS_ORPHANS_REAPED
from pipeline.process.config import MAX_TIMEOUT_SECONDS
from pipeline.process.executor import Executor, ExecutorUnavailable
from pipeline.process.repo import ProcessRepo

logger = logging.getLogger(__name__)

#: Age past which a container is an orphan whatever the ledger says. Derived
#: from the runtime config's timeout CEILING plus an hour of slack, not chosen
#: independently: below the ceiling this would kill legal work, and a test
#: pins the relationship so the two cannot drift apart.
DEFAULT_REAP_MAX_AGE_SECONDS = MAX_TIMEOUT_SECONDS + 3600

#: The one status that says "a live container is expected".
LIVE_STATUS = "running"


@dataclass(frozen=True)
class ReapResult:
    seen: int = 0
    reaped: int = 0
    #: Containers the backend refused to remove. Left for the next tick — the
    #: reaper is idempotent, so a transient daemon fault costs a cycle, not a
    #: leaked container.
    failed: int = 0


async def process_reap_tick(
    *,
    executor: Executor,
    repo: ProcessRepo,
    max_age_seconds: int = DEFAULT_REAP_MAX_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> ReapResult:
    at = now or dt.datetime.now(dt.UTC)
    try:
        launched = executor.list_launched()
    except ExecutorUnavailable as err:
        # An outage is not "no containers". Say so and do nothing — the next
        # tick reconciles once the backend is back.
        logger.warning(
            "orphan reaper could not list run containers",
            extra={"executor": executor.name, "error": str(err)},
        )
        return ReapResult()
    if not launched:
        return ReapResult()

    statuses = await repo.run_statuses([entry.run_id for entry in launched])

    reaped = failed = 0
    for entry in launched:
        aged = (
            entry.created_at is not None
            and (at - entry.created_at).total_seconds() > max_age_seconds
        )
        if statuses.get(entry.run_id) == LIVE_STATUS and not aged:
            continue
        try:
            executor.reap(entry.handle)
        except Exception as err:
            failed += 1
            logger.warning(
                "orphan run container could not be reaped",
                extra={
                    "run_id": entry.run_id,
                    "container": entry.handle.id,
                    "error": str(err),
                },
            )
            continue
        reaped += 1
        PROCESS_ORPHANS_REAPED.inc()
        logger.warning(
            "reaped an orphaned run container",
            extra={
                "run_id": entry.run_id,
                "container": entry.handle.id,
                "run_status": statuses.get(entry.run_id),
                "aged_out": aged,
            },
        )
    return ReapResult(seen=len(launched), reaped=reaped, failed=failed)
