"""Execute one claimed run and record its verdict (spec §6).

The join between M5-B's executor (which knows how to run a container) and the
ledger (which knows what a run means). Kept separate from both so the
execution boundary can be tested without a database and the ledger without
Docker.

Ordering is the load-bearing part, and it follows §9/I-62: the log OBJECT is
written before the ledger row that references it. The retention leg deletes in
the opposite order, so the only interleaving a crash can leave behind is an
object with no row — which the staging TTL sweep ages out — never a row
pointing at bytes that do not exist.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from pipeline.config import Settings
from pipeline.process.config import ProcessConfigError, parse_process_env, parse_process_runtime
from pipeline.process.credentials import RunCredentialsError
from pipeline.process.executor import Executor, ExecutorUnavailable
from pipeline.process.launch import SecretResolutionError, execute_run
from pipeline.process.ledger import infrastructure_transition, outcome_transition
from pipeline.process.repo import ProcessRepo, QueuedRun

logger = logging.getLogger(__name__)

#: How long a failed run waits before the sweep re-drives it. The runtime's
#: RetrySpec carries the attempt BUDGET; the spacing is a platform concern.
DEFAULT_RETRY_WAIT_SECONDS = 60


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    log_ref: str | None = None
    error: str | None = None


async def run_one(
    run: QueuedRun,
    *,
    repo: ProcessRepo,
    executor: Executor,
    settings: Settings,
    storage_client,
    resolve_secret,
    now: dt.datetime | None = None,
    sts_client=None,
) -> RunResult:
    """Execute a claimed run and write its outcome. Never raises for a run
    that merely failed — that is a result, recorded in the ledger."""
    at = now or dt.datetime.now(dt.UTC)

    # A revision whose stored documents no longer parse is a CONTRACT
    # violation, not a transient fault: retrying cannot fix it, so the run
    # dies immediately instead of burning its budget.
    try:
        runtime = parse_process_runtime(run.runtime)
        env_entries = parse_process_env(run.env)
    except ProcessConfigError as err:
        await _finish(repo, run, "dead", None, f"unusable revision: {err}", None, at)
        return RunResult(run.id, "dead", error=str(err))

    if run.code is None:
        await _finish(
            repo, run, "dead", None, "revision carries no code to execute", None, at
        )
        return RunResult(run.id, "dead", error="no code")

    try:
        outcome = execute_run(
            executor,
            settings,
            storage_client,
            run_id=run.id,
            process_id=run.process_id,
            runtime=runtime,
            code=run.code,
            env_entries=env_entries,
            resolve_secret=resolve_secret,
            sts_client=sts_client,
        )
    except (ExecutorUnavailable, RunCredentialsError) as err:
        # OUR failure, not the process's: back to `queued` without spending an
        # attempt (see ledger.infrastructure_transition).
        transition = infrastructure_transition(
            now=at, retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS, error=str(err)
        )
        await repo.finish_run(
            run.id,
            status=transition.status,
            error=transition.error,
            log_ref=None,
            next_attempt_at=transition.next_attempt_at,
        )
        logger.warning(
            "process run could not start; requeued without spending an attempt",
            extra={"run_id": run.id, "process_id": run.process_id, "error": str(err)},
        )
        return RunResult(run.id, transition.status, error=str(err))
    except SecretResolutionError as err:
        # The revision names a secret that cannot be resolved. That is
        # configuration, and no amount of retrying fixes it.
        await _finish(repo, run, "dead", None, str(err), None, at)
        return RunResult(run.id, "dead", error=str(err))

    transition = outcome_transition(
        exit_code=outcome.status.exit_code,
        timed_out=outcome.status.timed_out,
        attempts=run.attempts,
        max_attempts=runtime.max_attempts,
        now=at,
        retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS,
        error=outcome.status.error,
    )
    await _finish(
        repo,
        run,
        transition.status,
        outcome.log_ref,
        transition.error,
        transition.next_attempt_at,
        at,
    )
    return RunResult(
        run.id, transition.status, log_ref=outcome.log_ref, error=transition.error
    )


async def _finish(
    repo: ProcessRepo,
    run: QueuedRun,
    status: str,
    log_ref: str | None,
    error: str | None,
    next_attempt_at: dt.datetime | None,
    at: dt.datetime,
) -> None:
    await repo.finish_run(
        run.id,
        status=status,
        error=error,
        log_ref=log_ref,
        next_attempt_at=next_attempt_at,
    )
    # flow_stats is a per-SOURCE rollup, so a test run or a manual re-run
    # (both source-less) contributes nothing — otherwise an operator testing a
    # process would move the telemetry its expectation is judged against.
    if run.source_id and status in ("succeeded", "dead"):
        await repo.record_source_run(
            run.source_id, succeeded=status == "succeeded", at=at
        )
