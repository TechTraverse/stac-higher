"""`python -m pipeline.demo goes-seed` — the GOES loop on a local stack,
against the LIVE NODD bucket (the manual recipe for spec §10's "full hour").

    goes-abi-mcmipc   <- anonymous s3 connection to noaa-goes19, reference
                          mode, the last hour of ABI-L2-MCMIPC, at most two
                          new files per poll, EXTRACTED by goes-abi-metadata
    goes-geocolor     <- the goes-geocolor process, triggered per source item
                          (optionally delivered to a MinIO bucket, --deliver)

Needs the internet, the process runtime image, and — for --deliver —
CREDENTIALS_MASTER_KEY in the environment (the delivery connection's MinIO
credentials are sealed with it, exactly as pipeline.loadgen does).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

import psycopg

from pipeline.connections.envelope import load_master_key, seal
from pipeline.demo.goes import extractor_code, geocolor_code
from pipeline.demo.platform import (
    check_migrations,
    enable_serving,
    ensure_bucket,
    install_process,
    put_collection,
    remove_process,
    request,
    s3_client,
    say,
    upsert_association,
    upsert_connection,
)

GROUP = "earth-observation"
CREATED_BY = "goes-seed"
SOURCE_COLLECTION = "goes-abi-mcmipc"
OUTPUT_COLLECTION = "goes-geocolor"
NODD_BUCKET = "noaa-goes19"
PRODUCT_PREFIX = "ABI-L2-MCMIPC/"
CONNECTION_NAME = "goes-nodd"
DEST_CONNECTION_NAME = "goes-geocolor-dest"
DEST_BUCKET = "stac-higher-deliveries"
EXTRACTOR_NAME = "goes-abi-metadata"
GEOCOLOR_NAME = "goes-geocolor"
EXTRACTOR_ID = "60e50000-0000-4000-8000-000000000001"
EXTRACTOR_REVISION_ID = "60e50000-0000-4000-8000-000000000002"
GEOCOLOR_ID = "60e50000-0000-4000-8000-000000000003"
GEOCOLOR_REVISION_ID = "60e50000-0000-4000-8000-000000000004"

#: The migration that gave a process a `kind` — an ingest association cannot
#: name an extractor before it (G-6).
REQUIRED_MIGRATION = "027_extractors"

EXTRACTOR_RUNTIME = {
    "kind": "inline_python",
    "image": None,
    "memory_mb": 1024,
    "timeout_seconds": 120,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}
GEOCOLOR_RUNTIME = {
    "kind": "inline_python",
    "image": None,
    "memory_mb": 2048,
    "timeout_seconds": 600,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}
TRIGGER = {"kind": "item_event", "item_filter": None}


# --------------------------------------------------------------------------- #
# the shapes the seeder writes
# --------------------------------------------------------------------------- #


def nodd_connection_config() -> dict:
    return {"bucket": NODD_BUCKET, "region": "us-east-1", "anonymous": True}


def ingest_config(
    *,
    include=(),
    window_begin: str = "-1h",
    max_files_per_poll: int = 2,
    extractor_id: str = EXTRACTOR_ID,
) -> dict:
    return {
        "source_path": PRODUCT_PREFIX,
        "include": list(include),
        "exclude": [],
        "poll_frequency_seconds": 60,
        "storage_mode": "reference",
        "path_template": "{Y}/{j}/{H}/",
        "window": {"begin": window_begin, "end": None},
        "max_files_per_poll": max_files_per_poll,
        "grouping": {"rule": "none", "timeout_seconds": 900, "on_timeout": "ingest_partial"},
        "metadata": {"strategy": "extractor", "extractor": {"process_id": extractor_id}},
        "post_ingest": "leave",
    }


def deliver_config() -> dict:
    return {
        "path_template": "goes/{item_id}/{filename}",
        "item_filter": None,
        "asset_keys": None,
        "payload": {"item_json": False, "checksums": None, "completion_marker": False},
        "on_update": "redeliver",
        "overwrite": "if_newer",
        "retry": {"max_attempts": 5, "backoff": "exponential"},
        "max_concurrent_transfers": 4,
    }


def collection_documents() -> list[dict]:
    def doc(cid: str, description: str) -> dict:
        return {
            "type": "Collection",
            "stac_version": "1.0.0",
            "id": cid,
            "description": description,
            "license": "proprietary",
            "extent": {
                "spatial": {"bbox": [[-152.0, 14.0, -52.0, 57.0]]},
                "temporal": {"interval": [["2025-04-07T00:00:00Z", None]]},
            },
            "links": [],
        }

    return [
        doc(
            SOURCE_COLLECTION,
            "GOES-19 ABI L2 CONUS multi-band CMI, catalogued in place from NODD.",
        ),
        doc(
            OUTPUT_COLLECTION,
            "GeoColor-style true-colour COGs derived by the goes-geocolor process.",
        ),
    ]


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #


def competing_ingest_associations(
    conn: psycopg.Connection,
    collection_ids: Sequence[str],
    *,
    own_connection_name: str = CONNECTION_NAME,
    include_disabled: bool = False,
) -> list[tuple[str, str, str]]:
    """Ingest associations on `collection_ids` that belong to somebody else.

    Returns `(association id, collection id, connection name)` per row. Both
    the seeder and the teardown ask this question — of the same rows, with a
    different appetite: seeding only cares about an ENABLED competitor (two
    live pollers ingest every file twice), while tearing a collection down
    matters to a DISABLED association too, since deleting the collection
    would leave it pointing at nothing.
    """
    enabled_clause = "" if include_disabled else " AND cc.enabled"
    rows = conn.execute(
        "SELECT cc.id, cc.collection_id, c.name"
        "  FROM stac_higher.collection_connections cc"
        "  JOIN stac_higher.connections c ON c.id = cc.connection_id"
        " WHERE cc.collection_id = ANY(%s) AND cc.direction = 'ingest'"
        "   AND cc.deleted_at IS NULL AND c.deleted_at IS NULL"
        f"   AND c.name <> %s{enabled_clause}",
        (list(collection_ids), own_connection_name),
    ).fetchall()
    return [(str(assoc), str(collection), str(name)) for assoc, collection, name in rows]


def _describe(rows: Sequence[tuple[str, str, str]]) -> str:
    return "\n".join(
        f"  association {assoc_id}  collection {collection_id}  connection {name}"
        for assoc_id, collection_id, name in rows
    )


def _refuse_competing_ingest(conn: psycopg.Connection) -> None:
    """A SECOND enabled ingest association on either GOES collection would poll
    the same product into the same collection, so every file arrives twice.

    Checked BEFORE anything is written — including before the collection
    documents are PUT: a refusal that had already replaced two collections
    and deployed two processes would be worse than useless.
    """
    rows = competing_ingest_associations(conn, (SOURCE_COLLECTION, OUTPUT_COLLECTION))
    if not rows:
        return
    raise SystemExit(
        "another ENABLED ingest association already targets a GOES collection:\n"
        f"{_describe(rows)}\n"
        "Disable it first, or it will ingest the same files twice."
    )


def seed(args: argparse.Namespace) -> int:
    check_migrations(args.database_url, REQUIRED_MIGRATION)

    _require_master_key(args)

    # The connection opens FIRST so the competing-association refusal happens
    # before the catalog is touched, not after two collections were replaced.
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        _refuse_competing_ingest(conn)

        say("catalog")
        for document in collection_documents():
            put_collection(args.stac_url, document)

        say("platform")
        enable_serving(conn, SOURCE_COLLECTION, GROUP)
        enable_serving(conn, OUTPUT_COLLECTION, GROUP)
        say("  OGC serving enabled on both collections")

        install_process(
            conn,
            process_id=EXTRACTOR_ID,
            revision_id=EXTRACTOR_REVISION_ID,
            name=EXTRACTOR_NAME,
            description=(
                "GOES worked example: the EXTRACTOR that dates each ABI granule "
                "from its filename and footprints it from the CMI_C02 grid."
            ),
            group=GROUP,
            created_by=CREATED_BY,
            kind="extractor",
            max_runs_per_hour=600,
            runtime=EXTRACTOR_RUNTIME,
            code=extractor_code(),
        )
        say(f"  process {EXTRACTOR_NAME} deployed (extractor)")

        install_process(
            conn,
            process_id=GEOCOLOR_ID,
            revision_id=GEOCOLOR_REVISION_ID,
            name=GEOCOLOR_NAME,
            description=(
                "GOES worked example: a GeoColor-style true-colour COG per "
                "ABI MCMIPC granule, day/night blended."
            ),
            group=GROUP,
            created_by=CREATED_BY,
            kind="transform",
            max_runs_per_hour=120,
            runtime=GEOCOLOR_RUNTIME,
            code=geocolor_code(OUTPUT_COLLECTION),
            sources=((SOURCE_COLLECTION, TRIGGER),),
            outputs=(OUTPUT_COLLECTION,),
        )
        say(f"  process {GEOCOLOR_NAME} deployed ({SOURCE_COLLECTION} -> {OUTPUT_COLLECTION})")

        source_connection = upsert_connection(
            conn,
            name=CONNECTION_NAME,
            protocol="s3",
            config=nodd_connection_config(),
            credentials=None,
            group=GROUP,
            created_by=CREATED_BY,
        )
        association = upsert_association(
            conn,
            collection_id=SOURCE_COLLECTION,
            connection_id=source_connection,
            direction="ingest",
            config=ingest_config(
                include=args.include,
                window_begin=args.window,
                max_files_per_poll=args.max_files,
            ),
            created_by=CREATED_BY,
        )
        say(
            f"  ingest association {association}"
            f" (s3://{NODD_BUCKET}/{PRODUCT_PREFIX}, window {args.window},"
            f" <= {args.max_files} file(s)/poll)"
        )

        if args.deliver:
            client = s3_client(args.s3_endpoint)
            ensure_bucket(client, DEST_BUCKET)
            credentials = seal(
                json.dumps(
                    {"access_key_id": "minioadmin", "secret_access_key": "minioadmin"}
                ),
                load_master_key(dict(os.environ)),
            )
            dest_connection = upsert_connection(
                conn,
                name=DEST_CONNECTION_NAME,
                protocol="s3",
                config={
                    "bucket": DEST_BUCKET,
                    "region": "us-east-1",
                    "endpoint": args.internal_s3_endpoint,
                    "force_path_style": True,
                },
                credentials=credentials,
                group=GROUP,
                created_by=CREATED_BY,
            )
            delivery = upsert_association(
                conn,
                collection_id=OUTPUT_COLLECTION,
                connection_id=dest_connection,
                direction="deliver",
                config=deliver_config(),
                created_by=CREATED_BY,
            )
            say(f"  deliver association {delivery} (-> s3://{DEST_BUCKET}/goes/)")

    say("")
    say("The ingest poll runs on the pipeline's minute tick. Watch it with:")
    say("  uv run python -m pipeline.demo goes-status")
    say("")
    say(f"UI:    http://localhost:4321/collections/{SOURCE_COLLECTION}/items")
    say(f"       http://localhost:4321/collections/{OUTPUT_COLLECTION}/items")
    say("       http://localhost:4321/processes")
    say(f"Tiles: {args.titiler_url}/collections/{OUTPUT_COLLECTION}"
        "/WebMercatorQuad/tilejson.json?assets=visual")
    return 0


def _require_master_key(args: argparse.Namespace) -> None:
    """Fail on a missing master key BEFORE the catalog is touched, rather than
    at the last write of a seed that otherwise succeeded."""
    if getattr(args, "deliver", False):
        load_master_key(dict(os.environ))


# --------------------------------------------------------------------------- #
# status / teardown
# --------------------------------------------------------------------------- #


def status(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url) as conn:
        runs = conn.execute(
            "SELECT p.name, r.status, jsonb_array_length(r.input_items),"
            "       to_char(r.created_at, 'HH24:MI:SS'),"
            "       round(extract(epoch from (r.started_at - r.created_at)) * 1000),"
            "       coalesce(r.error, '')"
            "  FROM stac_higher.process_runs r"
            "  JOIN stac_higher.processes p ON p.id = r.process_id"
            " WHERE r.process_id = ANY(%s)"
            " ORDER BY r.created_at DESC LIMIT 15",
            ([EXTRACTOR_ID, GEOCOLOR_ID],),
        ).fetchall()
        ledger = conn.execute(
            "SELECT f.status, count(*)"
            "  FROM stac_higher.ingest_files f"
            "  JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
            "  JOIN stac_higher.connections c ON c.id = cc.connection_id"
            " WHERE cc.collection_id = %s AND cc.direction = 'ingest' AND c.name = %s"
            " GROUP BY f.status ORDER BY f.status",
            (SOURCE_COLLECTION, CONNECTION_NAME),
        ).fetchall()

    if not runs:
        say("no runs yet")
    else:
        say("recent runs (newest first)")
        say(f"  {'process':<18} {'status':<10} {'items':>5} {'created':>10} {'claim ms':>9}  error")
        for name, state, items, created, lag, error in runs:
            say(
                f"  {name:<18} {state:<10} {items:>5} {created:>10}"
                f" {lag or '-'!s:>9}  {error[:60]}"
            )

    if not ledger:
        say("ingest ledger: empty (no file discovered yet)")
    else:
        say("ingest ledger: " + ", ".join(f"{state}={count}" for state, count in ledger))

    for collection_id in (SOURCE_COLLECTION, OUTPUT_COLLECTION):
        code, body = request(f"{args.stac_url}/collections/{collection_id}/items?limit=100")
        count = len(json.loads(body).get("features", [])) if code == 200 else -1
        say(f"{collection_id}: {count} item(s)")
    return 0


def teardown(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        # Both collections are fixed, well-known names. Somebody else's ingest
        # association may be sitting on one (the W-1 seed association is, on
        # the lead's stack), and deleting the collection out from under it
        # would leave it pointing at nothing — so refuse before the first
        # DELETE. Disabled associations count too: they are still pointed here.
        others = competing_ingest_associations(
            conn, (SOURCE_COLLECTION, OUTPUT_COLLECTION), include_disabled=True
        )
        if others and not getattr(args, "force", False):
            say(_describe(others))
            raise SystemExit(
                "goes-teardown: other ingest associations target these collections;"
                " delete them first or pass --force"
            )

        # Order is forced by the FKs migration 010 hardened to RESTRICT: the
        # ledger/history rows FK the association, the association FKs the
        # connection, and a `connection_checks` row (a UI "Test connection")
        # holds the connection too. Alerts CASCADE, so they need no line here.
        for table in ("ingest_files", "delivery_log", "delivery_backfills"):
            conn.execute(
                f"DELETE FROM stac_higher.{table} t"
                " USING stac_higher.collection_connections cc, stac_higher.connections c"
                " WHERE t.association_id = cc.id AND cc.connection_id = c.id"
                "   AND c.name = ANY(%s)",
                ([CONNECTION_NAME, DEST_CONNECTION_NAME],),
            )
        conn.execute(
            "DELETE FROM stac_higher.connection_checks ch"
            " USING stac_higher.connections c"
            " WHERE ch.connection_id = c.id AND c.name = ANY(%s)",
            ([CONNECTION_NAME, DEST_CONNECTION_NAME],),
        )
        conn.execute(
            "DELETE FROM stac_higher.collection_connections cc"
            " USING stac_higher.connections c"
            " WHERE cc.connection_id = c.id AND c.name = ANY(%s)",
            ([CONNECTION_NAME, DEST_CONNECTION_NAME],),
        )
        conn.execute(
            "DELETE FROM stac_higher.connections WHERE name = ANY(%s)",
            ([CONNECTION_NAME, DEST_CONNECTION_NAME],),
        )
        remove_process(conn, GEOCOLOR_ID)
        remove_process(conn, EXTRACTOR_ID)
        conn.execute(
            "DELETE FROM stac_higher.collection_settings WHERE collection_id = ANY(%s)",
            ([SOURCE_COLLECTION, OUTPUT_COLLECTION],),
        )
    say("platform rows removed")

    for collection_id in (OUTPUT_COLLECTION, SOURCE_COLLECTION):
        code, _ = request(f"{args.stac_url}/collections/{collection_id}", method="DELETE")
        say(f"  collection {collection_id}: {code}")

    client = s3_client(args.s3_endpoint)
    removed = 0
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=args.bucket, Prefix=f"assets/{OUTPUT_COLLECTION}/"
    )
    for page in pages:
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=args.bucket, Key=obj["Key"])
            removed += 1
    say(f"  {removed} object(s) deleted")
    return 0
