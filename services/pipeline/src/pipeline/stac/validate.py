"""The single STAC item validation gate (Phase 7 spec §6.2).

Lifted verbatim from ``pipeline/ingest/itemize.py`` so ITEMIZE (polled ingest)
and finalize (push ingest, Phase 9 process runs) pass the IDENTICAL gate —
anything else would make one path's gate stricter than the other's, which is
indefensible (§6.2). Offline by construction: stac-pydantic validates core
structure with no schema fetches, fitting the pipeline's no-egress posture.
Extension-schema validation ("stac-validator on demand") is deferred for both
paths (ISSUES, §6.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ItemValidationError(Exception):
    """The built item fails stac-pydantic validation."""


def validate_item(item_dict: Mapping[str, Any]) -> None:
    """stac-pydantic gate (offline, core-structural). Raises on invalid.

    Uses the core ``stac_pydantic.Item`` (not ``stac_pydantic.api.Item``): the
    API variant additionally requires a ``root`` link, which pipeline-built and
    pushed catalog items never carry (they are plain catalog items, not API
    page entries).
    """
    from pydantic import ValidationError
    from stac_pydantic import Item

    try:
        Item.model_validate(dict(item_dict))
    except ValidationError as exc:
        raise ItemValidationError(str(exc)) from exc
