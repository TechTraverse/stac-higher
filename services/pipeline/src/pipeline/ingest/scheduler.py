"""Ingest poll scheduler: decide which associations are due each tick.

Procrastinate's periodic scheduler is 1-minute-granular (same constraint as the
connection drain, ISSUES I-3), so a per-association ``poll_frequency_seconds`` is
modelled as **N whole minutes**: the poll task fires every minute and an
association is due when the tick's minute index is a multiple of its interval.
The tick ``timestamp`` (unix seconds of the scheduled run) makes this
deterministic across worker restarts — no per-association "last polled" state.
"""

from __future__ import annotations

import logging

from pipeline.ingest.config import IngestConfigError, parse_ingest_config
from pipeline.ingest.repo import IngestAssociation, IngestRepo

logger = logging.getLogger(__name__)


def poll_interval_ticks(poll_frequency_seconds: int) -> int:
    """Whole-minute interval for a poll frequency (floor of 1 minute)."""
    return max(1, round(poll_frequency_seconds / 60))


def is_due(poll_frequency_seconds: int, timestamp: int) -> bool:
    """True when the tick at ``timestamp`` should poll this frequency."""
    ticks = poll_interval_ticks(poll_frequency_seconds)
    return (timestamp // 60) % ticks == 0


async def due_associations(
    repo: IngestRepo, timestamp: int
) -> list[IngestAssociation]:
    """The enabled ingest associations whose poll interval lands on this tick.

    An association whose ``config`` won't parse is skipped (logged) rather than
    crashing the scheduler — one bad row must not stall every other flow.
    """
    associations = await repo.list_enabled_ingest_associations()
    due: list[IngestAssociation] = []
    for association in associations:
        try:
            config = parse_ingest_config(association.config)
        except IngestConfigError as exc:
            logger.error(
                "ingest scheduler: skipping association with invalid config",
                extra={"association_id": association.id, "error": str(exc)},
            )
            continue
        if is_due(config.poll_frequency_seconds, timestamp):
            due.append(association)
    return due
