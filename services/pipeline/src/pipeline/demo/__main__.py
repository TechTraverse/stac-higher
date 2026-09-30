"""`python -m pipeline.demo` — seed the demo pipeline on a local stack.

Runs from the HOST against compose's published ports, like `pipeline.loadgen`.
It rebuilds, from nothing, the smallest thing that exercises the whole G queue:

    a scene collection holding a real COG in canonical storage
      -> a process triggered by its items, reading them through the ADR 0018
         input manifest
      -> a thumbnail collection it publishes into
      -> both collections tiling through the derived tile server

    # from services/pipeline, with `docker compose up -d --wait` done
    uv run python -m pipeline.demo seed
    uv run python -m pipeline.demo status
    uv run python -m pipeline.demo teardown

The same CLI carries the GOES worked example (G-7) — the same shapes against
the live NODD bucket, needing the internet and the process runtime image:

    uv run python -m pipeline.demo goes-seed
    uv run python -m pipeline.demo goes-status
    uv run python -m pipeline.demo goes-teardown

`seed` is idempotent: re-running it replaces the scene and leaves one process
deployed, so it is safe after a `docker compose down -v` **or** on a stack that
already has the demo. It writes to pgstac through the STAC API and to
`stac_higher` directly, the same split `pipeline.loadgen` uses.

PRECONDITION: the app must have run its migrations against this database (they
run on its first API request). `seed` checks and says so rather than failing
half-built.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys

import numpy as np
import psycopg
import rasterio
from rasterio.transform import from_bounds

from pipeline.demo.fixtures import (
    CREATED_BY,
    GROUP,
    OUTPUT_COLLECTION,
    PROCESS_CODE,
    PROCESS_ID,
    PROCESS_NAME,
    REVISION_ID,
    RUNTIME,
    SCENE_BBOX,
    SCENE_SIZE,
    SOURCE_COLLECTION,
    TRIGGER,
    collection_document,
    scene_item,
)
from pipeline.demo.goes import seed as goes
from pipeline.demo.images import seed as images
from pipeline.demo.images.seed import SeedError
from pipeline.demo.platform import (
    check_migrations,
    enable_serving,
    install_process,
    put_collection,
    remove_process,
    request,
    s3_client,
    say,
)
from pipeline.storage.keys import canonical_asset_key

DEFAULT_STAC_URL = "http://localhost:8082"
DEFAULT_TITILER_URL = "http://localhost:8084"
DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_DATABASE_URL = "postgresql://username:password@localhost:5433/postgis"
DEFAULT_BUCKET = "stac-higher"
SCENE_ITEM_ID = "demo-scene-001"
SCENE_FILENAME = "scene.tif"

#: The newest migration the demo's process path depends on. It was G-3's
#: coalescing index (025); since G-6 the shared writer stamps `processes.kind`,
#: which 027 adds — and 027 is strictly newer, so the G-3 guarantee still holds.
REQUIRED_MIGRATION = "027_extractors"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


#: Thin aliases: the generic helpers now live in `demo/platform.py`, shared
#: with the GOES seeder. Kept under their old names so this module reads the
#: way it did.
_say = say
_request = request
_s3 = s3_client
_put_collection = put_collection


def _check_migrations(database_url: str) -> None:
    check_migrations(database_url, REQUIRED_MIGRATION)


# --------------------------------------------------------------------------- #
# the scene: a real COG in canonical storage
# --------------------------------------------------------------------------- #


def build_scene_cog(size: int = SCENE_SIZE) -> bytes:
    """A synthetic RGB scene, written as a real COG.

    Not a flat gradient: overlapping wave fields give the overviews something
    to average, so a zoomed-out tile looks like resampled imagery rather than
    a smooth ramp — which is what makes the tiler's work visible.
    """
    y, x = np.mgrid[0:size, 0:size] / size
    swirl = np.sin(8 * np.pi * x) * np.cos(6 * np.pi * y)
    ridges = np.sin(20 * np.pi * (x * 0.6 + y * 0.4))
    base = 0.55 + 0.25 * swirl + 0.12 * ridges

    red = np.clip(base * 0.9 + 0.20 * x, 0, 1)
    green = np.clip(base * 0.8 + 0.25 * (1 - y), 0, 1)
    blue = np.clip(base * 0.6 + 0.30 * y, 0, 1)
    rgb = (np.stack([red, green, blue]) * 255).astype("uint8")

    west, south, east, north = SCENE_BBOX
    profile = {
        "driver": "COG",
        "dtype": "uint8",
        "count": 3,
        "height": size,
        "width": size,
        "crs": "EPSG:4326",
        "transform": from_bounds(west, south, east, north, size, size),
        "compress": "deflate",
        "blocksize": 256,
    }
    buffer = io.BytesIO()
    with rasterio.io.MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(rgb)
        buffer.write(mem.read())
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #


def _put_item(stac_url: str, item: dict) -> None:
    collection_id = item["collection"]
    status, payload = _request(
        f"{stac_url}/collections/{collection_id}/items", method="POST", body=item
    )
    if status == 409:
        status, payload = _request(
            f"{stac_url}/collections/{collection_id}/items/{item['id']}",
            method="PUT",
            body=item,
        )
    if status not in (200, 201):
        raise SystemExit(f"item {item['id']}: {status} {payload[:200]!r}")
    _say(f"  item {collection_id}/{item['id']}")


def _enable_serving(conn: psycopg.Connection, collection_id: str) -> None:
    enable_serving(conn, collection_id, GROUP)


def _install_process(conn: psycopg.Connection) -> None:
    """Create (or re-deploy) the demo process, its trigger and its output."""
    install_process(
        conn,
        process_id=PROCESS_ID,
        revision_id=REVISION_ID,
        name=PROCESS_NAME,
        description=(
            "Demo: downscale each incoming scene into a thumbnail COG, "
            "reading its input through the platform's input manifest."
        ),
        group=GROUP,
        created_by=CREATED_BY,
        kind="transform",
        max_runs_per_hour=120,
        runtime=RUNTIME,
        code=PROCESS_CODE,
        sources=((SOURCE_COLLECTION, TRIGGER),),
        outputs=(OUTPUT_COLLECTION,),
    )


def seed(args: argparse.Namespace) -> int:
    _check_migrations(args.database_url)

    _say("catalog")
    _put_collection(
        args.stac_url,
        collection_document(
            SOURCE_COLLECTION, "Demo source scenes — synthetic RGB COGs in platform storage."
        ),
    )
    _put_collection(
        args.stac_url,
        collection_document(
            OUTPUT_COLLECTION, "Demo thumbnails — published by the demo-downscale process."
        ),
    )

    _say("object storage")
    payload = build_scene_cog()
    key = canonical_asset_key(SOURCE_COLLECTION, SCENE_ITEM_ID, SCENE_FILENAME)
    _s3(args.s3_endpoint).put_object(
        Bucket=args.bucket,
        Key=key,
        Body=payload,
        ContentType="image/tiff; application=geotiff; profile=cloud-optimized",
    )
    _say(f"  s3://{args.bucket}/{key} ({len(payload):,} bytes)")

    _say("platform")
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        _enable_serving(conn, SOURCE_COLLECTION)
        _enable_serving(conn, OUTPUT_COLLECTION)
        _say("  OGC serving enabled on both collections")
        _install_process(conn)
        _say(f"  process {PROCESS_NAME} deployed ({SOURCE_COLLECTION} -> {OUTPUT_COLLECTION})")

    if args.trigger:
        _say("catalog (trigger)")
        _put_item(
            args.stac_url,
            scene_item(
                SCENE_ITEM_ID,
                dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                SCENE_FILENAME,
            ),
        )
        _say("")
        _say("The item write wakes the dispatcher, which runs the process within")
        _say("seconds. Watch it with:  uv run python -m pipeline.demo status")
    else:
        _say("")
        _say("Seeded without the scene item (--no-trigger), so no run was started.")

    _say("")
    _say(f"UI:    http://localhost:4321/collections/{SOURCE_COLLECTION}/items/{SCENE_ITEM_ID}")
    _say(f"Tiles: {args.titiler_url}/collections/{SOURCE_COLLECTION}/items/{SCENE_ITEM_ID}"
         "/WebMercatorQuad/tilejson.json?assets=visual")
    return 0


# --------------------------------------------------------------------------- #
# status / teardown
# --------------------------------------------------------------------------- #


def status(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url) as conn:
        runs = conn.execute(
            "SELECT status, jsonb_array_length(input_items),"
            "       to_char(created_at, 'HH24:MI:SS'),"
            "       round(extract(epoch from (started_at - created_at)) * 1000),"
            "       coalesce(error, '')"
            "  FROM stac_higher.process_runs WHERE process_id = %s"
            " ORDER BY created_at DESC LIMIT 10",
            (PROCESS_ID,),
        ).fetchall()

    if not runs:
        _say("no runs yet")
    else:
        _say("recent runs (newest first)")
        _say(f"  {'status':<10} {'items':>5} {'created':>10} {'claim ms':>9}  error")
        for state, items, created, lag, error in runs:
            _say(f"  {state:<10} {items:>5} {created:>10} {lag or '-'!s:>9}  {error[:60]}")

    for collection_id in (SOURCE_COLLECTION, OUTPUT_COLLECTION):
        code, body = _request(f"{args.stac_url}/collections/{collection_id}/items?limit=100")
        count = len(json.loads(body).get("features", [])) if code == 200 else -1
        _say(f"{collection_id}: {count} item(s)")
    return 0


def teardown(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        remove_process(conn, PROCESS_ID)
        conn.execute(
            "DELETE FROM stac_higher.collection_settings WHERE collection_id = ANY(%s)",
            ([SOURCE_COLLECTION, OUTPUT_COLLECTION],),
        )
    _say("platform rows removed")

    for collection_id in (OUTPUT_COLLECTION, SOURCE_COLLECTION):
        code, _ = _request(f"{args.stac_url}/collections/{collection_id}", method="DELETE")
        _say(f"  collection {collection_id}: {code}")

    client = _s3(args.s3_endpoint)
    removed = 0
    for collection_id in (SOURCE_COLLECTION, OUTPUT_COLLECTION):
        pages = client.get_paginator("list_objects_v2").paginate(
            Bucket=args.bucket, Prefix=f"assets/{collection_id}/"
        )
        for page in pages:
            for obj in page.get("Contents", []):
                client.delete_object(Bucket=args.bucket, Key=obj["Key"])
                removed += 1
    _say(f"  {removed} object(s) deleted")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.demo", description=__doc__)
    parser.add_argument("--stac-url", default=DEFAULT_STAC_URL)
    parser.add_argument("--titiler-url", default=DEFAULT_TITILER_URL)
    parser.add_argument("--s3-endpoint", default=DEFAULT_S3_ENDPOINT)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    sub = parser.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser("seed", help="build the demo pipeline (idempotent)")
    seed_parser.add_argument(
        "--no-trigger",
        dest="trigger",
        action="store_false",
        help="seed everything except the scene item, so no process run starts",
    )
    seed_parser.set_defaults(func=seed, trigger=True)

    sub.add_parser("status", help="recent runs + item counts").set_defaults(func=status)
    sub.add_parser("teardown", help="remove everything seed created").set_defaults(func=teardown)

    goes_parser = sub.add_parser(
        "goes-seed", help="build the GOES loop against the live NODD bucket (idempotent)"
    )
    goes_parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="restrict the ingest to matching keys (repeatable); default: the whole window",
    )
    goes_parser.add_argument(
        "--window", default="-1h", help="ingest window begin (default: -1h)"
    )
    goes_parser.add_argument(
        "--max-files", type=int, default=2, help="max new files per poll (default: 2)"
    )
    goes_parser.add_argument(
        "--deliver",
        action="store_true",
        help="also deliver the output collection to MinIO (needs CREDENTIALS_MASTER_KEY)",
    )
    goes_parser.add_argument(
        "--internal-s3-endpoint",
        default="http://minio:9000",
        help="how the PIPELINE reaches MinIO (default: http://minio:9000)",
    )
    goes_parser.set_defaults(func=goes.seed)

    sub.add_parser("goes-status", help="GOES loop: runs, ingest ledger, item counts").set_defaults(
        func=goes.status
    )
    goes_teardown_parser = sub.add_parser(
        "goes-teardown", help="remove everything goes-seed created"
    )
    goes_teardown_parser.add_argument(
        "--force",
        action="store_true",
        help="delete the collections even when another ingest association targets them",
    )
    goes_teardown_parser.set_defaults(func=goes.teardown)

    images_seed_parser = sub.add_parser(
        "images-seed",
        help="add demo container images through the app API and deploy their"
        " kind-2 GOES twin + canary (C-5)",
    )
    images_seed_parser.add_argument("--app-url", default="http://127.0.0.1:4321")
    images_seed_parser.add_argument(
        "--bearer", default=os.environ.get("STAC_HIGHER_BEARER"),
        help="bearer token for the app API (default: $STAC_HIGHER_BEARER)",
    )
    images_seed_parser.add_argument(
        "--with-large", action="store_true", help="also add python:3.12 (a larger image)"
    )
    images_seed_parser.add_argument(
        "--with-kev", action="store_true",
        help="also add vulnerables/cve-2014-6271 (a known-exploited-vulnerability image)",
    )
    images_seed_parser.add_argument(
        "--exception-days", type=_exception_days, default=None,
        help="grant the runtime image an exception this many days long (1-90) if it fails",
    )
    images_seed_parser.add_argument(
        "--no-wait", action="store_true", help="do not poll for scan results before returning"
    )
    images_seed_parser.add_argument(
        "--scan-timeout", type=int, default=1800, help="seconds to poll for scans (default: 1800)"
    )
    images_seed_parser.set_defaults(func=images.seed)

    images_status_parser = sub.add_parser(
        "images-status", help="the images live-gate scenario: image states, runs, alerts"
    )
    images_status_parser.add_argument("--app-url", default="http://127.0.0.1:4321")
    images_status_parser.add_argument("--bearer", default=os.environ.get("STAC_HIGHER_BEARER"))
    images_status_parser.set_defaults(func=images.status)

    images_teardown_parser = sub.add_parser(
        "images-teardown", help="remove everything images-seed created"
    )
    images_teardown_parser.add_argument(
        "--images", action="store_true",
        help="also delete the demo images' registry rows (skips any still in use)",
    )
    images_teardown_parser.add_argument(
        "--force", action="store_true", help="skip the in-progress-run check"
    )
    images_teardown_parser.set_defaults(func=images.teardown)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SeedError as err:
        say(str(err))
        return 1


def _exception_days(value: str) -> int:
    days = int(value)
    if not 1 <= days <= 90:
        raise argparse.ArgumentTypeError("--exception-days must be between 1 and 90")
    return days


if __name__ == "__main__":
    sys.exit(main())
