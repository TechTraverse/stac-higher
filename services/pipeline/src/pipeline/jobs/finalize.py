"""Finalize job wiring (Phase 7 spec §6.4, §9).

``pipeline.finalize`` runs one push finalize: the dispatcher (P7-F) enqueues
one job per staged item event — payload ``{upload_id, collection_id, item_id,
event_op}`` — BEFORE draining the event (enqueue-before-drain, at-least-once,
the delivery pattern). The ledger claim inside the resolver makes concurrent
duplicates no-op, so at-least-once is safe.

``pipeline.finalize_sweep`` is the §6.4 crash-recovery tick: expire ``pending``
rows past the §4.1 TTL clock and re-enqueue stale ``finalizing`` claims.

Both are registered through the queue backend, so ``instrument_handler`` wraps
them centrally — ``pipeline_job_runs_total``/``pipeline_job_seconds`` come for
free (M2-H); the per-item §9 counters are incremented inside the steps.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.config import Settings
from pipeline.finalize.process_run import (
    DEFAULT_CATALOG_HREF_BASE,
    ProcessRunRecorder,
    ProcessRunResolver,
)
from pipeline.finalize.push import PushRecorder, PushResolver, build_push_request
from pipeline.finalize.repo import PgFinalizeRepo
from pipeline.finalize.seam import (
    PRODUCER_PROCESS_RUN,
    PRODUCER_PUSH_INGEST,
    ProducerHooks,
)
from pipeline.finalize.steps import run_finalize
from pipeline.finalize.store import PlatformObjectStore
from pipeline.finalize.sweep import finalize_sweep_tick
from pipeline.process.repo import PgProcessRepo
from pipeline.queue.interface import QueueBackend, RetrySpec
from pipeline.stac.pgstac_writer import PgPgstacWriter
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_FINALIZE = "pipeline.finalize"
JOB_SWEEP = "pipeline.finalize_sweep"
SWEEP_CRON = "*/5 * * * *"
#: rows one sweep tick re-drives; the next tick drains more.
SWEEP_BATCH = 500
#: Queue-level retry (the I-55 idiom): a transient DB/storage fault before the
#: ledger claim would otherwise strand the row `pending` until TTL expiry —
#: the triggering outbox event is already drained and only the stale-claim
#: sweep (which needs a claim to exist) or a re-push would re-drive it.
FINALIZE_RETRY = RetrySpec(max_attempts=4, wait_seconds=60)


def build_hooks(
    repo: PgFinalizeRepo,
    writer: PgPgstacWriter,
    store: PlatformObjectStore,
    process_repo: PgProcessRepo,
    *,
    catalog_href_base: str = DEFAULT_CATALOG_HREF_BASE,
) -> dict[str, ProducerHooks]:
    """Both producers' hook pairs in ONE registry.

    Registering them together is what keeps the seam honest: `run_finalize`
    dispatches on `req.producer` and never branches on it, so a request naming
    either producer takes the identical step path (ADR 0014's check
    criterion).
    """
    return {
        PRODUCER_PUSH_INGEST: ProducerHooks(
            resolver=PushResolver(repo), recorder=PushRecorder(repo, writer)
        ),
        PRODUCER_PROCESS_RUN: ProducerHooks(
            resolver=ProcessRunResolver(store=store, catalog_href_base=catalog_href_base),
            recorder=ProcessRunRecorder(repo=process_repo),
        ),
    }


def register(queue: QueueBackend, settings: Settings) -> None:
    def _hooks(repo: PgFinalizeRepo, writer: PgPgstacWriter) -> dict[str, ProducerHooks]:
        store = PlatformObjectStore(
            client=build_platform_client(settings), bucket=settings.staging_bucket
        )
        return build_hooks(
            repo,
            writer,
            store,
            PgProcessRepo(settings.database_url),
            catalog_href_base=settings.catalog_href_base,
        )

    async def finalize_job(
        upload_id: str,
        collection_id: str,
        item_id: str,
        event_op: str | None = None,
    ) -> None:
        repo = PgFinalizeRepo(settings.database_url)
        writer = PgPgstacWriter(settings.database_url)
        store = PlatformObjectStore(
            client=build_platform_client(settings), bucket=settings.staging_bucket
        )
        req = build_push_request(upload_id, collection_id, item_id, event_op)
        await run_finalize(
            req,
            hooks=_hooks(repo, writer),
            preflight=repo,
            store=store,
            writer=writer,
            asset_href_base=settings.asset_href_base,
        )

    async def finalize_sweep(timestamp: int) -> None:
        repo = PgFinalizeRepo(settings.database_url)

        async def _enqueue(payloads: list[dict[str, Any]]) -> None:
            await queue.enqueue_batch(JOB_FINALIZE, payloads)

        result = await finalize_sweep_tick(
            repo,
            _enqueue,
            ttl_seconds=settings.staging_ttl_seconds,
            stale_seconds=settings.finalize_stale_seconds,
            batch_limit=SWEEP_BATCH,
        )
        if result.expired or result.requeued:
            logger.info(
                "finalize sweep",
                extra={
                    "expired": result.expired,
                    "requeued": result.requeued,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_task(finalize_job, name=JOB_FINALIZE, retry=FINALIZE_RETRY)
    queue.register_periodic(finalize_sweep, name=JOB_SWEEP, cron=SWEEP_CRON)
