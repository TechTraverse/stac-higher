"""The run-rate ceiling (spec §7, backstopping I-64).

`processes.max_runs_per_hour` is enforced at ENQUEUE time, and the policy is
pure so it can be reasoned about without a database.

Why a ceiling exists at all: the M5-D cycle check can only see OUR edges
(collection → process → collection). A loop that closes through a connection
or an external system is not statically decidable, so something must bound the
blast radius at runtime. This is it — a runaway loop degrades to ceiling-paced,
alerting, and visible, instead of amplifying silently.

The two decisions worth stating:

- **Defer, never drop.** A trigger over the ceiling does not lose its items;
  it lands on the source's already-deferred run, which absorbs them. Dropping
  would make the ceiling a data-loss mechanism, and an operator raising the
  limit later could never recover what was thrown away.
- **Coalesce per source.** At most one deferred run per source (the partial
  unique index in migration 022 makes that true under concurrent dispatch),
  so a source under sustained pressure accumulates ONE growing run rather
  than a queue of runs that would themselves breach the ceiling on release.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

#: The rolling window the ceiling is expressed over.
WINDOW_SECONDS = 3600


class RateDecision(Enum):
    RUN = "run"
    DEFER = "defer"


@dataclass(frozen=True)
class RateVerdict:
    decision: RateDecision
    #: When the deferred run becomes eligible. None for RUN.
    deferred_until: dt.datetime | None = None
    #: Runs already counted in the window — carried for the alert message and
    #: the flow_stats rollup, so the operator sees the number, not just "hit
    #: the limit".
    recent_runs: int = 0
    ceiling: int = 0

    @property
    def deferred(self) -> bool:
        return self.decision is RateDecision.DEFER


def evaluate(
    recent_runs: int,
    max_runs_per_hour: int,
    now: dt.datetime,
    *,
    oldest_in_window: dt.datetime | None = None,
) -> RateVerdict:
    """Decide whether one more run may start now.

    ``oldest_in_window`` is when the earliest counted run started. The
    deferral lands just after that run ages out of the window, which is the
    soonest the ceiling could actually admit another — a fixed backoff would
    either wake too early (and re-defer) or hold the run longer than the
    policy requires.
    """
    if recent_runs < max_runs_per_hour:
        return RateVerdict(
            decision=RateDecision.RUN,
            recent_runs=recent_runs,
            ceiling=max_runs_per_hour,
        )

    if oldest_in_window is not None:
        eligible = oldest_in_window + dt.timedelta(seconds=WINDOW_SECONDS)
        # Never schedule into the past: a clock skew or a stale read would
        # otherwise produce a deferral that is immediately eligible, turning
        # the ceiling into a busy loop.
        deferred_until = max(eligible, now + dt.timedelta(seconds=1))
    else:
        # No timestamp to reason from (a count without rows should not happen,
        # but a defensive fallback beats an exception on the enqueue path).
        deferred_until = now + dt.timedelta(seconds=WINDOW_SECONDS)

    return RateVerdict(
        decision=RateDecision.DEFER,
        deferred_until=deferred_until,
        recent_runs=recent_runs,
        ceiling=max_runs_per_hour,
    )
