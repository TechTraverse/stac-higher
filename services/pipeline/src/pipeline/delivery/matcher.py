"""Pure item→delivery-association matching (ROADMAP §6.4).

The dispatcher (Slice A skeleton) fetches the changed item + candidate
`direction='deliver'` associations, then calls :func:`match_item` to decide which
associations should receive the item and which of its assets. Kept pure (no DB,
no I/O) so it is fully unit-testable; the Pg wiring lives in dispatcher/repo.py.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cql2 import Expr

from pipeline.delivery.config import parse_delivery_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverAssociation:
    """An enabled ``direction='deliver'`` association (config is raw §5.1 jsonb)."""

    id: str
    collection_id: str
    config: dict[str, Any]


@dataclass(frozen=True)
class Match:
    association_id: str
    item_id: str
    asset_keys: tuple[str, ...]


def _item_filter_passes(item_filter: str | None, item: dict[str, Any]) -> bool:
    """Evaluate a CQL2 text filter against a STAC item. Null filter = pass.

    Any evaluation error (a malformed filter string, or a filter referencing a
    property this item doesn't have — cql2 raises rather than returning False
    in that case) is treated as "does not match" so it is skipped in
    isolation, never poisoning the rest of :func:`match_item`'s loop over
    other associations.
    """
    if not item_filter:
        return True
    try:
        return bool(Expr(item_filter).matches(item))
    except Exception:
        logger.warning(
            "item_filter evaluation failed, treating as no-match "
            "(item_filter=%r, item_id=%r)",
            item_filter,
            item.get("id"),
        )
        return False


def match_item(
    item: dict[str, Any], associations: Sequence[DeliverAssociation]
) -> list[Match]:
    """Return one :class:`Match` per association that should receive ``item``.

    An association matches when its ``item_filter`` passes (null = all) AND the
    intersection of its ``asset_keys`` (null = all) with the item's assets is
    non-empty. The asset order follows the item's own asset declaration order.
    """
    item_id = str(item.get("id"))
    item_assets = list((item.get("assets") or {}).keys())
    matches: list[Match] = []
    for assoc in associations:
        cfg = parse_delivery_config(assoc.config)
        if not _item_filter_passes(cfg.item_filter, item):
            continue
        if cfg.asset_keys is None:
            keys = tuple(item_assets)
        else:
            wanted = set(cfg.asset_keys)
            keys = tuple(k for k in item_assets if k in wanted)
        if not keys:
            continue
        matches.append(Match(association_id=assoc.id, item_id=item_id, asset_keys=keys))
    return matches
