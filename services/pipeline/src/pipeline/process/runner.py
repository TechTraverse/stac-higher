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
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pipeline.config import Settings
from pipeline.metrics import PROCESS_RUNS
from pipeline.process.config import ProcessConfigError, parse_process_env, parse_process_runtime
from pipeline.process.credentials import RunCredentialsError
from pipeline.process.executor import Executor, ExecutorUnavailable
from pipeline.process.hardware import (
    HardwareProfileError,
    HardwareProfileSet,
    load_hardware_profiles,
)
from pipeline.process.inputs import (
    KIND_EXTRACT,
    KIND_TRANSFORM,
    InputPlanError,
    input_env,
    parse_canonical_href,
    plan_inputs,
)
from pipeline.process.launch import (
    HardwareProfileRejected,
    ImageUnusable,
    NetworkCapExceeded,
    RuntimeImageUnavailable,
    SecretResolutionError,
    check_hardware_bounds_for,
    check_network_cap,
    check_user_image_launchable,
    execute_run,
    resolve_runtime_image,
)
from pipeline.process.ledger import infrastructure_transition, outcome_transition
from pipeline.process.repo import ProcessRepo, QueuedRun
from pipeline.process.staging import InputStagingError, RemoteFetcher, stage_inputs

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
    fetch_remote: RemoteFetcher | None = None,
    on_dead: Callable[[QueuedRun, str], Awaitable[None]] | None = None,
    profiles: HardwareProfileSet | None = None,
) -> RunResult:
    """Execute a claimed run and write its outcome. Never raises for a run
    that merely failed — that is a result, recorded in the ledger.

    Order (GOES spec §3.2): parse → network cap → runtime image → hardware
    bounds → plan inputs (repo reads) → stage remote inputs → mint
    credentials + launch. A failure anywhere before launch means no
    container ever existed.
    """
    at = now or dt.datetime.now(dt.UTC)

    # A revision whose stored documents no longer parse is a CONTRACT
    # violation, not a transient fault: retrying cannot fix it, so the run
    # dies immediately instead of burning its budget.
    try:
        runtime = parse_process_runtime(run.runtime)
        env_entries = parse_process_env(run.env)
    except ProcessConfigError as err:
        await _finish(
            repo, run, "dead", None, f"unusable revision: {err}", None, at, on_dead=on_dead
        )
        return RunResult(run.id, "dead", error=str(err))

    # C-1 (ADR 0021): the contract admits user-image kinds but nothing can
    # launch one yet. This runs before the code check, so a kind-3 run
    # (legitimately code-less) dies for the real reason.
    try:
        check_user_image_launchable(runtime)
    except ImageUnusable as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))

    if run.code is None:
        await _finish(
            repo,
            run,
            "dead",
            None,
            "revision carries no code to execute",
            None,
            at,
            on_dead=on_dead,
        )
        return RunResult(run.id, "dead", error="no code")

    # A revision above the deployment's network cap is configuration, not a
    # fault: it dies naming the level and the cap, never launches lower.
    try:
        check_network_cap(runtime, settings)
    except NetworkCapExceeded as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))

    # X-queue spec §8: same shape for the runtime image alias — a known alias
    # this deployment ships no image for dies here, before anything is staged
    # or minted, naming the alias and the variable.
    try:
        resolve_runtime_image(runtime, settings)
    except RuntimeImageUnavailable as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))

    # K-1 spec §4: resolve the deployment's profile set. An unreadable or
    # missing profile document is OUR infrastructure failing, not the
    # process's, so it goes back to `queued` without spending an attempt —
    # the same shape as the executor-outage branch below.
    try:
        if profiles is not None:
            profile_set = profiles
        elif settings.process_hardware_profiles_file:
            profile_set = load_hardware_profiles(Path(settings.process_hardware_profiles_file))
        else:
            profile_set = load_hardware_profiles()
    except HardwareProfileError as err:
        transition = infrastructure_transition(
            now=at, retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS, error=str(err)
        )
        PROCESS_RUNS.labels(outcome=transition.status).inc()
        await repo.finish_run(
            run.id,
            status=transition.status,
            error=transition.error,
            log_ref=None,
            next_attempt_at=transition.next_attempt_at,
        )
        logger.warning(
            "process run could not start: hardware profiles unavailable; requeued "
            "without spending an attempt",
            extra={"run_id": run.id, "process_id": run.process_id, "error": str(err)},
        )
        return RunResult(run.id, transition.status, error=str(err))

    # K-1 spec §4: the hardware block against the deployment's profile set —
    # the same check the app ran at deploy time, run again here because the
    # set is deployment config that may differ from the app's.
    try:
        profile = check_hardware_bounds_for(runtime, profile_set)
    except HardwareProfileRejected as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))

    # GOES spec §3: describe the inputs, stage the remote ones, THEN mint+launch.
    # §15: an extractor run (association-triggered) reads the collection its
    # association ingests into — its refs name it — since it has no sources.
    # Its refs also CARRY their documents (the draft), so pgstac is not read.
    is_extract = run.association_id is not None
    if is_extract:
        source_collections = tuple(
            sorted(
                {str(ref["collection_id"]) for ref in run.input_items if ref.get("collection_id")}
            )
        )
    else:
        source_collections = await repo.list_source_collections(run.process_id)
    documents: dict[tuple[str, str], dict] = {}
    for ref in run.input_items:
        coll = ref.get("collection_id") or (
            source_collections[0] if len(source_collections) == 1 else None
        )
        item_id = ref.get("item_id")
        if not coll or not item_id or (coll, item_id) in documents:
            continue
        if is_extract:
            # The draft the ref carries IS the document (§15) — the planner
            # reads it from the ref, and it is collected here so both kinds
            # go through the same reference-mode resolution below.
            draft = ref.get("draft")
            if isinstance(draft, dict):
                documents[(coll, item_id)] = draft
            continue
        doc = await repo.get_item(coll, item_id)
        if doc is not None:
            documents[(coll, item_id)] = doc

    # Reference-mode items are catalogued with canonical hrefs (the app resolves
    # them through ingest_files.source_href at request time); the planner needs
    # the same resolution or it grants a key that does not exist (G-6 Task 9b).
    source_hrefs: dict[tuple[str, str], dict[str, str]] = {}
    for (coll, item_id), doc in documents.items():
        if any(
            parse_canonical_href(a.get("href"), settings.asset_href_base)
            for a in (doc.get("assets") or {}).values()
            if isinstance(a, dict)
        ):
            source_hrefs[(coll, item_id)] = await repo.reference_source_hrefs(coll, item_id)

    try:
        plan = plan_inputs(
            run_id=run.id,
            process_id=run.process_id,
            # Slice 1: exactly one batch per run (§3.1).
            batch_id=uuid.uuid4().hex,
            kind=KIND_EXTRACT if is_extract else KIND_TRANSFORM,
            refs=run.input_items,
            documents=documents,
            source_collections=source_collections,
            bucket=settings.staging_bucket,
            asset_href_base=settings.asset_href_base,
            source_hrefs=source_hrefs,
        )
    except InputPlanError as err:
        await _finish(repo, run, "dead", None, f"unusable inputs: {err}", None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))

    if fetch_remote is None:

        async def fetch_remote(href: str) -> bytes:
            raise InputStagingError(f"no remote fetcher configured for {href!r}")

    try:
        await stage_inputs(
            plan,
            storage_client=storage_client,
            bucket=settings.staging_bucket,
            fetch_remote=fetch_remote,
            concurrency=settings.process_input_stage_concurrency,
        )
    except InputStagingError as err:
        # The run's inputs could not be fetched — a per-run outcome that spends
        # an attempt (the source may be flaky), then goes dead on budget.
        transition = outcome_transition(
            exit_code=1,
            timed_out=False,
            attempts=run.attempts,
            max_attempts=runtime.max_attempts,
            now=at,
            retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS,
            error=str(err),
        )
        await _finish(
            repo,
            run,
            transition.status,
            None,
            transition.error,
            transition.next_attempt_at,
            at,
            on_dead=on_dead,
        )
        return RunResult(run.id, transition.status, error=transition.error)

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
            read_prefixes=plan.read_prefixes,
            extra_env=input_env(run.id, plan.manifest_key),
            profile=profile,
            priority="interactive" if run.is_test else "triggered",
        )
    except (ExecutorUnavailable, RunCredentialsError) as err:
        # OUR failure, not the process's: back to `queued` without spending an
        # attempt (see ledger.infrastructure_transition).
        transition = infrastructure_transition(
            now=at, retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS, error=str(err)
        )
        PROCESS_RUNS.labels(outcome=transition.status).inc()
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
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
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
        on_dead=on_dead,
    )
    return RunResult(run.id, transition.status, log_ref=outcome.log_ref, error=transition.error)


async def _finish(
    repo: ProcessRepo,
    run: QueuedRun,
    status: str,
    log_ref: str | None,
    error: str | None,
    next_attempt_at: dt.datetime | None,
    at: dt.datetime,
    *,
    on_dead: Callable[[QueuedRun, str], Awaitable[None]] | None = None,
) -> None:
    PROCESS_RUNS.labels(outcome=status).inc()
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
        await repo.record_source_run(run.source_id, succeeded=status == "succeeded", at=at)
    # G-6: a dead extractor run fails every ledger row in its batch (spec
    # §6.2, §15: at TERMINAL dead, never on a retryable attempt — the retry
    # may still land the batch).
    if status == "dead" and run.association_id is not None and on_dead is not None:
        await on_dead(run, error or "run died")
