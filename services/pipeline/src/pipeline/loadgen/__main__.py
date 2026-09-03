"""`python -m pipeline.loadgen` — the M3-S-A load harness CLI.

Runs from the HOST against the compose stack's published ports; nothing here
executes inside the pipeline container, so a load run never perturbs the
process being measured beyond the work it offers it.

    # from services/pipeline, with the stack up
    uv run python -m pipeline.loadgen setup    --label run1 --mode copy
    uv run python -m pipeline.loadgen feed     --label run1 --rate 30 --count 1800
    uv run python -m pipeline.loadgen watch    --seconds 120
    uv run python -m pipeline.loadgen teardown --label run1

Every subcommand is idempotent enough to re-run: `setup` reuses what exists,
`teardown` tolerates what is already gone. That matters because the harness is
also the M3 gate's rehearsal driver, and a gate you cannot re-run is a gate you
only ever pass once.

The `--label` namespaces one load run's rows and objects, so several runs can
be compared without a `down -v` between them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import boto3
import psycopg
from botocore.client import Config

from pipeline.connections.envelope import load_master_key, seal
from pipeline.loadgen.feed import emission_offsets, granule, raster_granule
from pipeline.loadgen.fixtures import (
    EXTRACTOR_CODE,
    EXTRACTOR_NAME,
    EXTRACTOR_PROCESS_ID,
    EXTRACTOR_REVISION_ID,
    EXTRACTOR_RUNTIME,
    LOAD_CREATED_BY,
    LOAD_GROUP,
    METADATA_STRATEGIES,
    collection_document,
    deliver_config,
    ingest_config,
    metadata_config,
    s3_connection_config,
)
from pipeline.loadgen.report import render
from pipeline.loadgen.sample import take

#: Host-side defaults for the compose stack (docker-compose.yml).
DEFAULT_DATABASE_URL = "postgresql://username:password@localhost:5433/postgis"
DEFAULT_S3_ENDPOINT = "http://localhost:9000"
#: What the PIPELINE must use to reach the same MinIO — it is on the compose
#: network, where `localhost` is its own container. Getting these two mixed up
#: is the harness's single most likely misconfiguration, so they are separate
#: flags with separate defaults rather than one shared "endpoint".
DEFAULT_INTERNAL_S3_ENDPOINT = "http://minio:9000"
DEFAULT_METRICS_URL = "http://localhost:8083/metrics"
DEFAULT_SOURCE_BUCKET = "m3-load-source"
DEFAULT_DEST_BUCKET = "m3-load-dest"


def s3(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("LOADGEN_S3_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("LOADGEN_S3_SECRET_ACCESS_KEY", "minioadmin"),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, max_pool_connections=64),
    )


def ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)


def names(label: str) -> dict[str, str]:
    return {
        "collection": f"m3-load-{label}",
        "connection": f"m3-load-src-{label}",
        "dest_connection": f"m3-load-dest-{label}",
        "prefix": f"load/{label}/",
    }


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #


def cmd_setup(args: argparse.Namespace) -> int:
    n = names(args.label)
    key = load_master_key(dict(os.environ))
    client = s3(args.s3_endpoint)
    ensure_bucket(client, args.source_bucket)
    if args.deliver:
        ensure_bucket(client, args.dest_bucket)

    credentials = seal(
        json.dumps(
            {
                "access_key_id": os.environ.get("LOADGEN_S3_ACCESS_KEY_ID", "minioadmin"),
                "secret_access_key": os.environ.get(
                    "LOADGEN_S3_SECRET_ACCESS_KEY", "minioadmin"
                ),
            }
        ),
        key,
    )

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pgstac.create_collection(%s::jsonb)"
                if not args.reuse_collection
                else "SELECT pgstac.upsert_collection(%s::jsonb)",
                (json.dumps(collection_document(n["collection"])),),
            ) if not _collection_exists(cur, n["collection"]) else None

            source_id = _upsert_connection(
                cur,
                name=n["connection"],
                config=s3_connection_config(args.source_bucket, args.internal_s3_endpoint),
                credentials=credentials,
            )
            if args.metadata == "extractor":
                _install_extractor(cur)
            ingest_id = _upsert_association(
                cur,
                collection_id=n["collection"],
                connection_id=source_id,
                direction="ingest",
                config=ingest_config(
                    n["prefix"],
                    storage_mode=args.mode,
                    metadata=metadata_config(args.metadata),
                ),
            )
            deliver_id = None
            if args.deliver:
                dest_id = _upsert_connection(
                    cur,
                    name=n["dest_connection"],
                    config=s3_connection_config(args.dest_bucket, args.internal_s3_endpoint),
                    credentials=credentials,
                )
                deliver_id = _upsert_association(
                    cur,
                    collection_id=n["collection"],
                    connection_id=dest_id,
                    direction="deliver",
                    config=deliver_config(
                        f"m3-load/{args.label}/{{item_id}}/{{filename}}"
                    ),
                )
        conn.commit()

    print(
        json.dumps(
            {
                "label": args.label,
                "collection": n["collection"],
                "source_bucket": args.source_bucket,
                "source_prefix": n["prefix"],
                "storage_mode": args.mode,
                "metadata_strategy": args.metadata,
                "ingest_association": str(ingest_id),
                "deliver_association": str(deliver_id) if deliver_id else None,
            },
            indent=2,
        )
    )
    return 0


def _collection_exists(cur, collection_id: str) -> bool:
    cur.execute("SELECT 1 FROM pgstac.collections WHERE id = %s", (collection_id,))
    return cur.fetchone() is not None


def _upsert_connection(cur, *, name: str, config: dict, credentials: bytes) -> uuid.UUID:
    cur.execute(
        "SELECT id FROM stac_higher.connections WHERE name = %s AND deleted_at IS NULL",
        (name,),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE stac_higher.connections"
            "   SET config = %s::jsonb, credentials = %s, enabled = true"
            " WHERE id = %s",
            (json.dumps(config), credentials, row[0]),
        )
        return row[0]
    cur.execute(
        "INSERT INTO stac_higher.connections"
        " (name, description, protocol, config, credentials, group_id, created_by, enabled)"
        " VALUES (%s, %s, 's3', %s::jsonb, %s, %s, %s, true) RETURNING id",
        (name, "M3 load harness", json.dumps(config), credentials, LOAD_GROUP, LOAD_CREATED_BY),
    )
    return cur.fetchone()[0]


def _upsert_association(
    cur, *, collection_id: str, connection_id: uuid.UUID, direction: str, config: dict
) -> uuid.UUID:
    cur.execute(
        "SELECT id FROM stac_higher.collection_connections"
        " WHERE collection_id = %s AND connection_id = %s AND direction = %s"
        "   AND deleted_at IS NULL",
        (collection_id, connection_id, direction),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE stac_higher.collection_connections"
            "   SET config = %s::jsonb, enabled = true WHERE id = %s",
            (json.dumps(config), row[0]),
        )
        return row[0]
    cur.execute(
        "INSERT INTO stac_higher.collection_connections"
        " (collection_id, connection_id, direction, config, created_by, enabled)"
        " VALUES (%s, %s, %s, %s::jsonb, %s, true) RETURNING id",
        (collection_id, connection_id, direction, json.dumps(config), LOAD_CREATED_BY),
    )
    return cur.fetchone()[0]


def _install_extractor(cur) -> None:
    """The pass-through extractor the `extractor` profile names (G-6). Same
    direct-SQL split as the associations: the app owns the DDL, the harness
    writes rows."""
    cur.execute(
        "INSERT INTO stac_higher.processes"
        " (id, name, description, group_id, kind, enabled, max_runs_per_hour, created_by)"
        " VALUES (%s, %s, 'M3 load harness pass-through extractor', %s, 'extractor', true, 600, %s)"
        " ON CONFLICT (id) DO UPDATE SET enabled = true, deleted_at = NULL",
        (EXTRACTOR_PROCESS_ID, EXTRACTOR_NAME, LOAD_GROUP, LOAD_CREATED_BY),
    )
    cur.execute(
        "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
        (EXTRACTOR_PROCESS_ID,),
    )
    cur.execute(
        "DELETE FROM stac_higher.process_runs WHERE process_id = %s",
        (EXTRACTOR_PROCESS_ID,),
    )
    cur.execute(
        "DELETE FROM stac_higher.process_revisions WHERE process_id = %s",
        (EXTRACTOR_PROCESS_ID,),
    )
    cur.execute(
        "INSERT INTO stac_higher.process_revisions"
        " (id, process_id, runtime, code, env, created_by)"
        " VALUES (%s, %s, %s::jsonb, %s, '[]'::jsonb, %s)",
        (
            EXTRACTOR_REVISION_ID,
            EXTRACTOR_PROCESS_ID,
            json.dumps(EXTRACTOR_RUNTIME),
            EXTRACTOR_CODE,
            LOAD_CREATED_BY,
        ),
    )
    cur.execute(
        "UPDATE stac_higher.processes SET current_revision = %s WHERE id = %s",
        (EXTRACTOR_REVISION_ID, EXTRACTOR_PROCESS_ID),
    )


# --------------------------------------------------------------------------- #
# feed / seed
# --------------------------------------------------------------------------- #


def cmd_feed(args: argparse.Namespace) -> int:
    n = names(args.label)
    client = s3(args.s3_endpoint)
    offsets = emission_offsets(args.count, args.rate)
    make = raster_granule if args.profile == "raster" else granule
    started = time.monotonic()
    late = 0
    written_bytes = 0
    for index, offset in enumerate(offsets):
        due = started + offset
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        elif offset > 0 and wait < -1.0:
            # The GENERATOR fell behind, which means the report's "offered
            # rate" is not what was asked for. Counted and printed rather
            # than silently absorbed.
            late += 1
        g = make(index, asset_bytes=args.asset_bytes, prefix=n["prefix"])
        client.put_object(Bucket=args.source_bucket, Key=g.key, Body=g.body)
        written_bytes += len(g.body)
    elapsed = time.monotonic() - started
    print(
        json.dumps(
            {
                "wrote": args.count,
                "seconds": round(elapsed, 2),
                "achieved_rate": round(args.count / elapsed, 2) if elapsed else None,
                "requested_rate": args.rate,
                "generator_late_writes": late,
                "profile": args.profile,
                # Written, not requested: a GeoTIFF is not exactly the size
                # asked for, and the byte column must not claim otherwise.
                "bytes": written_bytes,
            },
            indent=2,
        )
    )
    return 0


def cmd_seed_outbox(args: argparse.Namespace) -> int:
    """Insert `item_events` directly — dispatcher/delivery without ingest.

    The point is isolation: ingest and dispatch share workers, so a number
    measured with both running cannot say which one saturated.
    """
    n = names(args.label)
    rows = [(n["collection"], f"seed-{args.label}-{i:09d}", "insert") for i in range(args.count)]
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stac_higher.item_events (collection_id, item_id, op)"
                " VALUES (%s, %s, %s)",
                rows,
            )
        conn.commit()
    print(json.dumps({"seeded_events": args.count}, indent=2))
    return 0


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #


def cmd_watch(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url) as conn:
        conn.autocommit = True
        first = take(args.metrics_url, conn)
        samples = [first]
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))
            samples.append(take(args.metrics_url, conn))
    print(render(samples, interval=args.interval))
    return 0


# --------------------------------------------------------------------------- #
# teardown
# --------------------------------------------------------------------------- #


def cmd_teardown(args: argparse.Namespace) -> int:
    n = names(args.label)
    client = s3(args.s3_endpoint)
    removed = 0
    for bucket, prefix in (
        (args.source_bucket, n["prefix"]),
        (args.dest_bucket, f"m3-load/{args.label}/"),
    ):
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                    removed += len(keys)
        except Exception as err:  # a bucket that was never created is fine
            print(f"note: {bucket} not cleaned ({err})", file=sys.stderr)

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            # Ledger rows FK the association, so they go first. This is the
            # harness's own data only — every row is keyed to the label.
            cur.execute(
                "DELETE FROM stac_higher.ingest_files WHERE association_id IN ("
                "  SELECT id FROM stac_higher.collection_connections WHERE collection_id = %s)",
                (n["collection"],),
            )
            cur.execute(
                "DELETE FROM stac_higher.delivery_log WHERE association_id IN ("
                "  SELECT id FROM stac_higher.collection_connections WHERE collection_id = %s)",
                (n["collection"],),
            )
            cur.execute(
                "DELETE FROM stac_higher.alerts WHERE association_id IN ("
                "  SELECT id FROM stac_higher.collection_connections WHERE collection_id = %s)",
                (n["collection"],),
            )
            cur.execute(
                "DELETE FROM stac_higher.collection_connections WHERE collection_id = %s",
                (n["collection"],),
            )
            # G-6: the extractor process, if this label's setup installed one.
            # Null current_revision first — the FK the revision delete would
            # otherwise trip — then runs, then revisions, then the process.
            cur.execute(
                "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
                (EXTRACTOR_PROCESS_ID,),
            )
            cur.execute(
                "DELETE FROM stac_higher.process_runs WHERE process_id = %s",
                (EXTRACTOR_PROCESS_ID,),
            )
            cur.execute(
                "DELETE FROM stac_higher.process_revisions WHERE process_id = %s",
                (EXTRACTOR_PROCESS_ID,),
            )
            cur.execute(
                "DELETE FROM stac_higher.processes WHERE id = %s", (EXTRACTOR_PROCESS_ID,)
            )
            cur.execute(
                "DELETE FROM stac_higher.connections WHERE name = ANY(%s)",
                ([n["connection"], n["dest_connection"]],),
            )
            cur.execute(
                "DELETE FROM stac_higher.item_events WHERE collection_id = %s",
                (n["collection"],),
            )
            if not args.keep_items:
                cur.execute("SELECT pgstac.delete_collection(%s)", (n["collection"],))
        conn.commit()
    print(json.dumps({"objects_removed": removed, "label": args.label}, indent=2))
    return 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline.loadgen", description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("LOADGEN_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument("--s3-endpoint", default=DEFAULT_S3_ENDPOINT, help="host-visible MinIO")
    parser.add_argument("--source-bucket", default=DEFAULT_SOURCE_BUCKET)
    parser.add_argument("--dest-bucket", default=DEFAULT_DEST_BUCKET)
    parser.add_argument("--label", default="run1")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="create the collection, connection(s) and association(s)")
    setup.add_argument("--mode", default="copy", choices=("copy", "reference"))
    setup.add_argument(
        "--metadata",
        default="defaults_only",
        choices=METADATA_STRATEGIES,
        help=(
            "defaults_only = plumbing ceiling (no GDAL); raster_auto = real EXTRACT cost; "
            "extractor = the G-6 extractor path (pass-through extractor process, "
            "one run per batch)"
        ),
    )
    setup.add_argument("--deliver", action="store_true", help="also attach a delivery fan-out")
    setup.add_argument(
        "--internal-s3-endpoint",
        default=DEFAULT_INTERNAL_S3_ENDPOINT,
        help="how the PIPELINE reaches MinIO (compose network), not the host",
    )
    setup.add_argument("--reuse-collection", action="store_true")
    setup.set_defaults(func=cmd_setup)

    feed = sub.add_parser("feed", help="write synthetic granules at a target rate")
    feed.add_argument("--rate", type=float, default=30.0, help="files/s; 0 = as fast as possible")
    feed.add_argument("--count", type=int, default=600)
    feed.add_argument("--asset-bytes", type=int, default=64 * 1024)
    feed.add_argument(
        "--profile",
        default="opaque",
        choices=("opaque", "raster"),
        help=(
            "must match the association's metadata strategy (see `setup --metadata`); "
            "extractor pairs with opaque"
        ),
    )
    feed.set_defaults(func=cmd_feed)

    seed = sub.add_parser("seed-outbox", help="insert item_events directly (no ingest)")
    seed.add_argument("--count", type=int, default=1000)
    seed.set_defaults(func=cmd_seed_outbox)

    watch = sub.add_parser("watch", help="sample /metrics + ledger counts and report rates")
    watch.add_argument("--seconds", type=float, default=120.0)
    watch.add_argument("--interval", type=float, default=5.0)
    watch.add_argument("--metrics-url", default=DEFAULT_METRICS_URL)
    watch.set_defaults(func=cmd_watch)

    down = sub.add_parser("teardown", help="remove this label's rows and objects")
    down.add_argument("--keep-items", action="store_true", help="leave the pgstac collection")
    down.set_defaults(func=cmd_teardown)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
