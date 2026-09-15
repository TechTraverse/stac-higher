"""Delivery dispatch wiring (Slice B-i core; B-iii retry; Slice C NOTIFY wake).

The outbox drains through ``dispatch_until_empty``, which groups matches per
association and enqueues a batched ``pipeline.deliver`` job (Phase 7 §7:
staged items route to ``pipeline.finalize`` instead, and delete events mark
``asset_gc`` — reason ``item_delete``, grace from collection_settings —
before draining). Two wake paths
share that drain (overlap is safe — the outbox claim is atomic, I-40):
``build_notify_listener`` returns the LISTEN-woken primary loop (run by
main.py alongside the worker), and ``dispatch_poll`` keeps the minute cron as
fallback for dropped notifications / listener downtime.

The ``deliver`` handler pre-records a ``delivery_log`` row per item (M2-0,
ISSUES I-56) before anything fallible, then loads the destination connection,
builds its adapter, and runs each item through ``deliver_item`` (canonical
bytes → destination), bounded by the association's
``max_concurrent_transfers`` for S3 destinations (single-channel SFTP/FTP
clients are not concurrency-safe — those run serial regardless of the cap).
``delivery_retry_sweep`` first recovers rows stranded in
``pending``/``delivering`` past the stall window, then re-drives ``failed``
rows whose ``next_attempt_at`` has passed; ``deliver_item`` dead-letters at
``retry.max_attempts``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.config import DeliveryConfig, parse_delivery_config
from pipeline.delivery.repo import PgDeliveryRepo
from pipeline.delivery.transfer import can_server_side_copy
from pipeline.delivery.worker import deliver_item, retry_delay_seconds
from pipeline.dispatcher.listener import run_dispatch_listener
from pipeline.dispatcher.loop import dispatch_until_empty
from pipeline.dispatcher.repo import PgDispatchRepo
from pipeline.gc.repo import PgGcRepo
from pipeline.gc.sweep import item_prefix
from pipeline.jobs._common import load_key_or_skip
from pipeline.jobs.finalize import JOB_FINALIZE
from pipeline.jobs.process import JOB_TRIGGER as JOB_PROCESS_TRIGGER
from pipeline.queue.interface import QUEUE_BYTES, QueueBackend, RetrySpec
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_DISPATCH_POLL = "pipeline.dispatch_poll"
JOB_DELIVER = "pipeline.deliver"
JOB_RETRY_SWEEP = "pipeline.delivery_retry_sweep"
CRON = "* * * * *"
#: rows per sweep tick — bounds one tick's fan-out; the next tick drains more.
RETRY_SWEEP_BATCH = 500
#: Queue-level retry (ISSUES I-55): a transient fault BEFORE `deliver_item`'s
#: first delivery_log record (load_target / get_item DB errors) would
#: otherwise lose the delivery — the outbox row is already claimed and the
#: retry sweep has nothing to re-drive.
DELIVER_RETRY = RetrySpec(max_attempts=4, wait_seconds=60)
#: asset_gc reason for the §7.3 delete-event mark (migration-017 CHECK value —
#: the same reason the app's BFF item delete writes; the open-key unique index
#: makes the two idempotent against each other).
REASON_ITEM_DELETE = "item_delete"


def _entry_asset_keys(
    entry_keys: list[str] | None,
    item: dict[str, Any],
    config_asset_keys: tuple[str, ...] | None,
) -> list[str]:
    """Asset keys for one delivery. Dispatch-time entries carry the matcher's
    keys; sweep-requeued entries carry ``None`` and re-derive them here (same
    rule as the matcher: config ``asset_keys`` ∩ the item's assets, null = all),
    so a retry always reflects the item's CURRENT assets."""
    if entry_keys is not None:
        return entry_keys
    item_assets = list((item.get("assets") or {}).keys())
    if config_asset_keys is None:
        return item_assets
    wanted = set(config_asset_keys)
    return [k for k in item_assets if k in wanted]


def build_dispatch_drain(
    queue: QueueBackend, settings: Settings
) -> Any:
    """The shared outbox drain both wake paths call: claim → match → enqueue
    batched deliver jobs, until the outbox is empty."""

    async def run_dispatch(wake_path: str) -> None:
        repo = PgDispatchRepo(settings.database_url)
        gc_repo = PgGcRepo(settings.database_url)

        async def _enqueue(batches: list[dict[str, Any]]) -> None:
            await queue.enqueue_batch(JOB_DELIVER, batches)

        async def _enqueue_process_runs(batches: list[dict[str, Any]]) -> None:
            # Phase 9 §6: one trigger job per matched source per tick. The job
            # applies the §7 ceiling and writes the run row; dispatch stays
            # out of the rate decision so a slow ceiling check cannot stall
            # the outbox.
            await queue.enqueue_batch(JOB_PROCESS_TRIGGER, batches)

        async def _enqueue_finalize(payloads: list[dict[str, Any]]) -> None:
            # Staged gate (spec §7.1): one pipeline.finalize job per staged
            # event, P7-E payload contract, batched per claim.
            await queue.enqueue_batch(JOB_FINALIZE, payloads)

        async def _mark_delete_gc(collection_id: str, item_id: str) -> None:
            # §7.3: the mark SQL is PgGcRepo's (one place); grace comes from
            # the collection's settings row (the app's default when absent).
            grace = await repo.get_gc_grace_days(collection_id)
            await gc_repo.mark_asset_prefix(
                item_prefix(collection_id, item_id),
                collection_id,
                item_id,
                REASON_ITEM_DELETE,
                grace,
            )

        matches = await dispatch_until_empty(
            repo,
            _enqueue,
            enqueue_finalize=_enqueue_finalize,
            mark_delete_gc=_mark_delete_gc,
            enqueue_process_runs=_enqueue_process_runs,
        )
        if matches:
            logger.info(
                "dispatch enqueued delivery batches",
                extra={"matches": matches, "wake_path": wake_path},
            )

    return run_dispatch


def build_notify_listener(queue: QueueBackend, settings: Settings) -> Any:
    """The LISTEN-woken primary wake loop, for main.py to run alongside the
    worker. Returns a coroutine; runs until cancelled."""
    run_dispatch = build_dispatch_drain(queue, settings)

    async def _on_wake() -> None:
        await run_dispatch("notify")

    return run_dispatch_listener(settings.database_url, _on_wake)


def register(queue: QueueBackend, settings: Settings) -> None:
    run_dispatch = build_dispatch_drain(queue, settings)

    async def dispatch_poll(timestamp: int) -> None:
        await run_dispatch("poll")

    async def deliver(association_id: str, items: list[dict[str, Any]]) -> None:
        master_key = load_key_or_skip(settings, JOB_DELIVER)
        if master_key is None:
            return
        repo = PgDeliveryRepo(settings.database_url)
        # M2-0: record the intent BEFORE anything that can fail. Everything
        # below — load_target, config parsing, adapter build, get_item — used
        # to run with NO delivery_log row in existence, so a fault that
        # outlived the queue retries lost the delivery silently: the outbox row
        # was already claimed and the retry sweep had nothing to re-drive.
        # INSERT-only, so an existing row's state (which deliver_item's
        # on_update/overwrite gates read) is untouched.
        pre = await repo.pre_record(
            association_id,
            [(entry["item_id"], entry.get("item_created_at")) for entry in items],
        )

        async def _fail_batch(error: str, config: DeliveryConfig) -> None:
            """Settle every pre-recorded row on the same terms deliver_item
            uses — the attempt was made, it just failed before any byte moved."""
            for record in pre:
                attempts = await repo.mark_delivering(record.id)
                dead = attempts >= config.max_attempts
                await repo.mark_failed(
                    record.id,
                    error,
                    # Preserve the fingerprint map: this path never delivered
                    # anything, so wiping it would force a needless rewrite.
                    delivered_assets=record.delivered_assets,
                    next_attempt_at=None
                    if dead
                    else dt.datetime.now(dt.UTC)
                    + dt.timedelta(
                        seconds=retry_delay_seconds(config.backoff, attempts)
                    ),
                    dead=dead,
                )

        target = await repo.load_target(association_id)
        if target is None:
            # Association disabled/deleted between dispatch and delivery — a
            # legitimate no-op, so drop the placeholders we just created rather
            # than leave phantom pending rows for the stall sweep.
            await repo.discard_pre_records([r.id for r in pre if r.created])
            return
        try:
            config = parse_delivery_config(target.config)
        except Exception as exc:  # surfaced on the rows, not swallowed (BLE001 off)
            logger.exception(
                "deliver: invalid delivery config",
                extra={"association_id": association_id},
            )
            await _fail_batch(f"invalid delivery config: {exc}", DeliveryConfig())
            return
        try:
            adapter = build_adapter(
                target.connection, master_key, settings.egress_allow_hosts
            )
        except AdapterBuildError as exc:
            logger.exception(
                "deliver: adapter build failed",
                extra={"association_id": association_id},
            )
            await _fail_batch(f"destination adapter unavailable: {exc}", config)
            return
        s3_client = build_platform_client(settings)

        def _source_adapter(connection: ConnectionRow):
            # Reference-mode assets: decrypt + build the ingest source adapter
            # on demand (worker caches per connection).
            return build_adapter(connection, master_key, settings.egress_allow_hosts)

        server_side_copy = can_server_side_copy(
            target.connection.protocol,
            (target.connection.config or {}).get("endpoint"),
            settings.staging_s3_endpoint,
        )
        async def _deliver_entry(entry: dict[str, Any]) -> None:
            item = await repo.get_item(target.collection_id, entry["item_id"])
            if item is None:
                return
            asset_keys = _entry_asset_keys(
                entry.get("asset_keys"), item, config.asset_keys
            )
            if not asset_keys:
                return
            await deliver_item(
                repo,
                adapter,
                s3_client,
                settings.staging_bucket,
                target=target,
                config=config,
                item=item,
                asset_keys=asset_keys,
                item_created_at=entry.get("item_created_at"),
                build_source_adapter=_source_adapter,
                server_side_copy=server_side_copy,
            )

        # Per-connection concurrency cap (§6.4): honor max_concurrent_transfers
        # for S3 destinations; SFTP/FTP clients are single-channel and not
        # concurrency-safe, so they run serial regardless of the cap.
        cap = (
            config.max_concurrent_transfers
            if target.connection.protocol == "s3"
            else 1
        )
        if cap <= 1:
            for entry in items:
                await _deliver_entry(entry)
        else:
            semaphore = asyncio.Semaphore(cap)

            async def _bounded(entry: dict[str, Any]) -> None:
                async with semaphore:
                    await _deliver_entry(entry)

            # deliver_item never raises (failures land in delivery_log), so a
            # plain gather cannot lose siblings to one bad item.
            await asyncio.gather(*(_bounded(entry) for entry in items))

    async def retry_sweep(timestamp: int) -> None:
        repo = PgDeliveryRepo(settings.database_url)
        # M2-0 stall recovery first: rows stranded in pending/delivering (the
        # job died between pre-record and delivery, or a worker died
        # mid-transfer) become failed-and-due, so this same tick re-enqueues
        # them. Mirrors the ingest stored-stall sweep (I-52/I-55).
        recovered = await repo.sweep_stalled_deliveries(
            settings.delivery_stall_seconds, RETRY_SWEEP_BATCH
        )
        if recovered:
            logger.info(
                "delivery stall sweep recovered stranded rows",
                extra={"rows": recovered, "scheduled_timestamp": timestamp},
            )
        due = await repo.list_due_retries(RETRY_SWEEP_BATCH)
        if not due:
            return
        # Flip to pending FIRST so the next tick cannot re-enqueue the same
        # rows while this batch is still queued.
        await repo.requeue_for_retry([row.id for row in due])
        batches: dict[str, dict[str, Any]] = {}
        for row in due:
            batch = batches.setdefault(
                row.association_id,
                {"association_id": row.association_id, "items": []},
            )
            batch["items"].append(
                {
                    "item_id": row.item_id,
                    # None ⇒ the deliver job re-derives keys from the item's
                    # current assets (the original match is minutes stale).
                    "asset_keys": None,
                    "item_created_at": row.item_created_at,
                }
            )
        await queue.enqueue_batch(JOB_DELIVER, list(batches.values()))
        logger.info(
            "retry sweep re-enqueued failed deliveries",
            extra={"rows": len(due), "scheduled_timestamp": timestamp},
        )

    queue.register_periodic(dispatch_poll, name=JOB_DISPATCH_POLL, cron=CRON)
    queue.register_periodic(retry_sweep, name=JOB_RETRY_SWEEP, cron=CRON)
    queue.register_task(deliver, name=JOB_DELIVER, retry=DELIVER_RETRY, queue=QUEUE_BYTES)
