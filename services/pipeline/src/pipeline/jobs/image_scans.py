"""Image scan drain (C-2, container-images spec §8.1, ADR 0021, ADR 0004).

Every minute: fail scans stalled past the policy timeout, then claim ONE
pending ``image_scans`` row (a deployment-wide cap of
``IMAGE_SCAN_CONCURRENCY`` running scans, default 1) and run it through
:func:`pipeline.images.drain.drain_one`. The scanner runs in
``asyncio.to_thread``: it blocks a worker slot for up to the policy timeout
until K-4 (ISSUES I-124), never the event loop.

A missing or invalid image policy skips the tick (fail closed, spec §7.2):
pending scans stay pending and nothing is scanned against a policy nobody
can read.

Before the drain, the same tick reconciles the ``process_image_flagged``
alerts (C-4, ``pipeline/images/alerts.py``): one per process whose current
revision uses a flagged, revoked, gone or stale image.

``pipeline.image_rescan_tick`` (``15 * * * *``, C-4) expires admin
exceptions (re-evaluating the latest scan, audited) and inserts one
``rescan`` row per image due for its periodic rescan; this drain runs them.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from pipeline import metrics
from pipeline.config import Settings
from pipeline.flow.repo import PgFlowMonitorRepo
from pipeline.images.alerts import PgImageAlertsRepo, sync_image_alerts
from pipeline.images.drain import STALL_GRACE_SECONDS, drain_one
from pipeline.images.drift import tag_drift
from pipeline.images.lifecycle import PgImageLifecycleRepo, lifecycle_tick
from pipeline.images.policy import ImagePolicyError, load_image_policy
from pipeline.images.registry_auth import (
    RegistryAuthUnavailable,
    RegistryCredentialGone,
    resolve_registry_auth,
)
from pipeline.images.repo import ClaimedScan, ImageRow, PgImagesRepo
from pipeline.images.scan_launch import ScanRun, execute_scan, read_scan_result
from pipeline.jobs._common import load_key_or_skip
from pipeline.process.docker_executor import DockerExecutor
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.image_scan_drain"
CRON = "* * * * *"

#: C-4 (spec §8.2): hourly, each image rescanned once its interval is up.
RESCAN_JOB_NAME = "pipeline.image_rescan_tick"
RESCAN_CRON = "15 * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def image_scan_drain(timestamp: int) -> None:  # pragma: no cover - needs a DB
        try:
            policy = load_image_policy()
        except ImagePolicyError as err:
            logger.error(
                "image scan drain skipped: the image policy is unavailable",
                extra={"job": JOB_NAME, "error": str(err)},
            )
            return
        # C-4 (spec §10): reconcile process_image_flagged BEFORE the drain,
        # so a 15-minute scan never delays it. A failure here must not stop
        # the drain.
        try:
            raised, resolved = await sync_image_alerts(
                PgImageAlertsRepo(settings.database_url),
                PgFlowMonitorRepo(settings.database_url).sync_alerts,
                scan_window_days=policy.scan_window_days,
                now=dt.datetime.now(dt.UTC),
            )
        except Exception:
            logger.exception("image alert sync failed", extra={"job": JOB_NAME})
        else:
            if raised:
                metrics.ALERTS.labels(event="raised").inc(raised)
            if resolved:
                metrics.ALERTS.labels(event="auto_resolved").inc(resolved)
            if raised or resolved:
                logger.info(
                    "image alerts reconciled",
                    extra={"raised": raised, "auto_resolved": resolved},
                )
        repo = PgImagesRepo(settings.database_url)
        started_before = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=policy.scan_timeout_seconds + STALL_GRACE_SECONDS
        )
        stalled = await repo.fail_stalled_scans(started_before=started_before)
        if stalled:
            logger.warning("stalled image scans failed", extra={"count": stalled})
        executor = DockerExecutor(docker_host=settings.docker_host)
        storage_client = build_platform_client(settings)

        async def run_scan(scan: ClaimedScan, kind: str) -> ScanRun:
            auth = None
            if kind == "admission":
                # The key is needed only for a private image's credential; a
                # deployment without one still scans public images.
                key = (
                    load_key_or_skip(settings, JOB_NAME)
                    if scan.image.registry_connection_id
                    else None
                )
                auth = await resolve_registry_auth(
                    scan.image, repo=repo, settings=settings, master_key=key
                )
            return await asyncio.to_thread(
                execute_scan,
                executor,
                settings,
                policy,
                storage_client,
                scan_id=scan.id,
                image=scan.image,
                kind=kind,
                registry_auth=auth,
            )

        async def read_result(key: str):
            return await asyncio.to_thread(
                read_scan_result, storage_client, settings.staging_bucket, key
            )

        async def check_drift(image: ImageRow) -> dict[str, Any]:
            # The pull credential the daemon would use, so a private tag can
            # be HEADed. If it cannot be resolved, the HEAD goes anonymous and
            # a private tag reads as unchecked (current_digest null), never a
            # failed scan.
            key = (
                load_key_or_skip(settings, JOB_NAME) if image.registry_connection_id else None
            )
            try:
                auth = await resolve_registry_auth(
                    image, repo=repo, settings=settings, master_key=key
                )
            except (RegistryCredentialGone, RegistryAuthUnavailable):
                auth = None
            return await asyncio.to_thread(tag_drift, image, auth, settings.egress_allow_hosts)

        outcome = await drain_one(
            repo,
            policy=policy,
            max_running=settings.image_scan_concurrency,
            run_scan=run_scan,
            read_result=read_result,
            clock=lambda: dt.datetime.now(dt.UTC),
            check_drift=check_drift,
        )
        if outcome is not None:
            logger.info(
                "image scan finished",
                extra={
                    "scan_id": outcome.scan_id,
                    "image_id": outcome.image_id,
                    "scan_status": outcome.scan_status,
                    "image_status": outcome.image_status,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(image_scan_drain, name=JOB_NAME, cron=CRON)

    async def image_rescan_tick(timestamp: int) -> None:  # pragma: no cover - needs a DB
        try:
            policy = load_image_policy()
        except ImagePolicyError as err:
            logger.error(
                "image rescan tick skipped: the image policy is unavailable",
                extra={"job": RESCAN_JOB_NAME, "error": str(err)},
            )
            return
        result = await lifecycle_tick(
            PgImageLifecycleRepo(settings.database_url),
            policy=policy,
            now=dt.datetime.now(dt.UTC),
        )
        if result.exceptions_expired or result.rescans_requested:
            logger.info(
                "image rescan tick",
                extra={
                    "exceptions_expired": result.exceptions_expired,
                    "flagged_on_expiry": result.flagged_on_expiry,
                    "rescans_requested": result.rescans_requested,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(image_rescan_tick, name=RESCAN_JOB_NAME, cron=RESCAN_CRON)
