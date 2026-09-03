"""Plan what a run receives (GOES spec §3, ADR 0018): the manifest, the remote
fetches, and the read grants — computed purely so it can be tested without
storage.

Two kinds of asset location, decided per asset:

- a canonical ``/api/assets/{c}/{i}/{f}`` href → the object already sits at
  ``assets/{c}/{i}/{f}`` in the platform bucket. No copy; the run's session
  policy is granted read on each SOURCE collection's prefix (§3.2) — UNLESS
  the ingest ledger says the item is reference-mode, in which case the bytes
  never entered the bucket and the asset is fetched from its ``source_href``
  like any other remote asset.
- any other absolute href → fetched by the launcher into the run's inputs
  area (``RemoteFetch``); the manifest points at the staged copy.

Anything else (a relative href, no href) is not a location the run can use
and is omitted from ``assets`` — the item itself is still delivered, because
its metadata may be the point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote

from pipeline.storage.keys import (
    CANONICAL_PREFIX,
    canonical_asset_key,
    run_input_asset_key,
    run_input_manifest_key,
    run_inputs_prefix,
)

MANIFEST_VERSION = 1
KIND_TRANSFORM = "transform"
KIND_EXTRACT = "extract"
OP_UNKNOWN = "unknown"
SKIP_NOT_FOUND = "not_found"


class InputPlanError(Exception):
    """The run's inputs cannot be described — a contract problem, not I/O."""


@dataclass(frozen=True)
class InputAsset:
    bucket: str
    key: str
    staged: bool
    href: str


@dataclass(frozen=True)
class InputItem:
    collection: str
    op: str  # "insert" | "update" | "unknown"
    item: dict[str, Any]
    assets: dict[str, InputAsset]


@dataclass(frozen=True)
class RemoteFetch:
    href: str
    key: str
    item_id: str
    asset_key: str


@dataclass(frozen=True)
class InputPlan:
    manifest: dict[str, Any]  # exactly what is written to manifest.json
    manifest_key: str
    fetches: tuple[RemoteFetch, ...]
    read_prefixes: tuple[str, ...]


def parse_canonical_href(href: object, base: str = "/api/assets") -> tuple[str, str, str] | None:
    """``/api/assets/{c}/{i}/{f}`` → (c, i, f), URL-decoded; else None."""
    if not isinstance(href, str):
        return None
    prefix = base.rstrip("/") + "/"
    if not href.startswith(prefix):
        return None
    parts = href[len(prefix) :].split("?", 1)[0].split("/")
    if len(parts) != 3:
        return None
    segs = [unquote(p) for p in parts]
    if any(not s or s in (".", "..") or "/" in s or "\\" in s for s in segs):
        return None
    return segs[0], segs[1], segs[2]


def _is_absolute(href: object) -> bool:
    return isinstance(href, str) and "://" in href


def _filename_from_href(href: str) -> str:
    tail = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return unquote(tail) or "asset"


def input_env(run_id: str, manifest_key: str) -> dict[str, str]:
    """The two variables that tell the run where its inputs are (§3.1)."""
    return {
        "STAC_HIGHER_INPUT_PREFIX": run_inputs_prefix(run_id),
        "STAC_HIGHER_INPUT_MANIFEST": manifest_key,
    }


def plan_inputs(
    *,
    run_id: str,
    process_id: str,
    batch_id: str,
    kind: str,
    refs: Sequence[Mapping[str, Any]],
    documents: Mapping[tuple[str, str], dict[str, Any]],
    source_collections: Sequence[str],
    bucket: str,
    asset_href_base: str,
    source_hrefs: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
) -> InputPlan:
    """Turn the run's ``input_items`` refs plus their pgstac documents into a
    manifest, the remote fetches the launcher must perform, and the read
    grants the session policy needs.

    ``refs`` are ``{"item_id", "collection_id"?, "op"?}``; a legacy ref with
    no collection (a run row written before G-2) falls back to the process's
    single source collection, and is an error when that is ambiguous. A ref
    with a ``draft`` (extractor runs, GOES spec §6) is its own document. A ref
    whose document is missing (deleted between trigger and run) is skipped and
    recorded — not an error.

    ``source_hrefs`` maps (collection, item_id) to the ingest ledger's
    ``filename -> source_href`` for a reference-mode item, whose canonical
    href names bytes the platform never stored.
    """
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    fetches: list[RemoteFetch] = []

    for ref in refs:
        item_id = str(ref.get("item_id") or "")
        if not item_id:
            raise InputPlanError("input ref has no item_id")
        collection = ref.get("collection_id")
        if not collection:
            if len(source_collections) != 1:
                raise InputPlanError(
                    f"input ref {item_id!r} names no collection and the process has "
                    f"{len(source_collections)} source collections"
                )
            collection = source_collections[0]
        collection = str(collection)
        op = str(ref.get("op") or OP_UNKNOWN)

        # G-6: an extractor ref CARRIES its document — the draft ITEMIZE built,
        # which is not in pgstac yet. A transform ref names a catalogued item.
        document = ref.get("draft") if isinstance(ref.get("draft"), dict) else None
        if document is None:
            document = documents.get((collection, item_id))
        if document is None:
            skipped.append({"item_id": item_id, "collection": collection, "reason": SKIP_NOT_FOUND})
            continue

        assets: dict[str, dict[str, Any]] = {}
        for asset_key, entry in (document.get("assets") or {}).items():
            href = entry.get("href") if isinstance(entry, dict) else None
            canonical = parse_canonical_href(href, asset_href_base)
            if canonical is not None:
                c, i, f = canonical
                source = (source_hrefs or {}).get((collection, item_id), {}).get(f)
                if source:
                    # A reference-mode asset: the catalog href is canonical but
                    # the bytes live at the source (ingest_files.source_href).
                    # Stage from there; keep the CATALOG href for provenance.
                    key = run_input_asset_key(run_id, batch_id, item_id, f)
                    fetches.append(
                        RemoteFetch(href=source, key=key, item_id=item_id, asset_key=asset_key)
                    )
                    assets[asset_key] = asdict(
                        InputAsset(bucket=bucket, key=key, staged=True, href=href)
                    )
                    continue
                # parse_canonical_href already refused traversal/separators,
                # so InvalidKeySegment here would be a bug, not an input.
                assets[asset_key] = asdict(
                    InputAsset(
                        bucket=bucket, key=canonical_asset_key(c, i, f), staged=False, href=href
                    )
                )
                continue
            if _is_absolute(href):
                key = run_input_asset_key(run_id, batch_id, item_id, _filename_from_href(href))
                fetches.append(
                    RemoteFetch(href=href, key=key, item_id=item_id, asset_key=asset_key)
                )
                assets[asset_key] = asdict(
                    InputAsset(bucket=bucket, key=key, staged=True, href=href)
                )
            # relative / missing href: not a location the run can use — omitted.

        items.append({"collection": collection, "op": op, "item": document, "assets": assets})

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "run_id": run_id,
        "process_id": process_id,
        "batch_id": batch_id,
        "kind": kind,
        "items": items,
    }
    if skipped:
        manifest["skipped"] = skipped

    read_prefixes = tuple(sorted(f"{CANONICAL_PREFIX}/{c}/" for c in set(source_collections)))
    return InputPlan(
        manifest=manifest,
        manifest_key=run_input_manifest_key(run_id, batch_id),
        fetches=tuple(fetches),
        read_prefixes=read_prefixes,
    )
