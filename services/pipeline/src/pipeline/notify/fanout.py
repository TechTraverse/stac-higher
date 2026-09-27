"""Notification fan-out tick + the per-delivery webhook handler (M2-C, §4).

The periodic tick mirrors the repo's other sweep-owned recovery paths
(delivery retry, ingest stall): fan newly firing alerts out into the
``notification_deliveries`` ledger, revive stalled claims, and enqueue a
webhook job per dispatchable row. The queue provides transport; the LEDGER
provides durability and backoff — a crash at any point is retried from row
state on a later tick, and a duplicate enqueue no-ops at claim time.

Only NEW alert rows notify (spec §3.3): a ``last_seen`` bump on an open row is
detection continuing, not news, and an ``acknowledged`` row was never fanned
out to begin with if the ack landed first — ack suppresses notification.
In-app channels have no dispatch at all: the alerts row plus per-user read
state IS the in-app delivery.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from pipeline.flow.repo import AlertCondition
from pipeline.notify.config import NotificationConfigError, parse_webhook_config
from pipeline.notify.repo import (
    WEBHOOK_FAILED_KIND,
    ClaimedDelivery,
    NotifiableAlert,
    NotifyRepo,
)
from pipeline.notify.webhook import WebhookDeliveryError, deliver

logger = logging.getLogger(__name__)

Enqueue = Callable[[str], Awaitable[None]]

WEBHOOK_JOB_NAME = "pipeline.webhook_notify"


@dataclass(frozen=True)
class NotifyTickResult:
    fanned_out: int  # alerts stamped notified this tick
    deliveries_created: int  # new ledger rows
    revived: int  # stalled claims flipped back into the retry path
    enqueued: int  # webhook jobs enqueued


def _webhook_failed_condition(channel_id: str, error: str) -> AlertCondition:
    return AlertCondition(
        source="job_failure",
        kind=WEBHOOK_FAILED_KIND,
        channel_id=channel_id,
        message=f"webhook channel failed terminally: {error}",
    )


async def notify_tick(
    repo: NotifyRepo,
    enqueue: Enqueue,
    *,
    max_attempts: int,
    retry_seconds: int,
    stall_seconds: int,
) -> NotifyTickResult:
    """One fan-out + dispatch pass."""
    # 1. Fan newly firing alerts out to their group's webhook channels. The
    #    inserts are idempotent and notified_at is stamped last, so a crash
    #    mid-loop re-runs harmlessly (at-least-once).
    fanned_out = 0
    deliveries_created = 0
    for alert in await repo.list_unnotified_alerts():
        if alert.group_id is not None:
            channels = await repo.list_group_webhook_channels(alert.group_id)
            # Never ask a channel to announce its own failure.
            channel_ids = [c.id for c in channels if c.id != alert.channel_id]
            deliveries_created += await repo.create_pending_deliveries(
                alert.id, channel_ids
            )
        await repo.mark_notified(alert.id)
        fanned_out += 1

    # 2. Revive claims stranded by a crashed worker; a revival that hits the
    #    attempt cap is a terminal failure like any other.
    revived_rows = await repo.revive_stalled_deliveries(stall_seconds, max_attempts)
    for _delivery_id, channel_id, status in revived_rows:
        if status == "dead":
            await repo.raise_alert(
                _webhook_failed_condition(channel_id, "stalled mid-delivery")
            )

    # 3. Enqueue a job per dispatchable row (pending, or failed past cool-off).
    dispatchable = await repo.list_dispatchable_deliveries(max_attempts, retry_seconds)
    for delivery_id in dispatchable:
        await enqueue(delivery_id)

    return NotifyTickResult(
        fanned_out=fanned_out,
        deliveries_created=deliveries_created,
        revived=len(revived_rows),
        enqueued=len(dispatchable),
    )


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_payload(alert: NotifiableAlert) -> bytes:
    """The webhook POST body — a stable, documented shape (ADR 0010)."""
    return json.dumps(
        {
            "event": "alert.firing",
            "alert": {
                "id": alert.id,
                "source": alert.source,
                "kind": alert.kind,
                "message": alert.message,
                "group_id": alert.group_id,
                "connection_id": alert.connection_id,
                "connection_name": alert.connection_name,
                "association_id": alert.association_id,
                "collection_id": alert.collection_id,
                "process_id": alert.process_id,
                "first_seen": _iso(alert.first_seen),
                "last_seen": _iso(alert.last_seen),
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


async def handle_webhook_delivery(
    repo: NotifyRepo,
    delivery_id: str,
    *,
    max_attempts: int,
    allow_hosts: Iterable[str] = (),
    timeout: float = 10.0,
    send: Callable[..., None] = deliver,
) -> str:
    """Execute one webhook delivery job. Returns the resulting ledger status
    (``delivered`` / ``failed`` / ``dead`` / ``skipped``).

    Failures are recorded in the LEDGER and never re-raised — queue-level
    retry is deliberately not used, so backoff and the attempt cap live in one
    place (the sweep + ledger, like every other transfer).
    """
    claimed = await repo.claim_delivery(delivery_id)
    if claimed is None:
        return "skipped"
    try:
        config = parse_webhook_config(claimed.channel_config)
        body = build_payload(claimed.alert)
        await asyncio.to_thread(
            send, config, body, allow_hosts=allow_hosts, timeout=timeout
        )
    except (NotificationConfigError, WebhookDeliveryError) as exc:
        return await _record_failure(repo, claimed, str(exc), max_attempts)
    except Exception as exc:  # a webhook must never kill the worker
        logger.exception(
            "webhook delivery failed unexpectedly",
            extra={"delivery_id": delivery_id, "channel_id": claimed.channel_id},
        )
        return await _record_failure(repo, claimed, str(exc), max_attempts)
    await repo.record_delivery_success(delivery_id, claimed.channel_id)
    return "delivered"


async def _record_failure(
    repo: NotifyRepo, claimed: ClaimedDelivery, error: str, max_attempts: int
) -> str:
    status = await repo.record_delivery_failure(claimed.id, error, max_attempts)
    if status == "dead":
        await repo.raise_alert(_webhook_failed_condition(claimed.channel_id, error))
    return status
