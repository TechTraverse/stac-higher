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
import sys
import urllib.error
import urllib.request

import boto3
import numpy as np
import psycopg
import rasterio
from botocore.client import Config
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
from pipeline.storage.keys import canonical_asset_key

DEFAULT_STAC_URL = "http://localhost:8082"
DEFAULT_TITILER_URL = "http://localhost:8084"
DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_DATABASE_URL = "postgresql://username:password@localhost:5433/postgis"
DEFAULT_BUCKET = "stac-higher"
SCENE_ITEM_ID = "demo-scene-001"
SCENE_FILENAME = "scene.tif"

#: The one migration the demo's process path depends on (G-3 coalescing).
REQUIRED_MIGRATION = "025_process_runs_queued_source_idx"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _say(message: str) -> None:
    print(message, flush=True)


def _request(url: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except urllib.error.URLError as err:
        raise SystemExit(f"cannot reach {url}: {err.reason}. Is the stack up?") from err


def _s3(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def _check_migrations(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        try:
            row = conn.execute(
                "SELECT count(*) FROM stac_higher.migrations WHERE name = %s",
                (REQUIRED_MIGRATION,),
            ).fetchone()
        except psycopg.errors.UndefinedTable as err:
            raise SystemExit(
                "the stac_higher schema does not exist yet. The APP owns those\n"
                "migrations and runs them on its first API request (ADR 0001):\n"
                "  cd app && npm run dev     # then load any page, or\n"
                "  curl -s localhost:4321/api/auth/me >/dev/null\n"
                "then re-run this command."
            ) from err
    if not row or row[0] == 0:
        raise SystemExit(
            f"migration {REQUIRED_MIGRATION} is not applied — this database predates\n"
            "G-3. Start the app once against it so it migrates, then re-run."
        )


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


def _put_collection(stac_url: str, document: dict) -> None:
    collection_id = document["id"]
    status, payload = _request(f"{stac_url}/collections", method="POST", body=document)
    if status == 409:
        status, payload = _request(
            f"{stac_url}/collections/{collection_id}", method="PUT", body=document
        )
    if status not in (200, 201):
        raise SystemExit(f"collection {collection_id}: {status} {payload[:200]!r}")
    _say(f"  collection {collection_id}")


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
    conn.execute(
        "INSERT INTO stac_higher.collection_settings (collection_id, group_id, serving_enabled)"
        " VALUES (%s, %s, true)"
        " ON CONFLICT (collection_id) DO UPDATE SET serving_enabled = true",
        (collection_id, GROUP),
    )


def _install_process(conn: psycopg.Connection) -> None:
    """Create (or re-deploy) the demo process, its trigger and its output.

    Written directly to `stac_higher` — the same split `pipeline.loadgen` uses:
    pgstac through the API, platform tables through SQL. The app owns the DDL
    either way (ADR 0001); nothing here creates or alters a table.
    """
    conn.execute(
        "INSERT INTO stac_higher.processes"
        " (id, name, description, group_id, enabled, max_runs_per_hour, created_by)"
        " VALUES (%s, %s, %s, %s, true, 120, %s)"
        " ON CONFLICT (id) DO UPDATE SET"
        "   name = EXCLUDED.name, description = EXCLUDED.description,"
        "   enabled = true, deleted_at = NULL",
        (
            PROCESS_ID,
            PROCESS_NAME,
            "Demo: downscale each incoming scene into a thumbnail COG, "
            "reading its input through the platform's input manifest.",
            GROUP,
            CREATED_BY,
        ),
    )
    # A revision is immutable, so re-seeding replaces its row wholesale rather
    # than stacking a new one every run — this is a demo fixture, not history.
    conn.execute(
        "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
        (PROCESS_ID,),
    )
    conn.execute(
        "DELETE FROM stac_higher.process_runs WHERE process_id = %s", (PROCESS_ID,)
    )
    conn.execute(
        "DELETE FROM stac_higher.process_revisions WHERE process_id = %s", (PROCESS_ID,)
    )
    conn.execute(
        "INSERT INTO stac_higher.process_revisions"
        " (id, process_id, runtime, code, env, created_by)"
        " VALUES (%s, %s, %s::jsonb, %s, '[]'::jsonb, %s)",
        (REVISION_ID, PROCESS_ID, json.dumps(RUNTIME), PROCESS_CODE, CREATED_BY),
    )
    conn.execute(
        "UPDATE stac_higher.processes SET current_revision = %s WHERE id = %s",
        (REVISION_ID, PROCESS_ID),
    )
    conn.execute(
        "INSERT INTO stac_higher.process_sources"
        " (process_id, collection_id, trigger, flow_stats, enabled)"
        " SELECT %s, %s, %s::jsonb, '{}'::jsonb, true"
        "  WHERE NOT EXISTS ("
        "    SELECT 1 FROM stac_higher.process_sources"
        "     WHERE process_id = %s AND collection_id = %s)",
        (PROCESS_ID, SOURCE_COLLECTION, json.dumps(TRIGGER), PROCESS_ID, SOURCE_COLLECTION),
    )
    conn.execute(
        "INSERT INTO stac_higher.process_outputs (process_id, collection_id)"
        " SELECT %s, %s"
        "  WHERE NOT EXISTS ("
        "    SELECT 1 FROM stac_higher.process_outputs"
        "     WHERE process_id = %s AND collection_id = %s)",
        (PROCESS_ID, OUTPUT_COLLECTION, PROCESS_ID, OUTPUT_COLLECTION),
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
        conn.execute(
            "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
            (PROCESS_ID,),
        )
        for table in (
            "process_runs",
            "process_sources",
            "process_outputs",
            "process_checks",
            "process_revisions",
        ):
            conn.execute(
                f"DELETE FROM stac_higher.{table} WHERE process_id = %s", (PROCESS_ID,)
            )
        conn.execute("DELETE FROM stac_higher.processes WHERE id = %s", (PROCESS_ID,))
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
