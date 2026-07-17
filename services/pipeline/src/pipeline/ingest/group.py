"""GROUP stage: settled files → product groups (ROADMAP §6.1, §5.1 grouping).

Reads the association's ``settled`` ledger rows and forms product groups, one per
STAC item:

* ``rule = "none"`` — one file per product (the common raster case). Each settled
  file is its own group, ready immediately; the item id is the filename stem.
* ``rule = "shared_basename"`` — files sharing a basename stem in the same
  directory form one product (e.g. ``scene.tif`` + ``scene.xml`` sidecar). There
  is no manifest telling us a group is "complete", so a group waits
  ``timeout_seconds`` from when its earliest member settled — giving late
  siblings time to arrive — then is emitted (``on_timeout: ingest_partial``) or
  dropped (``on_timeout: discard`` → members marked ``failed``).

GROUP does not mutate emitted members' status; FETCH flips ``settled → fetching``.
Re-running GROUP before FETCH re-emits the same groups, which FETCH handles
idempotently. ``storage_mode: reference`` is deferred to Slice C, so groups are
not formed for reference associations here.
"""

from __future__ import annotations

import datetime as dt
import logging
import posixpath
from dataclasses import dataclass, field

from pipeline.ingest.config import IngestConfig
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_SETTLED,
    IngestRepo,
    LedgerEntry,
)

logger = logging.getLogger(__name__)


@dataclass
class ReadyGroup:
    """A product ready for FETCH: an item id and its settled source files."""

    item_id: str
    members: list[LedgerEntry]


@dataclass
class GroupResult:
    ready: list[ReadyGroup] = field(default_factory=list)
    waiting: int = 0
    discarded: int = 0
    skipped_reference: bool = False


def _stem(relpath: str) -> str:
    return posixpath.splitext(posixpath.basename(relpath))[0]


def _safe_item_id(name: str) -> str:
    """A filesystem/URL-safe single-segment item id (no separators/traversal)."""
    cleaned = name.strip().replace("/", "_").replace("\\", "_")
    if cleaned in ("", ".", ".."):
        return "item"
    return cleaned


async def group_stage(
    repo: IngestRepo,
    association_id: str,
    config: IngestConfig,
    now: dt.datetime,
) -> GroupResult:
    """Form ready product groups from the association's settled files."""
    if config.storage_mode == "reference":
        logger.info(
            "ingest group: reference mode deferred to Slice C — no groups formed",
            extra={"association_id": association_id},
        )
        return GroupResult(skipped_reference=True)

    settled = await repo.list_ledger_by_status(association_id, STATUS_SETTLED)
    if config.grouping.rule == "shared_basename":
        return await _group_shared_basename(repo, association_id, config, settled, now)
    return _group_none(settled)


def _group_none(settled: list[LedgerEntry]) -> GroupResult:
    result = GroupResult()
    for entry in settled:
        item_id = _safe_item_id(_stem(entry.source_path))
        result.ready.append(ReadyGroup(item_id=item_id, members=[entry]))
    return result


async def _group_shared_basename(
    repo: IngestRepo,
    association_id: str,
    config: IngestConfig,
    settled: list[LedgerEntry],
    now: dt.datetime,
) -> GroupResult:
    buckets: dict[tuple[str, str], list[LedgerEntry]] = {}
    for entry in settled:
        key = (posixpath.dirname(entry.source_path), _stem(entry.source_path))
        buckets.setdefault(key, []).append(entry)

    result = GroupResult()
    timeout = config.grouping.timeout_seconds
    for (_, stem), members in buckets.items():
        earliest = min(
            (m.updated_at for m in members if m.updated_at is not None),
            default=now,
        )
        age = (now - earliest).total_seconds()
        if age < timeout:
            # still within the collection window — let more siblings settle.
            result.waiting += 1
            continue
        if config.grouping.on_timeout == "discard":
            for member in members:
                await repo.set_ledger_fields(member.id, status=STATUS_FAILED)
            result.discarded += 1
            logger.info(
                "ingest group: discarded incomplete group at timeout",
                extra={"association_id": association_id, "stem": stem},
            )
            continue
        result.ready.append(ReadyGroup(item_id=_safe_item_id(stem), members=members))
    return result
