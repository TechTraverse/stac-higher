"""Periodic health sweep of all enabled connections (ADR 0004).

Every ~5 minutes, test each enabled connection and update its health columns
(status, last_checked_at, last_error) plus the TOFU host-key pin on a first
successful SSH-family test. No ``connection_checks`` rows are involved — this is
background monitoring, not a user-requested test. ``connections.updated_at`` is
never touched.
"""

from __future__ import annotations

import asyncio
import logging

from pipeline.config import Settings
from pipeline.connections.probe import probe_connection
from pipeline.connections.repo import ConnectionRow, ConnectionsRepo, PgConnectionsRepo
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.connection_health_sweep"
CRON = "*/5 * * * *"
#: cap on concurrent probes so a big enabled set can't open unbounded sockets.
SWEEP_CONCURRENCY = 8


async def sweep_tick(
    repo: ConnectionsRepo,
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> int:
    """Test every enabled connection and update its health. Returns the count
    swept.

    Probes run concurrently (bounded by ``SWEEP_CONCURRENCY``): they are
    independent and network-bound, so a serial loop would make the sweep's wall
    time the SUM of every probe's timeout — one unreachable host (15 s connect
    timeout) could push a large sweep past the 5-minute interval.
    """
    connections = await repo.list_enabled_connections()
    sem = asyncio.Semaphore(SWEEP_CONCURRENCY)

    async def _check(connection: ConnectionRow) -> None:
        async with sem:
            outcome = await probe_connection(connection, master_key, allow_hosts)
            await repo.update_connection_health(
                connection.id,
                status=outcome.connection_status,
                last_error=outcome.last_error,
                host_key_to_pin=outcome.host_key_to_pin,
            )
            logger.info(
                "connection health checked",
                extra={
                    "connection_id": connection.id,
                    "protocol": connection.protocol,
                    "ok": outcome.ok,
                },
            )

    await asyncio.gather(*(_check(c) for c in connections))
    return len(connections)


def register(queue: QueueBackend, settings: Settings) -> None:
    async def sweep(timestamp: int) -> None:
        master_key = load_key_or_skip(settings, JOB_NAME)
        if master_key is None:
            return
        repo = PgConnectionsRepo(settings.database_url)
        count = await sweep_tick(repo, master_key, settings.egress_allow_hosts)
        logger.info(
            "connection health sweep done",
            extra={"swept": count, "scheduled_timestamp": timestamp},
        )

    queue.register_periodic(sweep, name=JOB_NAME, cron=CRON)
