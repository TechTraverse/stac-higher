"""Process job wiring (Phase 9 §6, M5-C).

Five registrations:

- ``pipeline.process_trigger`` — one job per dispatched process-source batch.
  It does NOT execute; it applies the §7 rate ceiling and writes a queued (or
  deferred, coalesced) run row. Enqueue and execution are split so the
  ceiling's decision is durable in the ledger rather than living in a worker's
  memory.
- ``pipeline.process_run_tick`` — claims due runs and executes them.
- ``pipeline.process_cron`` — the minute tick that enqueues due cron sources.
- ``pipeline.process_sweep`` — stall recovery.
- ``pipeline.process_reap`` — orphaned-container reconciliation (M3-W-1).
  Separate from the sweep on purpose: the sweep is a pure DB job, and giving
  it an executor would couple the ledger's failure domain to Docker's.

All go through the queue backend, so ``instrument_handler`` wraps them
centrally (M2-H) and the per-job run/duration/outcome metrics come for free.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from pipeline.config import Settings
from pipeline.connections.envelope import decrypt, load_master_key
from pipeline.finalize.process_run import build_process_request
from pipeline.finalize.repo import PgFinalizeRepo
from pipeline.finalize.steps import run_finalize
from pipeline.finalize.store import PlatformObjectStore
from pipeline.jobs._common import load_key_or_skip
from pipeline.jobs.finalize import build_hooks
from pipeline.process.cron import is_due
from pipeline.process.docker_executor import DockerExecutor
from pipeline.process.reaper import process_reap_tick
from pipeline.process.repo import PgProcessRepo
from pipeline.process.runner import run_one
from pipeline.process.staging import build_remote_fetcher
from pipeline.process.sweep import process_sweep_tick
from pipeline.process.trigger import trigger_run
from pipeline.queue.interface import QueueBackend, RetrySpec
from pipeline.stac.pgstac_writer import PgPgstacWriter
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_TRIGGER = "pipeline.process_trigger"
JOB_RUN_TICK = "pipeline.process_run_tick"
JOB_FINALIZE = "pipeline.process_finalize"
JOB_CRON = "pipeline.process_cron"
JOB_SWEEP = "pipeline.process_sweep"
JOB_REAP = "pipeline.process_reap"

#: The run tick and the cron tick are both minute-granular: cron schedules are
#: minute-resolution, and a queued run should not wait longer than that to
#: start.
RUN_TICK_CRON = "* * * * *"
CRON_TICK_CRON = "* * * * *"
SWEEP_CRON = "*/5 * * * *"
#: Orphans are rare and never urgent — a leaked container costs disk, not
#: correctness — and every tick is a daemon round trip, so this is the
#: slowest cadence that still bounds the leak to an hour's worth.
REAP_CRON = "*/15 * * * *"

#: Runs claimed per tick. Bounded so one tick cannot monopolise a worker.
RUN_BATCH = 10
SWEEP_BATCH = 200

#: Queue-level retry for the TRIGGER job only (the I-55 idiom): a transient DB
#: fault before the run row is written would otherwise lose the trigger — the
#: outbox event is already drained. The run tick needs none: its work is
#: already durable in the ledger and the next tick picks it up.
TRIGGER_RETRY = RetrySpec(max_attempts=4, wait_seconds=30)


def build_secret_resolver(settings: Settings):
    """Resolve a `secret_ref` to plaintext, in the WORKER only (ROADMAP §5.2).

    Injected into the launch path rather than imported there, so the module
    that assembles a run's environment never imports the master key at all.

    NOTE (M5-0 follow-up, still open): a `secret_ref` names a key inside a
    CONNECTION's credentials envelope, and those are closed per-protocol
    shapes — so today a ref can only reach one of ~8 endpoint-credential
    fields. The unresolvable case raises, and the runner turns that into a
    dead run rather than silently handing user code an empty variable.
    """

    async def _resolve(ref) -> str:  # pragma: no cover - needs a DB + key
        import json

        import psycopg

        # Raises loudly when the key is unset or malformed — a run must never
        # start with a secret it could not resolve.
        key = load_master_key({"CREDENTIALS_MASTER_KEY": settings.credentials_master_key or ""})
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                "SELECT credentials FROM stac_higher.connections"
                " WHERE id = %s AND deleted_at IS NULL",
                (ref.connection_id,),
            )
            row = await cur.fetchone()
        if not row or row[0] is None:
            raise RuntimeError(f"connection {ref.connection_id} has no credentials")
        payload = json.loads(decrypt(bytes(row[0]), key))
        if ref.key not in payload:
            raise RuntimeError(f"credential key {ref.key!r} is not set on the connection")
        return str(payload[ref.key])

    return _resolve


def register(queue: QueueBackend, settings: Settings) -> None:
    def _repo() -> PgProcessRepo:
        return PgProcessRepo(settings.database_url)

    async def process_trigger(
        source_id: str | None,
        process_id: str,
        items: list[dict[str, Any]] | None = None,
        revision_id: str | None = None,
        is_test: bool = False,
    ) -> None:
        repo = _repo()
        now = dt.datetime.now(dt.UTC)
        if revision_id is None:
            # The dispatcher matched on the source; the revision to pin is
            # whatever is CURRENT at trigger time, resolved here so a deploy
            # racing a dispatch pins the deployed one, not a stale read.
            #
            # M5-G caught this reading through list_due_cron_sources, which
            # filters to `cron` triggers — so an item_event trigger never
            # resolved a revision and every run was silently dropped as
            # "nothing deployed". A single-row read cannot make that mistake.
            revision_id = await repo.current_revision(process_id)
        if not revision_id:
            logger.warning(
                "process trigger skipped: nothing deployed",
                extra={"process_id": process_id, "source_id": source_id},
            )
            return
        result = await trigger_run(
            repo,
            process_id=process_id,
            revision_id=revision_id,
            source_id=source_id,
            input_items=items or [],
            now=now,
            is_test=is_test,
        )
        logger.info(
            "process run queued",
            extra={
                "process_id": process_id,
                "source_id": source_id,
                "run_id": result.run_id,
                "deferred": result.deferred,
                "items": len(items or []),
            },
        )

    async def process_run_tick(timestamp: int) -> None:
        repo = _repo()
        now = dt.datetime.now(dt.UTC)
        runs = await repo.claim_due_runs(now, RUN_BATCH)
        if not runs:
            return
        executor = DockerExecutor(docker_host=settings.docker_host)
        storage_client = build_platform_client(settings)
        resolver = build_secret_resolver(settings)
        # GOES spec §3.2: remote inputs are staged through a matching
        # reference-mode association's adapter, else a public GET. A missing
        # master key must NOT skip the tick — only private reference sources
        # lose their adapter path (the fetcher falls back to public-only).
        master_key = load_key_or_skip(settings, JOB_RUN_TICK)
        fetch_remote = build_remote_fetcher(settings, master_key)
        finalize_payloads: list[dict[str, Any]] = []
        for run in runs:
            # Per-run isolation: one run's failure must never abandon the rest
            # of the claimed batch in `running`, where only the stall sweep
            # would recover them.
            try:
                result = await run_one(
                    run,
                    repo=repo,
                    executor=executor,
                    settings=settings,
                    storage_client=storage_client,
                    resolve_secret=resolver,
                    now=now,
                    fetch_remote=fetch_remote,
                )
            except Exception:
                logger.exception(
                    "process run raised outside the ledger path",
                    extra={"run_id": run.id, "process_id": run.process_id},
                )
                continue
            # Only a SUCCESSFUL run publishes (ADR 0014). A failed run's
            # partial outputs stay in staging and age out with the TTL sweep:
            # publishing half of what a crashed run intended would put items
            # in the catalog that no successful run stands behind.
            if result.status == "succeeded":
                finalize_payloads.append(
                    {"run_id": run.id, "process_id": run.process_id}
                )

        if finalize_payloads:
            await queue.enqueue_batch(JOB_FINALIZE, finalize_payloads)

    async def process_finalize(run_id: str, process_id: str) -> None:
        """Publish one successful run's outputs through the ADR 0014 seam."""
        process_repo = _repo()
        outputs = await process_repo.list_output_collections(process_id)
        if not outputs:
            logger.warning(
                "process run produced outputs but the process has no output"
                " collection; nothing published",
                extra={"run_id": run_id, "process_id": process_id},
            )
            return
        finalize_repo = PgFinalizeRepo(settings.database_url)
        writer = PgPgstacWriter(settings.database_url)
        store = PlatformObjectStore(
            client=build_platform_client(settings), bucket=settings.staging_bucket
        )
        await run_finalize(
            build_process_request(run_id, outputs),
            hooks=build_hooks(finalize_repo, writer, store, process_repo),
            preflight=finalize_repo,
            store=store,
            writer=writer,
            asset_href_base=settings.asset_href_base,
        )

    async def process_cron(timestamp: int) -> None:
        repo = _repo()
        now = dt.datetime.now(dt.UTC)
        for source in await repo.list_due_cron_sources(now):
            if not is_due(source.trigger, now, source.last_run_at):
                continue
            if not source.current_revision:
                continue
            await trigger_run(
                repo,
                process_id=source.process_id,
                revision_id=source.current_revision,
                source_id=source.id,
                # A cron run has no trigger items by definition (§6).
                input_items=[],
                now=now,
            )

    async def process_sweep(timestamp: int) -> None:
        await process_sweep_tick(
            _repo(),
            stall_seconds=settings.process_run_stall_seconds,
            batch_limit=SWEEP_BATCH,
        )

    async def process_reap(timestamp: int) -> None:
        await process_reap_tick(
            executor=DockerExecutor(docker_host=settings.docker_host),
            repo=_repo(),
        )

    queue.register_task(process_trigger, name=JOB_TRIGGER, retry=TRIGGER_RETRY)
    queue.register_task(process_finalize, name=JOB_FINALIZE, retry=TRIGGER_RETRY)
    queue.register_periodic(process_run_tick, name=JOB_RUN_TICK, cron=RUN_TICK_CRON)
    queue.register_periodic(process_cron, name=JOB_CRON, cron=CRON_TICK_CRON)
    queue.register_periodic(process_sweep, name=JOB_SWEEP, cron=SWEEP_CRON)
    queue.register_periodic(process_reap, name=JOB_REAP, cron=REAP_CRON)
