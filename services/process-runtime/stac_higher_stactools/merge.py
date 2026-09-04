"""Merge a package-built pystac item onto the platform's draft (spec §6, §6.1).

stactools items mint their own ids, point hrefs at whatever path they were
given, and routinely ADD assets (COG conversions, thumbnails, sidecars). The
platform's extract contract (``check_extract_output`` in the pipeline's
``finalize/extract_run.py``) refuses all of that: the draft's id, collection,
asset SET and every asset href are immutable; geometry and ``datetime`` must
end up non-null. So the wrapper merges rather than replaces:

- keep the draft's ``id``, ``collection``, ``stac_version``, ``links``, asset
  KEYS and every asset ``href``;
- copy ``properties`` (the package's overlaid on the draft's — a platform
  field the package does not emit survives), ``geometry``, ``bbox`` and
  ``stac_extensions``;
- for each draft asset, copy the asset-level metadata the package produced
  for the SAME FILE (``type``, ``roles``, ``title``, ``raster:bands``,
  ``proj:*``, everything but ``href``) — "assets may gain metadata, not
  members". A produced asset is matched to a draft asset by the staged
  file it points at (full path, else basename — packages like noaa-hrrr
  emit the archive's URL rather than the local path);
- DROP every produced asset that matches no draft asset, and report it;
  hrefs are never copied, so a staged local path can never leak into the
  catalog.

Two conventions the packages do not share with the platform are settled
here rather than per adapter: a null ``datetime`` with ``start_datetime``
set (noaa-cdr, modis) becomes ``datetime = start_datetime``; a package that
leaves geometry null keeps the draft's (which may also be null — that is a
per-item failure the caller reports, not something to invent).
"""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Item-level keys that always come from the draft.
_DRAFT_OWNED = ("id", "collection", "stac_version", "links", "type")


class MergeError(ValueError):
    """The produced item cannot be merged into something the platform accepts."""


@dataclass(frozen=True)
class MergeResult:
    item: dict[str, Any]
    #: One line per produced asset that was not carried over, for the run log.
    dropped: tuple[str, ...] = field(default=())


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _match_key(href: Any, by_path: Mapping[str, str], by_name: Mapping[str, str]) -> str | None:
    if not isinstance(href, str) or not href:
        return None
    local = href[len("file://") :] if href.startswith("file://") else href
    key = by_path.get(_normalize(local))
    if key is not None:
        return key
    name = os.path.basename(local.split("?", 1)[0].rstrip("/"))
    return by_name.get(name)


def merge_item(
    draft: Mapping[str, Any],
    produced: Mapping[str, Any],
    staged: Mapping[str, str],
) -> MergeResult:
    """``draft`` is the manifest's item, ``produced`` the package item as a
    dict, ``staged`` maps each draft asset key to the local path it was
    staged at (the path the adapter handed the package)."""
    draft_assets = draft.get("assets")
    if not isinstance(draft_assets, dict):
        raise MergeError("the draft has no assets object")
    if not isinstance(produced, Mapping):
        raise MergeError("the package did not return an item")

    merged: dict[str, Any] = copy.deepcopy(dict(draft))

    # Properties: the package's overlaid on the draft's.
    draft_props = draft.get("properties")
    produced_props = produced.get("properties")
    props: dict[str, Any] = dict(draft_props) if isinstance(draft_props, dict) else {}
    if isinstance(produced_props, Mapping):
        props.update(produced_props)
    if props.get("datetime") is None and isinstance(props.get("start_datetime"), str):
        props["datetime"] = props["start_datetime"]
    merged["properties"] = props

    if produced.get("geometry") is not None:
        merged["geometry"] = copy.deepcopy(produced["geometry"])
        merged["bbox"] = copy.deepcopy(produced.get("bbox"))
    elif produced.get("bbox") is not None and merged.get("bbox") is None:
        merged["bbox"] = copy.deepcopy(produced["bbox"])

    extensions: list[str] = []
    for source in (draft.get("stac_extensions"), produced.get("stac_extensions")):
        if isinstance(source, list):
            for ext in source:
                if isinstance(ext, str) and ext not in extensions:
                    extensions.append(ext)
    merged["stac_extensions"] = extensions

    by_path = {_normalize(path): key for key, path in staged.items()}
    by_name = {os.path.basename(path): key for key, path in staged.items()}
    produced_assets = produced.get("assets")
    matched: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []
    if isinstance(produced_assets, Mapping):
        for produced_key, produced_asset in produced_assets.items():
            if not isinstance(produced_asset, Mapping):
                continue
            draft_key = _match_key(produced_asset.get("href"), by_path, by_name)
            if draft_key is None or draft_key in matched:
                reason = (
                    "no draft asset for that file"
                    if draft_key is None
                    else (f"draft asset {draft_key!r} already matched")
                )
                dropped.append(f"{produced_key} ({produced_asset.get('href')!r}): {reason}")
                continue
            matched[draft_key] = {k: v for k, v in produced_asset.items() if k != "href"}

    assets: dict[str, Any] = {}
    for key, draft_asset in draft_assets.items():
        asset = dict(draft_asset) if isinstance(draft_asset, Mapping) else {}
        href = asset.get("href")
        asset.update(copy.deepcopy(matched.get(key, {})))
        asset["href"] = href
        assets[key] = asset
    merged["assets"] = assets

    for key in _DRAFT_OWNED:
        if key in draft:
            merged[key] = copy.deepcopy(draft[key])
        else:
            merged.pop(key, None)
    return MergeResult(item=merged, dropped=tuple(dropped))
