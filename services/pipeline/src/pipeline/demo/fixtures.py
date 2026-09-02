"""The documents and code the demo pipeline is built from.

Kept apart from the CLI so the shapes are readable on their own — and so the
process code below can be read as what it is: an ordinary process, written
against the same contract `docs/processes.md` gives every operator.
"""

from __future__ import annotations

from typing import Any

#: Everything the demo creates is named with this prefix, so `teardown` can
#: find it and nothing collides with real work on a shared stack.
PREFIX = "demo"
SOURCE_COLLECTION = f"{PREFIX}-scenes"
OUTPUT_COLLECTION = f"{PREFIX}-thumbnails"
PROCESS_ID = "de000000-0000-4000-8000-000000000001"
REVISION_ID = "de000000-0000-4000-8000-000000000002"
PROCESS_NAME = f"{PREFIX}-downscale"
#: The dev-bypass identity's group, so the demo is visible in the UI without
#: logging in (`docs/auth.md`).
GROUP = "earth-observation"
CREATED_BY = "demo-seed"

#: The scene the demo publishes. Small enough to seed in seconds, big enough
#: that the tiler builds real overviews.
SCENE_SIZE = 1024
#: [west, south, east, north] — a recognisable footprint over the US plains.
SCENE_BBOX = (-104.0, 37.0, -94.0, 43.0)


def _polygon(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    w, s, e, n = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def collection_document(collection_id: str, description: str) -> dict[str, Any]:
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "title": collection_id.replace("-", " ").title(),
        "description": description,
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [list(SCENE_BBOX)]},
            "temporal": {"interval": [["2026-01-01T00:00:00Z", None]]},
        },
        "links": [],
    }


def scene_item(item_id: str, datetime_iso: str, filename: str) -> dict[str, Any]:
    """A source item whose asset href is the canonical `/api/assets/...` form.

    That href is the point of the demo's serving half: the app resolves it for
    browsers, and the derived tile server (G-4) maps it to the object behind it
    so the same item also renders as tiles.
    """
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": SOURCE_COLLECTION,
        "geometry": _polygon(SCENE_BBOX),
        "bbox": list(SCENE_BBOX),
        "properties": {"datetime": datetime_iso},
        "links": [],
        "assets": {
            "visual": {
                "href": f"/api/assets/{SOURCE_COLLECTION}/{item_id}/{filename}",
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["visual", "data"],
                "title": "Synthetic RGB scene",
            }
        },
    }


#: The deployed revision. `network.level` is `isolated`: the run reaches
#: platform storage and nothing else, which is all it needs because the
#: pipeline stages its inputs for it (ADR 0018).
RUNTIME: dict[str, Any] = {
    "kind": "inline_python",
    "image": None,
    "memory_mb": 1024,
    "timeout_seconds": 300,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}

TRIGGER: dict[str, Any] = {"kind": "item_event", "item_filter": None}


#: The process itself — a miniature of the GOES use case: read a raster input
#: the platform staged, write a derived COG, publish it as a STAC item. It is
#: deliberately ordinary code; nothing here is privileged.
PROCESS_CODE = '''\
"""Downscale each input scene to a browsable thumbnail COG.

The platform hands this run its inputs (ADR 0018): a manifest naming every
triggering item and, for each asset, a bucket and key these credentials can
read. Outputs go to STAC_HIGHER_OUTPUT_PREFIX as an item document plus the
files it names.
"""

import json
import os

import boto3
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile

FACTOR = 4

s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)

print(f"batch {manifest['batch_id']}: {len(manifest['items'])} item(s)")

for entry in manifest["items"]:
    item = entry["item"]
    asset = entry["assets"].get("visual") or next(iter(entry["assets"].values()))
    print(f"  {item['id']} <- {asset['key']} (staged={asset['staged']})")

    raw = s3.get_object(Bucket=asset["bucket"], Key=asset["key"])["Body"].read()
    with MemoryFile(raw) as mem, mem.open() as src:
        height, width = src.height // FACTOR, src.width // FACTOR
        data = src.read(out_shape=(src.count, height, width), resampling=Resampling.average)
        profile = src.profile.copy()
        transform = src.transform * src.transform.scale(
            src.width / width, src.height / height
        )
        profile.update(
            driver="COG", height=height, width=width, transform=transform,
            compress="deflate", blocksize=256,
        )

    out_name = "thumbnail.tif"
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(data.astype(np.uint8))
        payload = mem.read()
    s3.put_object(Bucket=bucket, Key=f"{prefix}{out_name}", Body=payload)

    out_id = f"{item['id']}-thumb"
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}{out_id}.json",
        Body=json.dumps(
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": out_id,
                "collection": "OUTPUT_COLLECTION_ID",
                "geometry": item["geometry"],
                "bbox": item["bbox"],
                "properties": {
                    "datetime": item["properties"]["datetime"],
                    "demo:derived_from": item["id"],
                    "demo:downscale_factor": FACTOR,
                },
                "links": [],
                "assets": {
                    "visual": {
                        "href": out_name,
                        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                        "roles": ["visual", "data"],
                    }
                },
            }
        ).encode(),
    )
    print(f"  -> published {out_id} ({width}x{height}, {len(payload)} bytes)")
'''.replace("OUTPUT_COLLECTION_ID", OUTPUT_COLLECTION)
