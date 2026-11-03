"""Migration 033's grant, as the cube-server sees it (DB-gated).

Needs ``DATABASE_URL`` (a superuser) on a database the app's migrations ran
on: CI applies them with ``npm run db:migrate`` before this suite.
"""

import os
import uuid

import psycopg
import pytest

from stac_higher_cube_server.sinks import ENABLED_SINKS_SQL, PgSinkSource

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@pytest.fixture
def sink():
    collection = f"z7-cube-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        sink_id = conn.execute(
            "INSERT INTO stac_higher.cube_sinks "
            "(source_collection_id, cube_collection_id, config, source_prefixes, created_by) "
            "VALUES (%s, %s, %s, %s, 'z7-test') RETURNING id::text",
            (
                f"{collection}-src",
                collection,
                psycopg.types.json.Jsonb({"append_dim": "t", "variables": ["CMI"],
                                          "loadable_variables": ["t"]}),
                ["s3://noaa-goes19/"],
            ),
        ).fetchone()[0]
    try:
        yield sink_id, collection
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM stac_higher.cube_sinks WHERE id = %s", (sink_id,))


def test_reader_role_reads_the_enabled_sinks(sink):
    sink_id, collection = sink
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET ROLE stac_higher_cube_reader")
        rows = conn.execute(ENABLED_SINKS_SQL).fetchall()
    assert (sink_id, collection) in [(r[0], r[1]) for r in rows]


def test_reader_role_cannot_read_other_columns_or_the_ledger(sink):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET ROLE stac_higher_cube_reader")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT last_error FROM stac_higher.cube_sinks")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM stac_higher.cube_appends")


def test_pg_sink_source_parses_rows(sink):
    sink_id, collection = sink
    found = [s for s in PgSinkSource(DSN).enabled_sinks() if s.id == sink_id]
    assert len(found) == 1
    assert found[0].cube_collection_id == collection
    assert found[0].variables == ("CMI",)
    assert found[0].source_prefixes == ("s3://noaa-goes19/",)
