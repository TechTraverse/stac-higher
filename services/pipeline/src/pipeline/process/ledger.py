"""Run-ledger state transitions (spec §6).

`queued → running → succeeded | failed → dead`, with attempts on the row and
the runtime's RetrySpec as the budget. Pure decision functions here; the SQL
that applies them is process/repo.py.

The distinction that matters, and the reason this is not just "set status":

- A run that FAILED has spent an attempt. It goes back to `failed` and waits
  for the retry sweep until the budget is gone, then `dead`.
- A run the EXECUTOR could not start (backend unavailable) has not spent an
  attempt on user code at all. Treating that as a failure would burn a
  process's retry budget on an outage of ours.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

TERMINAL_STATUSES = ("succeeded", "dead")
#: A run in one of these is still owed work by the pipeline.
OPEN_STATUSES = ("queued", "running", "failed")


@dataclass(frozen=True)
class RunTransition:
    status: str
    #: When the retry sweep may re-drive a `failed` run. None otherwise.
    next_attempt_at: dt.datetime | None = None
    error: str | None = None


def outcome_transition(
    *,
    exit_code: int,
    timed_out: bool,
    attempts: int,
    max_attempts: int,
    now: dt.datetime,
    retry_wait_seconds: int,
    error: str | None = None,
) -> RunTransition:
    """Where a finished run lands.

    ``attempts`` is the count INCLUDING the one that just finished, so the
    budget comparison reads the way an operator would state it: "three
    attempts" means the third failure is dead.
    """
    if exit_code == 0 and not timed_out:
        return RunTransition(status="succeeded")

    detail = error
    if detail is None:
        detail = (
            "run exceeded its timeout and was killed"
            if timed_out
            else f"run exited {exit_code}"
        )

    if attempts >= max_attempts:
        return RunTransition(status="dead", error=detail)
    return RunTransition(
        status="failed",
        next_attempt_at=now + dt.timedelta(seconds=retry_wait_seconds),
        error=detail,
    )


def infrastructure_transition(
    *, now: dt.datetime, retry_wait_seconds: int, error: str
) -> RunTransition:
    """A run the executor could not start.

    Deliberately NOT terminal and deliberately NOT attempt-spending: the
    process did not fail, our backend did. It returns to `queued` so the next
    sweep re-drives it, and a persistent outage surfaces as a stalled flow
    (the expectation breach) rather than as a pile of dead runs blamed on the
    operator's code.
    """
    return RunTransition(
        status="queued",
        next_attempt_at=now + dt.timedelta(seconds=retry_wait_seconds),
        error=error,
    )


#: K-3 (spec §5.2): the sub-states of a queued/running row in
#: ``process_runs.phase``, pinned by tests/contract-fixtures/process-run-phase.json
#: and migration 029's CHECK. NULL on a plain queued row and on every terminal
#: row. This slice writes `pending_capacity` and `starting`; `running` is
#: written by K-4's watcher.
PHASES = ("pending_capacity", "starting", "running")
PHASE_PENDING_CAPACITY, PHASE_STARTING, PHASE_RUNNING = PHASES

#: How long a run released for capacity waits before a claim may take it
#: again (spec §8). The claim is the run tick's, so the effective wait is the
#: first tick at or after this (under a minute) — see docs/ISSUES.md.
CAPACITY_RETRY_SECONDS = 15


def capacity_detail(profile_id: str, capacity: int) -> str:
    """The `phase_detail` a capacity release records — what the UI's
    *Waiting for capacity* tooltip shows."""
    return f"profile {profile_id!r} is at its capacity of {capacity} on this host"
