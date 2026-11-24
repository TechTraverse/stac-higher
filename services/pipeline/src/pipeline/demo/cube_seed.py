"""`python -m pipeline.demo cube-seed` — the Z-9 live gate's source, as a recipe.

    goes19-cmipc-c13   <- the anonymous NODD connection (the GOES demo's
                          `goes-nodd` when present), reference mode over
                          ABI-L2-CMIPC/{Y}/{j}/{H}/, `M6C13` only, the last six
                          hours, ONE new file per poll, dated and footprinted
                          by the band-agnostic demo extractor (issue #122)
    goes19-c13-cube    <- an EMPTY collection, ready for the cube sink

The sink is NOT created here: the live gate makes it through the UI card
(open /collections/goes19-c13-cube -> Settings -> Virtual cube).

Why an extractor: reference mode reads no bytes, so the built-in raster path
finds no datetime and every item fails ITEMIZE. The demo extractor dates the
item from the filename's `_sYYYYDDDHHMMSSt` token and footprints it from the
`CMI` grid (`CMI_C02` on a multi-band MCMIPC file).

Why ONE file per poll: see `README.md`, "Cube source (`cube-seed`)".

The extractor process installed here is the cube's OWN (separate ids, same
code as the GOES demo's): `install_process` replaces a process's revision and
deletes its run history, which must not happen to the standing GOES demo.
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from pipeline.connections.envelope import load_master_key, seal
from pipeline.demo.goes import extractor_code
from pipeline.demo.goes import seed as goes
from pipeline.demo.platform import (
    check_migrations,
    install_process,
    put_collection,
    remove_process,
    request,
    say,
    upsert_association,
    upsert_connection,
)

GROUP = goes.GROUP
CREATED_BY = "cube-seed"
SOURCE_COLLECTION = "goes19-cmipc-c13"
CUBE_COLLECTION = "goes19-c13-cube"
PRODUCT_PREFIX = "ABI-L2-CMIPC/"
#: Globs match the SOURCE-RELATIVE path (`2026/276/17/OR_ABI-...`), and a lone
#: `*` does not cross `/`: a bare `M6C13` or `*M6C13*` matches nothing.
INCLUDE = ("**/*M6C13*",)
WINDOW_BEGIN = "-6h"
#: One new file per poll, oldest first: the cube is append-only in time order
#: and a step older than the tip is skipped `late`; see the README.
MAX_FILES_PER_POLL = 1
POLL_FREQUENCY_SECONDS = 60
EXTRACTOR_NAME = "goes-abi-metadata-cmipc"
EXTRACTOR_ID = "60e50000-0000-4000-8000-000000000011"
EXTRACTOR_REVISION_ID = "60e50000-0000-4000-8000-000000000012"
REQUIRED_MIGRATION = goes.REQUIRED_MIGRATION
NEXT_STEP = "open /collections/goes19-c13-cube -> Settings -> Virtual cube"


def ingest_config() -> dict:
    return {
        "source_path": PRODUCT_PREFIX,
        "include": list(INCLUDE),
        "exclude": [],
        "poll_frequency_seconds": POLL_FREQUENCY_SECONDS,
        "storage_mode": "reference",
        "path_template": "{Y}/{j}/{H}/",
        "window": {"begin": WINDOW_BEGIN, "end": None},
        "max_files_per_poll": MAX_FILES_PER_POLL,
        "grouping": {"rule": "none", "timeout_seconds": 900, "on_timeout": "ingest_partial"},
        "metadata": {"strategy": "extractor", "extractor": {"process_id": EXTRACTOR_ID}},
        "post_ingest": "leave",
    }


def collection_documents() -> list[dict]:
    def doc(cid: str, title: str, description: str, start: str) -> dict:
        return {
            "type": "Collection",
            "stac_version": "1.0.0",
            "id": cid,
            "title": title,
            "description": description,
            "license": "proprietary",
            "extent": {
                "spatial": {"bbox": [[-152.0, 14.0, -52.0, 57.0]]},
                "temporal": {"interval": [[start, None]]},
            },
            "links": [],
        }

    return [
        doc(
            SOURCE_COLLECTION,
            "GOES-19 ABI L2 CMIPC band 13 (source)",
            "Single-band CMI granules (clean IR, band 13), catalogued in place"
            " from NODD. The source of the goes19-c13-cube virtual cube.",
            "2025-04-07T00:00:00Z",
        ),
        doc(
            CUBE_COLLECTION,
            "GOES-19 band 13 virtual cube",
            "A rolling virtual Icechunk cube over goes19-cmipc-c13. Starts empty;"
            " the cube sink fills it.",
            "2025-04-07T00:00:00Z",
        ),
    ]


def _ensure_settings(conn: psycopg.Connection, collection_id: str, *, serving: bool) -> None:
    conn.execute(
        "INSERT INTO stac_higher.collection_settings (collection_id, group_id, serving_enabled)"
        " VALUES (%s, %s, %s) ON CONFLICT (collection_id) DO NOTHING",
        (collection_id, GROUP, serving),
    )


def _extractor_is_current(conn: psycopg.Connection) -> bool:
    """True when the cube's extractor is deployed, enabled, and runs today's code."""
    row = conn.execute(
        "SELECT r.code FROM stac_higher.processes p"
        "  JOIN stac_higher.process_revisions r ON r.id = p.current_revision"
        " WHERE p.id = %s AND p.enabled AND p.deleted_at IS NULL",
        (EXTRACTOR_ID,),
    ).fetchone()
    return bool(row) and row[0] == extractor_code()


def _existing_connection(conn: psycopg.Connection) -> object | None:
    row = conn.execute(
        "SELECT id FROM stac_higher.connections WHERE name = %s AND deleted_at IS NULL",
        (goes.CONNECTION_NAME,),
    ).fetchone()
    return row[0] if row else None


def seed(args: argparse.Namespace) -> int:
    check_migrations(args.database_url, REQUIRED_MIGRATION)

    with psycopg.connect(args.database_url, autocommit=True) as conn:
        # Same refusal as goes-seed, checked before any write: a second
        # enabled poller on the source would ingest every file twice.
        rows = goes.competing_ingest_associations(conn, (SOURCE_COLLECTION,))
        if rows:
            raise SystemExit(
                "another ENABLED ingest association already targets "
                f"{SOURCE_COLLECTION}:\n{goes._describe(rows)}\n"
                "Disable it first, or it will ingest the same files twice."
            )

        connection_id = _existing_connection(conn)
        if connection_id is None:
            # Only a new connection needs the master key (it seals the empty
            # envelope an anonymous connection stores).
            master_key = load_master_key(dict(os.environ))
            connection_id = upsert_connection(
                conn,
                name=goes.CONNECTION_NAME,
                protocol="s3",
                config=goes.nodd_connection_config(),
                credentials=seal("{}", master_key),
                group=GROUP,
                created_by=CREATED_BY,
            )
            say(f"  connection {goes.CONNECTION_NAME} created (anonymous NODD)")
        else:
            say(f"  connection {goes.CONNECTION_NAME} reused")

        say("catalog")
        for document in collection_documents():
            put_collection(args.stac_url, document)

        say("platform")
        _ensure_settings(conn, SOURCE_COLLECTION, serving=False)
        _ensure_settings(conn, CUBE_COLLECTION, serving=False)

        if _extractor_is_current(conn):
            say(f"  process {EXTRACTOR_NAME} reused (up to date)")
        else:
            install_process(
                conn,
                process_id=EXTRACTOR_ID,
                revision_id=EXTRACTOR_REVISION_ID,
                name=EXTRACTOR_NAME,
                description=(
                    "Cube source extractor: dates each ABI granule from its filename and"
                    " footprints it from the CMI grid (CMI_C02 or CMI)."
                ),
                group=GROUP,
                created_by=CREATED_BY,
                kind="extractor",
                max_runs_per_hour=600,
                runtime=goes.EXTRACTOR_RUNTIME,
                code=extractor_code(),
            )
            say(f"  process {EXTRACTOR_NAME} deployed (extractor)")

        association = upsert_association(
            conn,
            collection_id=SOURCE_COLLECTION,
            connection_id=connection_id,
            direction="ingest",
            config=ingest_config(),
            created_by=CREATED_BY,
        )
        say(
            f"  ingest association {association}"
            f" (s3://{goes.NODD_BUCKET}/{PRODUCT_PREFIX}, include {', '.join(INCLUDE)},"
            f" window {WINDOW_BEGIN}, {MAX_FILES_PER_POLL} file/poll)"
        )

    say("")
    say("The cube sink is not created here. Next step:")
    say(f"  {NEXT_STEP}")
    say("Watch the source fill with:  uv run python -m pipeline.demo cube-status")
    return 0


# --------------------------------------------------------------------------- #
# status / teardown
# --------------------------------------------------------------------------- #


def status(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url) as conn:
        ledger = conn.execute(
            "SELECT f.status, count(*)"
            "  FROM stac_higher.ingest_files f"
            "  JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
            " WHERE cc.collection_id = %s AND cc.direction = 'ingest'"
            " GROUP BY f.status ORDER BY f.status",
            (SOURCE_COLLECTION,),
        ).fetchall()
        sinks = conn.execute(
            "SELECT s.enabled, coalesce(s.last_error, ''),"
            "       (SELECT string_agg(status || '=' || n, ', ' ORDER BY status) FROM ("
            "          SELECT status, count(*) AS n FROM stac_higher.cube_appends"
            "           WHERE cube_sink_id = s.id GROUP BY status) x)"
            "  FROM stac_higher.cube_sinks s WHERE s.cube_collection_id = %s",
            (CUBE_COLLECTION,),
        ).fetchall()
    say(
        "ingest ledger: "
        + (", ".join(f"{state}={n}" for state, n in ledger) or "empty (no file discovered yet)")
    )
    if not sinks:
        say(f"cube sink: none yet ({NEXT_STEP})")
    for enabled, error, appends in sinks:
        say(f"cube sink: enabled={enabled} appends[{appends or 'none'}] error={error[:80]}")
    for collection_id in (SOURCE_COLLECTION, CUBE_COLLECTION):
        code, body = request(f"{args.stac_url}/collections/{collection_id}/items?limit=100")
        count = len(json.loads(body).get("features", [])) if code == 200 else -1
        say(f"{collection_id}: {count} item(s)")
    return 0


def teardown(args: argparse.Namespace) -> int:
    """Remove what `cube-seed` made — and a sink created later through the UI.

    Bytes never go from here: like the app's catalog delete, this marks the
    whole collection prefix `collection_delete` in `asset_gc`, and the GC job
    removes `assets/{cube}/_cube/` after the collection's grace period. The
    standing GOES demo's rows (its collections, `goes-abi-metadata`) are not
    touched; the `goes-nodd` connection goes only when this seed created it
    and nothing else still uses it.
    """
    collections = (CUBE_COLLECTION, SOURCE_COLLECTION)
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        others = goes.competing_ingest_associations(
            conn, (SOURCE_COLLECTION,), include_disabled=True
        )
        if others and not getattr(args, "force", False):
            say(goes._describe(others))
            raise SystemExit(
                "cube-teardown: other ingest associations target the source collection;"
                " delete them first or pass --force"
            )

        # A sink made through the UI. The ledger rows cascade with it.
        conn.execute(
            "DELETE FROM stac_higher.cube_sinks"
            " WHERE cube_collection_id = %s OR source_collection_id = %s",
            (CUBE_COLLECTION, SOURCE_COLLECTION),
        )

        # The FK order goes-teardown documents: ledger, then the association.
        for table in ("ingest_files", "delivery_log", "delivery_backfills"):
            conn.execute(
                f"DELETE FROM stac_higher.{table} t"
                " USING stac_higher.collection_connections cc"
                " WHERE t.association_id = cc.id AND cc.collection_id = %s",
                (SOURCE_COLLECTION,),
            )
        conn.execute(
            "DELETE FROM stac_higher.collection_connections WHERE collection_id = %s",
            (SOURCE_COLLECTION,),
        )
        conn.execute(
            "DELETE FROM stac_higher.connection_checks ch"
            " USING stac_higher.connections c"
            " WHERE ch.connection_id = c.id AND c.name = %s AND c.created_by = %s"
            "   AND NOT EXISTS (SELECT 1 FROM stac_higher.collection_connections cc"
            "                    WHERE cc.connection_id = c.id)",
            (goes.CONNECTION_NAME, CREATED_BY),
        )
        conn.execute(
            "DELETE FROM stac_higher.connections c"
            " WHERE c.name = %s AND c.created_by = %s"
            "   AND NOT EXISTS (SELECT 1 FROM stac_higher.collection_connections cc"
            "                    WHERE cc.connection_id = c.id)"
            "   AND NOT EXISTS (SELECT 1 FROM stac_higher.connection_checks ch"
            "                    WHERE ch.connection_id = c.id)",
            (goes.CONNECTION_NAME, CREATED_BY),
        )
        remove_process(conn, EXTRACTOR_ID)

        grace = {
            cid: days
            for cid, days in conn.execute(
                "SELECT collection_id, gc_grace_days FROM stac_higher.collection_settings"
                " WHERE collection_id = ANY(%s)",
                (list(collections),),
            ).fetchall()
        }
        conn.execute(
            "DELETE FROM stac_higher.collection_settings WHERE collection_id = ANY(%s)",
            (list(collections),),
        )
    say("platform rows removed")

    for collection_id in collections:
        code, _ = request(f"{args.stac_url}/collections/{collection_id}", method="DELETE")
        say(f"  collection {collection_id}: {code}")
        if code not in (200, 204):
            continue
        # The app's catalog delete does this after a successful upstream delete
        # (ADR 0011); a direct STAC DELETE bypasses the app, so do it here.
        with psycopg.connect(args.database_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO stac_higher.asset_gc"
                " (object_key, collection_id, item_id, reason, collect_after)"
                " VALUES (%s, %s, NULL, 'collection_delete', now() + make_interval(days => %s))"
                " ON CONFLICT (object_key) WHERE collected_at IS NULL DO NOTHING",
                (f"assets/{collection_id}/", collection_id, grace.get(collection_id, 30)),
            )
    say("  asset_gc marked; bytes (incl. _cube/) go with asset_gc after the grace period")
    return 0
