"""Generic `stac_higher` row writers shared by the demo seeders.

The demo and the GOES seed write platform rows directly (the loadgen split:
pgstac through the API, `stac_higher` through SQL); the app owns the DDL
(ADR 0001) and nothing here creates or alters a table.

Everything is idempotent: a seeder is re-run after a `docker compose down -v`
*and* on a stack that already carries it, so every writer either finds its row
or makes one, and never stacks duplicates.

The HTTP/S3/announce helpers live here too, so `demo/goes/seed.py` can use them
without importing the CLI module that imports it back.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable, Sequence

import boto3
import psycopg
from botocore.client import Config

# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def say(message: str) -> None:
    print(message, flush=True)


def request(url: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
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


def s3_client(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)


def check_migrations(database_url: str, required_migration: str) -> None:
    with psycopg.connect(database_url) as conn:
        try:
            row = conn.execute(
                "SELECT count(*) FROM stac_higher.migrations WHERE name = %s",
                (required_migration,),
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
            f"migration {required_migration} is not applied — this database is\n"
            "older than the feature this command seeds. Start the app once\n"
            "against it so it migrates, then re-run."
        )


def put_collection(stac_url: str, document: dict) -> None:
    collection_id = document["id"]
    status, payload = request(f"{stac_url}/collections", method="POST", body=document)
    if status == 409:
        status, payload = request(
            f"{stac_url}/collections/{collection_id}", method="PUT", body=document
        )
    if status not in (200, 201):
        raise SystemExit(f"collection {collection_id}: {status} {payload[:200]!r}")
    say(f"  collection {collection_id}")


# --------------------------------------------------------------------------- #
# platform rows
# --------------------------------------------------------------------------- #


def enable_serving(conn: psycopg.Connection, collection_id: str, group: str) -> None:
    conn.execute(
        "INSERT INTO stac_higher.collection_settings (collection_id, group_id, serving_enabled)"
        " VALUES (%s, %s, true)"
        " ON CONFLICT (collection_id) DO UPDATE SET serving_enabled = true",
        (collection_id, group),
    )


def install_process(
    conn: psycopg.Connection,
    *,
    process_id: str,
    revision_id: str,
    name: str,
    description: str,
    group: str,
    created_by: str,
    kind: str,
    max_runs_per_hour: int,
    runtime: dict,
    code: str,
    sources: Sequence[tuple[str, dict]] = (),
    outputs: Iterable[str] = (),
) -> None:
    """Create (or re-deploy) one process, its trigger sources and its outputs.

    A revision is immutable, so re-seeding replaces its row wholesale rather
    than stacking a new one every run — this is a demo fixture, not history.
    """
    conn.execute(
        "INSERT INTO stac_higher.processes"
        " (id, name, description, group_id, kind, enabled, max_runs_per_hour, created_by)"
        " VALUES (%s, %s, %s, %s, %s, true, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET"
        "   name = EXCLUDED.name, description = EXCLUDED.description,"
        "   enabled = true, deleted_at = NULL",
        (process_id, name, description, group, kind, max_runs_per_hour, created_by),
    )
    conn.execute(
        "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
        (process_id,),
    )
    conn.execute("DELETE FROM stac_higher.process_runs WHERE process_id = %s", (process_id,))
    conn.execute("DELETE FROM stac_higher.process_revisions WHERE process_id = %s", (process_id,))
    conn.execute(
        "INSERT INTO stac_higher.process_revisions"
        " (id, process_id, runtime, code, env, created_by)"
        " VALUES (%s, %s, %s::jsonb, %s, '[]'::jsonb, %s)",
        (revision_id, process_id, json.dumps(runtime), code, created_by),
    )
    conn.execute(
        "UPDATE stac_higher.processes SET current_revision = %s WHERE id = %s",
        (revision_id, process_id),
    )
    for collection_id, trigger in sources:
        conn.execute(
            "INSERT INTO stac_higher.process_sources"
            " (process_id, collection_id, trigger, flow_stats, enabled)"
            " SELECT %s, %s, %s::jsonb, '{}'::jsonb, true"
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM stac_higher.process_sources"
            "     WHERE process_id = %s AND collection_id = %s)",
            (process_id, collection_id, json.dumps(trigger), process_id, collection_id),
        )
    for collection_id in outputs:
        conn.execute(
            "INSERT INTO stac_higher.process_outputs (process_id, collection_id)"
            " SELECT %s, %s"
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM stac_higher.process_outputs"
            "     WHERE process_id = %s AND collection_id = %s)",
            (process_id, collection_id, process_id, collection_id),
        )


def remove_process(conn: psycopg.Connection, process_id: str) -> None:
    """Delete one process and everything that points at it.

    `current_revision` is nulled first: the process row references its revision,
    so the revisions cannot go while it still points at one.
    """
    conn.execute(
        "UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s",
        (process_id,),
    )
    for table in (
        "process_runs",
        "process_sources",
        "process_outputs",
        "process_checks",
        "process_revisions",
    ):
        conn.execute(f"DELETE FROM stac_higher.{table} WHERE process_id = %s", (process_id,))
    conn.execute("DELETE FROM stac_higher.processes WHERE id = %s", (process_id,))


def upsert_connection(
    cur_or_conn,
    *,
    name: str,
    protocol: str,
    config: dict,
    credentials: bytes | None,
    group: str,
    created_by: str,
) -> uuid.UUID:
    """Find-or-make one connection by name. `credentials` is the SEALED
    envelope; pass `seal("{}", key)` for an anonymous connection (G-1) — the
    pipeline's build_adapter treats a NULL column as "no credentials" and
    refuses it, so None is never the anonymous case."""
    row = cur_or_conn.execute(
        "SELECT id FROM stac_higher.connections WHERE name = %s AND deleted_at IS NULL",
        (name,),
    ).fetchone()
    if row:
        cur_or_conn.execute(
            "UPDATE stac_higher.connections"
            "   SET config = %s::jsonb, credentials = %s, enabled = true"
            " WHERE id = %s",
            (json.dumps(config), credentials, row[0]),
        )
        return row[0]
    return cur_or_conn.execute(
        "INSERT INTO stac_higher.connections"
        " (name, description, protocol, config, credentials, group_id, created_by, enabled)"
        " VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, true) RETURNING id",
        (name, name, protocol, json.dumps(config), credentials, group, created_by),
    ).fetchone()[0]


def upsert_association(
    conn,
    *,
    collection_id: str,
    connection_id: uuid.UUID,
    direction: str,
    config: dict,
    created_by: str,
) -> uuid.UUID:
    """Find-or-make one association, matched on
    `(collection_id, connection_id, direction)`."""
    row = conn.execute(
        "SELECT id FROM stac_higher.collection_connections"
        " WHERE collection_id = %s AND connection_id = %s AND direction = %s"
        "   AND deleted_at IS NULL",
        (collection_id, connection_id, direction),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE stac_higher.collection_connections"
            "   SET config = %s::jsonb, enabled = true WHERE id = %s",
            (json.dumps(config), row[0]),
        )
        return row[0]
    return conn.execute(
        "INSERT INTO stac_higher.collection_connections"
        " (collection_id, connection_id, direction, config, created_by, enabled)"
        " VALUES (%s, %s, %s, %s::jsonb, %s, true) RETURNING id",
        (collection_id, connection_id, direction, json.dumps(config), created_by),
    ).fetchone()[0]
