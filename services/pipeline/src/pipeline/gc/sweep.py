"""Retention expiry + asset collection ticks (M2-F, spec §5.2, ADR 0011).

Two periodic passes over pure repo/storage seams:

- :func:`retention_tick` — for every collection with declared retention (or
  archived, ADR 0009), expire items in batches: **mark first, then delete**.
  A crash between the two re-runs harmlessly (the item is still expired next
  tick and the mark upsert is idempotent); the reverse order could orphan
  bytes — the exact bug (I-51) this slice closes.
- :func:`collect_tick` — delete platform-bucket objects under each due mark's
  key prefix and stamp ``collected_at``. Errors keep the mark open (retried
  every tick); an empty prefix (reference-mode item) is a normal zero.

Deleting from pgstac emits ``delete`` outbox events; the dispatcher already
drops them without matching (spec §6.4 — deletions never propagate to
delivery destinations).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pipeline.cubes.config import CUBE_ITEM_ID
from pipeline.gc.repo import GcRepo
from pipeline.storage.keys import CANONICAL_PREFIX

logger = logging.getLogger(__name__)

#: reason values (mirrors the migration-017 CHECK; the app writes the other two)
REASON_RETENTION = "retention"
REASON_ARCHIVE = "archive"


def item_prefix(collection_id: str, item_id: str) -> str:
    """The §5.3 canonical prefix holding ALL of one item's asset objects."""
    return f"{CANONICAL_PREFIX}/{collection_id}/{item_id}/"


@dataclass(frozen=True)
class RetentionTickResult:
    collections: int
    expired_items: int
    marks_created: int


async def retention_tick(repo: GcRepo, *, batch_limit: int) -> RetentionTickResult:
    collections = 0
    expired = 0
    marks = 0
    for c in await repo.list_gc_collections():
        collections += 1
        reason = REASON_ARCHIVE if c.archived else REASON_RETENTION
        items = await repo.list_expired_items(
            c.collection_id,
            c.retention_days,
            batch_limit,
            retention_max_items=c.retention_max_items,
        )
        for item_id in items:
            # Mark BEFORE delete — see module docstring. Never for `_cube`:
            # that prefix is a cube repository's (ADR 0022), not the item's.
            if item_id != CUBE_ITEM_ID and await repo.mark_asset_prefix(
                item_prefix(c.collection_id, item_id),
                c.collection_id,
                item_id,
                reason,
                c.gc_grace_days,
            ):
                marks += 1
            if await repo.delete_item(item_id, c.collection_id):
                expired += 1
    return RetentionTickResult(
        collections=collections, expired_items=expired, marks_created=marks
    )


@dataclass(frozen=True)
class CollectTickResult:
    collected_marks: int
    deleted_objects: int
    errors: int


async def collect_tick(
    repo: GcRepo,
    delete_under_prefix: Callable[[str], int],
    *,
    batch_limit: int,
) -> CollectTickResult:
    """``delete_under_prefix`` is blocking (boto3) — run via ``to_thread``."""
    collected = 0
    deleted = 0
    errors = 0
    for mark in await repo.list_due_marks(batch_limit):
        try:
            deleted += await asyncio.to_thread(delete_under_prefix, mark.object_key)
        except Exception as exc:  # storage outage must not kill the sweep
            errors += 1
            logger.warning(
                "asset collect failed; mark stays open",
                extra={"mark_id": mark.id, "object_key": mark.object_key},
            )
            await repo.record_collect_error(mark.id, str(exc))
            continue
        await repo.record_collected(mark.id)
        collected += 1
    return CollectTickResult(
        collected_marks=collected, deleted_objects=deleted, errors=errors
    )
