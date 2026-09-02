"""Turning a trigger into a queued — and immediately dispatched — run
(spec §6/§7; GOES spec §5 for the dispatch half).

One function, because both legs — the dispatcher's item_event batches and the
scheduler's cron ticks — must go through the SAME rate ceiling. Splitting them
would leave two enqueue paths and one of them would eventually forget the
check, which is exactly the silent-amplification failure §7 exists to prevent.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.metrics import PROCESS_RATE_DEFERRALS
from pipeline.process.rate import WINDOW_SECONDS, evaluate
from pipeline.process.repo import ProcessRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerResult:
    run_id: str | None
    deferred: bool
    recent_runs: int = 0
    ceiling: int = 0
    #: The items joined a run that was already queued for this source.
    merged: bool = False
    #: An immediate-execution job was asked for (G-3).
    enqueued_now: bool = False


async def trigger_run(
    repo: ProcessRepo,
    *,
    process_id: str,
    revision_id: str,
    source_id: str | None,
    input_items: Sequence[dict[str, Any]],
    now: dt.datetime,
    is_test: bool = False,
    enqueue_now: Callable[[str], Awaitable[None]] | None = None,
) -> TriggerResult:
    """Queue a run, deferring (and coalescing) when the §7 ceiling is spent.

    A test run is checked against the ceiling like any other. Exempting it
    would give a runaway loop a way around the limit: nothing stops a script
    from requesting test runs.
    """
    window = await repo.rate_window(
        process_id, now - dt.timedelta(seconds=WINDOW_SECONDS)
    )
    verdict = evaluate(
        window.recent_runs,
        window.max_runs_per_hour,
        now,
        oldest_in_window=window.oldest_in_window,
    )

    run_id, merged = await repo.enqueue_run_detailed(
        process_id=process_id,
        revision_id=revision_id,
        source_id=source_id,
        input_items=input_items,
        deferred_until=verdict.deferred_until,
        is_test=is_test,
    )

    # G-3: don't make the run wait for the next minute tick. The tick stays
    # registered as the recovery sweep, and both paths claim through the same
    # atomic UPDATE, so the loser simply finds nothing. A DEFERRED run is
    # deliberately not dispatched — immediate execution must not defeat the
    # ceiling the verdict just applied.
    enqueued_now = False
    if run_id is not None and not verdict.deferred and enqueue_now is not None:
        await enqueue_now(run_id)
        enqueued_now = True

    if verdict.deferred:
        PROCESS_RATE_DEFERRALS.inc()
        # The monitor raises `process_rate_limited` off this state (M5-E); the
        # log line is what an operator sees until then, and it carries the
        # numbers rather than just "limited".
        logger.warning(
            "process run deferred by the rate ceiling",
            extra={
                "process_id": process_id,
                "source_id": source_id,
                "run_id": run_id,
                "recent_runs": verdict.recent_runs,
                "ceiling": verdict.ceiling,
                "deferred_until": verdict.deferred_until.isoformat()
                if verdict.deferred_until
                else None,
            },
        )
    return TriggerResult(
        run_id=run_id,
        deferred=verdict.deferred,
        recent_runs=verdict.recent_runs,
        ceiling=verdict.ceiling,
        merged=merged,
        enqueued_now=enqueued_now,
    )
