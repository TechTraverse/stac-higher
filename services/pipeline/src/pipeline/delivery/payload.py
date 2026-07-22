"""Payload sidecar builders (ROADMAP §5.1/§6.4, Slice B-ii).

Pure and I/O-free: each returns ``(sidecar_filename, body_bytes)``; the worker
renders the filename through the association's ``path_template`` (so sidecars
land beside the assets) and writes via the adapter. The completion marker is
written LAST (§6.4) — its appearance is the "product is complete" signal for
directory-watching consumers.
"""

from __future__ import annotations

import json
from typing import Any


def item_json_payload(item: dict[str, Any]) -> tuple[str, bytes]:
    """``{item_id}.json`` — the pgstac item verbatim. Asset hrefs stay
    ``/api/assets/...`` (resolvable through the asset service; no href
    rewriting in this slice)."""
    return f"{item['id']}.json", (json.dumps(item, indent=2) + "\n").encode()


def checksum_payload(filename: str, algo: str, digest_hex: str) -> tuple[str, bytes]:
    """``{filename}.{algo}`` in coreutils format — ``sha256sum -c`` /
    ``md5sum -c`` verifiable."""
    return f"{filename}.{algo}", f"{digest_hex}  {filename}\n".encode()


def completion_payload(
    item_id: str, delivered_assets: dict[str, dict[str, Any]]
) -> tuple[str, bytes]:
    """``{item_id}.done`` — JSON manifest of every delivered-or-current asset
    (skipped-but-current assets keep their prior fingerprint entries)."""
    entries = [
        {
            "key": key,
            "filename": delivered_assets[key].get("filename"),
            "fingerprint": delivered_assets[key].get("fingerprint"),
            "size": delivered_assets[key].get("size"),
        }
        for key in sorted(delivered_assets)
    ]
    body = json.dumps({"item_id": item_id, "assets": entries}, indent=2) + "\n"
    return f"{item_id}.done", body.encode()
