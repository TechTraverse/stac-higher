"""Periodic drain of the app→pipeline test-connection bridge (ADR 0004).

Each tick claims pending ``stac_higher.connection_checks`` rows
(``FOR UPDATE SKIP LOCKED``), runs the connection's adapter ``test()`` (which
enforces egress), writes the check result, and updates the parent connection's
health + TOFU host-key pin. It drains ALL pending rows each run.

Cron granularity note (accepted deviation): Procrastinate's periodic scheduler
is 1-minute-granular, so ADR 0004's "~10 s" target is approximated by a
1-minute tick that clears the whole pending backlog at once. The true sub-minute
latency needs a NOTIFY-woken drain — flagged in ADR 0004 "Revisit"; not built in
Phase 2.
"""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.connections.probe import probe_connection
from pipeline.connections.repo import ConnectionsRepo, PgConnectionsRepo
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.connection_check_drain"
CRON = "* * * * *"
DEFAULT_BATCH_SIZE = 20
#: safety cap so a table that keeps refilling can't spin a tick forever.
MAX_BATCHES_PER_TICK = 1000


async def drain_tick(
    repo: ConnectionsRepo,
    master_key: bytes,
    allow_hosts: frozenset[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = MAX_BATCHES_PER_TICK,
) -> int:
    """Drain pending checks until none remain (or the safety cap). Returns the
    number of checks processed this tick.
    """
    processed = 0
    for _ in range(max_batches):
        claimed = await repo.claim_pending(batch_size)
        if not claimed:
            break
        for check in claimed:
            outcome = await probe_connection(check.connection, master_key, allow_hosts)
            check_status = "done" if outcome.ok else "failed"
            await repo.record_check(check.check_id, check_status, outcome.result)
            await repo.update_connection_health(
                check.connection.id,
                status=outcome.connection_status,
                last_error=outcome.last_error,
                host_key_to_pin=outcome.host_key_to_pin,
            )
            processed += 1
            logger.info(
                "connection check completed",
                extra={
                    "check_id": check.check_id,
                    "connection_id": check.connection.id,
                    "protocol": check.connection.protocol,
                    "ok": outcome.ok,
                },
            )
    else:
        logger.warning(
            "connection drain hit its per-tick batch cap; more may remain",
            extra={"max_batches": max_batches, "batch_size": batch_size},
        )
    return processed


def register(queue: QueueBackend, settings: Settings) -> None:
    async def drain(timestamp: int) -> None:
        master_key = load_key_or_skip(settings, JOB_NAME)
        if master_key is None:
            return
        repo = PgConnectionsRepo(settings.database_url)
        count = await drain_tick(repo, master_key, settings.egress_allow_hosts)
        if count:
            logger.info(
                "connection drain tick done",
                extra={"processed": count, "scheduled_timestamp": timestamp},
            )

    queue.register_periodic(drain, name=JOB_NAME, cron=CRON)
