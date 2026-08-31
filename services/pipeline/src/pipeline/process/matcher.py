"""Pure item→process-source matching (spec §6).

The delivery matcher's twin, and deliberately so: an ``item_event`` trigger is
the same question as a delivery association's — "does this landing item
concern this row?" — evaluated on the same CQL2 path. Kept pure (no DB, no
I/O) so it is fully unit-testable; the Pg wiring lives in process/repo.py.

Two differences from delivery matching, both from §6:

- **Cron sources never match here.** They are the scheduler's business; a cron
  source appearing in this list is a caller bug, and silently matching it
  would run a scheduled process on every landing item.
- **Matches batch per SOURCE, not per item.** One run = N trigger items
  (§6.7), so the dispatcher groups matches into one queued run per source per
  tick rather than one run per item.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cql2 import Expr

from pipeline.process.config import ProcessConfigError, parse_process_trigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessSource:
    """An enabled ``process_sources`` row (trigger is raw §5.6 jsonb)."""

    id: str
    process_id: str
    collection_id: str
    trigger: dict[str, Any]


@dataclass(frozen=True)
class ProcessMatch:
    source_id: str
    process_id: str
    item_id: str


def _item_filter_passes(item_filter: str | None, item: dict[str, Any]) -> bool:
    """Evaluate a CQL2 text filter against a STAC item. Null filter = pass.

    Any evaluation error (a malformed filter, or one referencing a property
    this item lacks — cql2 raises rather than returning False) is treated as
    "does not match", so a bad filter skips its own source in isolation
    instead of poisoning the loop over the others. Failing CLOSED is the right
    direction here: a filter that cannot be evaluated must not be read as
    "match everything", which would run a process over items its author
    deliberately excluded.
    """
    if not item_filter:
        return True
    try:
        return bool(Expr(item_filter).matches(item))
    except Exception:
        logger.warning(
            "process item_filter evaluation failed, treating as no-match",
            extra={"item_filter": item_filter, "item_id": item.get("id")},
        )
        return False


def match_process_sources(
    item: dict[str, Any], sources: Sequence[ProcessSource]
) -> list[ProcessMatch]:
    """Return one :class:`ProcessMatch` per source this item should trigger."""
    item_id = str(item.get("id"))
    matches: list[ProcessMatch] = []
    for source in sources:
        # Per-source isolation (I-39): a bad stored trigger must skip THIS
        # source, never abort the loop — an uncaught raise here would poison
        # the whole dispatch batch and stall the outbox.
        try:
            trigger = parse_process_trigger(source.trigger)
        except ProcessConfigError:
            logger.warning(
                "process source has an unusable trigger, skipping",
                extra={"source_id": source.id, "process_id": source.process_id},
            )
            continue
        if trigger.kind != "item_event":
            continue
        if not _item_filter_passes(trigger.item_filter, item):
            continue
        matches.append(
            ProcessMatch(
                source_id=source.id, process_id=source.process_id, item_id=item_id
            )
        )
    return matches
