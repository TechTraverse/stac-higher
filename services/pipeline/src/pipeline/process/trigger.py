"""Turning a trigger into a queued run (spec §6/§7).

One function, because both legs — the dispatcher's item_event batches and the
scheduler's cron ticks — must go through the SAME rate ceiling. Splitting them
would leave two enqueue paths and one of them would eventually forget the
check, which is exactly the silent-amplification failure §7 exists to prevent.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.process.rate import WINDOW_SECONDS, evaluate
from pipeline.process.repo import ProcessRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerResult:
    run_id: str | None
    deferred: bool
    recent_runs: int = 0
    ceiling: int = 0


async def trigger_run(
    repo: ProcessRepo,
    *,
    process_id: str,
    revision_id: str,
    source_id: str | None,
    input_items: Sequence[dict[str, Any]],
    now: dt.datetime,
    is_test: bool = False,
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

    run_id = await repo.enqueue_run(
        process_id=process_id,
        revision_id=revision_id,
        source_id=source_id,
        input_items=input_items,
        deferred_until=verdict.deferred_until,
        is_test=is_test,
    )

    if verdict.deferred:
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
    )
