"""``run(builtin_id)`` — the body of every built-in extractor process.

The ordinary extractor contract (``docs/processes.md``): read the manifest
named by ``STAC_HIGHER_INPUT_MANIFEST`` from ``STAC_HIGHER_OUTPUT_BUCKET``,
write one ``{item_id}.json`` per draft under ``STAC_HIGHER_OUTPUT_PREFIX``.
Failure is per item — an item with no output document is refused by
finalize with its reason on the ledger row, while the others still land —
and the process exits non-zero only when NOTHING landed, mirroring the
extract branch's own "dead only when nothing landed" rule.

Staged files are downloaded to a per-item temporary directory under their
original basenames (the packages parse product names from filenames), and
the adapter receives ``{asset_key: local_path}`` plus the draft.
"""

from __future__ import annotations

import datetime as dt
import enum
import importlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from stac_higher_stactools.merge import merge_item
from stac_higher_stactools.registry import (
    BuiltinExtractor,
    RegistryError,
    find_entry,
    load_registry,
)

KIND_EXTRACT = "extract"
Adapter = Callable[[Mapping[str, Path], Mapping[str, Any]], Any]


def _log(message: str) -> None:
    print(f"[stactools] {message}", flush=True)


def load_adapter(entry: BuiltinExtractor) -> Adapter:
    module = importlib.import_module(entry.adapter_module)
    create = getattr(module, "create", None)
    if not callable(create):
        raise RegistryError(f"{entry.adapter_module} has no create(paths, draft)")
    return create


def _filename(asset: Mapping[str, Any], fallback: str) -> str:
    key = asset.get("key")
    if isinstance(key, str) and key:
        name = os.path.basename(key.rstrip("/"))
        if name:
            return name
    return fallback


def stage_assets(s3: Any, entry_assets: Mapping[str, Any], directory: Path) -> dict[str, Path]:
    """Download every manifest asset into ``directory``; returns key → path."""
    paths: dict[str, Path] = {}
    for asset_key, asset in entry_assets.items():
        if not isinstance(asset, Mapping) or not asset.get("bucket") or not asset.get("key"):
            continue
        target = directory / _filename(asset, asset_key)
        if target in paths.values():
            raise ValueError(f"two assets stage to the same filename {target.name!r}")
        s3.download_file(asset["bucket"], asset["key"], str(target))
        paths[asset_key] = target
    return paths


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return str(value)


def to_dict(item: Any) -> dict[str, Any]:
    """A pystac Item (or a plain dict from a test double) as a JSON document,
    WITHOUT self links or href rewriting — the merge never keeps hrefs.

    Round-tripped through JSON so what the merge sees is what will be
    written: packages leave enums (goes-glm's orbital slot) and other
    non-JSON values in ``properties`` that ``to_dict`` does not coerce.
    """
    raw = (
        dict(item)
        if isinstance(item, Mapping)
        else item.to_dict(include_self_link=False, transform_hrefs=False)
    )
    return json.loads(json.dumps(raw, default=_jsonable))


def process_entry(
    s3: Any,
    adapter: Adapter,
    manifest_entry: Mapping[str, Any],
    *,
    bucket: str,
    prefix: str,
) -> dict[str, Any]:
    draft = manifest_entry["item"]
    item_id = draft["id"]
    with tempfile.TemporaryDirectory(prefix="stactools-") as tmp:
        directory = Path(tmp)
        paths = stage_assets(s3, manifest_entry.get("assets") or {}, directory)
        if not paths:
            raise ValueError("the draft has no readable assets")
        produced = adapter(paths, draft)
        result = merge_item(draft, to_dict(produced), {k: str(p) for k, p in paths.items()})
    for line in result.dropped:
        _log(f"  {item_id}: dropped produced asset {line}")
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}{item_id}.json",
        Body=json.dumps(result.item).encode("utf-8"),
    )
    return result.item


def run(builtin_id: str, *, s3: Any | None = None, registry_path: Path | None = None) -> int:
    """Execute the built-in extractor ``builtin_id`` against the run's
    manifest. Returns the number of items written; raises ``SystemExit(1)``
    when the manifest had items and none landed."""
    entries = load_registry(registry_path)
    entry = find_entry(builtin_id, entries)
    adapter = load_adapter(entry)

    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
    prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
    manifest = json.loads(
        s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
    )
    kind = manifest.get("kind")
    if kind != KIND_EXTRACT:
        raise SystemExit(
            f"[stactools] {builtin_id} is an extractor; this run's manifest is kind={kind!r}"
        )
    items = manifest.get("items") or []
    _log(
        f"batch {manifest.get('batch_id')}: {len(items)} draft(s) via "
        f"{entry.package}=={entry.version} (adapter {entry.adapter})"
    )

    written = 0
    for manifest_entry in items:
        item_id = (manifest_entry.get("item") or {}).get("id", "?")
        try:
            item = process_entry(s3, adapter, manifest_entry, bucket=bucket, prefix=prefix)
        except Exception as exc:  # the item's failure is its result
            _log(f"  {item_id}: FAILED {type(exc).__name__}: {exc}")
            continue
        written += 1
        props = item.get("properties") or {}
        _log(
            f"  {item_id}: datetime={props.get('datetime')} "
            f"geometry={(item.get('geometry') or {}).get('type')} "
            f"assets={len(item.get('assets') or {})}"
        )
    if items and written == 0:
        _log("no item landed")
        raise SystemExit(1)
    return written


if __name__ == "__main__":
    run(sys.argv[1])
    sys.exit(0)
