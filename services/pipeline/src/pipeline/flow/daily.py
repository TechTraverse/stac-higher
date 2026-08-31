"""Daily flow-stats rollup (P9-E, spec §10 — M5-E).

`flow_stats` on an association or a source is a LIVE counter: it says what has
happened in total, not what happened on any particular day. The lineage strip
and the run sparkline need history, so one job per day snapshots each
subject's counters into `flow_stats_daily`.

Why a table rather than a GROUP BY over the ledgers, restated because it is
the whole justification for this job existing (P9-E): M2-G's
`history_retention` prunes those ledgers, so derived history silently thins;
and at M3 rates a 30-day aggregate per page view is exactly the unbounded
scan M2-A removed from `listDeliveries`.

The write is a DELTA snapshot: today's row holds what happened TODAY, derived
by subtracting yesterday's cumulative totals. Storing the raw cumulative
counter instead would make every reader do the subtraction, and would break
the moment a counter is reset.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SUBJECT_ASSOCIATION = "association"
SUBJECT_PROCESS = "process"

#: Counter names read out of the live `flow_stats` jsonb. A subject that does
#: not produce one leaves it at zero.
COUNTERS = ("files", "items", "bytes", "delivered", "failed", "dead", "runs")


@dataclass(frozen=True)
class DailyRow:
    subject_kind: str
    subject_id: str
    day: dt.date
    counters: dict[str, int]


def _counter(stats: dict, name: str) -> int:
    value = stats.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def delta_row(
    subject_kind: str,
    subject_id: str,
    day: dt.date,
    current: dict,
    previous_cumulative: dict[str, int] | None,
) -> DailyRow:
    """One day's activity for one subject.

    A missing previous snapshot means this is the subject's first bucket, and
    the whole cumulative total is attributed to it. That over-attributes the
    first day for a subject that existed before the job did — an acceptable,
    one-off, visible-in-the-strip artefact, and far better than dropping the
    day or inventing a backfill from ledgers the retention sweep may already
    have pruned.

    A counter that went DOWN (a reset, or a rollup rewritten by hand) clamps
    to zero rather than recording a negative day, which no reader could plot.
    """
    counters: dict[str, int] = {}
    for name in COUNTERS:
        now_value = _counter(current, name)
        before = (previous_cumulative or {}).get(name, 0)
        counters[name] = max(0, now_value - before)
    return DailyRow(
        subject_kind=subject_kind,
        subject_id=subject_id,
        day=day,
        counters=counters,
    )
