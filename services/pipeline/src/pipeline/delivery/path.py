"""Render a delivery association's ``path_template`` for one asset (ROADMAP §6.4).

Pure and I/O-free. Tokens (documented in ``app/.../associations/schemas.ts``):
``{collection} {item_id} {filename} {yyyy} {mm} {dd}``. Date tokens resolve from
the item's ``properties.datetime`` (falling back to ``start_datetime``); if a
template references a date token and the item has neither, rendering raises so
delivery fails loudly rather than writing to a wrong path.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

_TOKEN_RE = re.compile(r"\{(\w+)\}")
_DATE_TOKENS = ("{yyyy}", "{mm}", "{dd}")


class DeliveryPathError(ValueError):
    """The path template could not be rendered for this item."""


def _item_datetime(item: dict[str, Any]) -> dt.datetime:
    props = item.get("properties") or {}
    raw = props.get("datetime") or props.get("start_datetime")
    if not raw:
        raise DeliveryPathError(
            "path template uses a date token but the item has no "
            "datetime/start_datetime"
        )
    text = str(raw).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError as exc:  # malformed datetime string
        raise DeliveryPathError(f"unparseable item datetime: {raw!r}") from exc


def render_path(template: str, item: dict[str, Any], filename: str) -> str:
    """Render ``template`` into a destination-relative path for one asset."""
    tokens: dict[str, str] = {
        "collection": str(item.get("collection", "")),
        "item_id": str(item.get("id", "")),
        "filename": filename,
    }
    if any(tok in template for tok in _DATE_TOKENS):
        when = _item_datetime(item)
        tokens["yyyy"] = f"{when.year:04d}"
        tokens["mm"] = f"{when.month:02d}"
        tokens["dd"] = f"{when.day:02d}"

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in tokens:
            raise DeliveryPathError(f"unknown path-template token: {{{name}}}")
        return tokens[name]

    return _TOKEN_RE.sub(_sub, template)
