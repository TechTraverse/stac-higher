"""PgCollectionPublisher against a real pgstac (spec §6.3) -- auto-skips unless
DATABASE_URL is set AND migration 032 has been applied.

    DATABASE_URL=postgresql://... uv run pytest tests/test_integration_cube_collection.py

The pipeline's first production collection write. Every test creates its own
uuid-suffixed collection and sink and removes both in teardown.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import numpy as np
import pytest

from _cube_sources import GOES_CONFIG, GOES_PROJECTION, as_ns, scan
from pipeline.cubes.collection import (
    MISSING_COLLECTION,
    PUBLISHED,
    SUPERSEDED,
    UNCHANGED,
    PgCollectionPublisher,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.repo import CubeSink
from pipeline.cubes.steps import StaticSpec, canonical_attrs
from pipeline.cubes.write import BatchResult

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

CONFIG = parse_cube_sink_config(GOES_CONFIG)
PREFIXES = ["s3://noaa-goes19/"]


def result(snapshot: str) -> BatchResult:
    return BatchResult(
        outcomes={},
        snapshot_id=snapshot,
        committed=True,
        values=np.array([as_ns(scan(0)), as_ns(scan(1))]),
        trimmed=0,
        initialised=True,
        statics={
            "x": StaticSpec(np.array([-0.1, 0.1], "f4"), None),
            "y": StaticSpec(np.array([0.1, 0.0], "f4"), None),
            "goes_imager_projection": StaticSpec(
                np.array(0, "i4"), canonical_attrs(GOES_PROJECTION)
            ),
        },
        spatial_dims=("y", "x"),
    )


@pytest.fixture
async def db():
    """(raw autocommit connection, the CubeSink, the publisher); the
    collection and the sink are removed after."""
    import psycopg
    from psycopg.types.json import Jsonb

    from pipeline.db.pool import close_pools

    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
    cur = await conn.execute("SELECT to_regclass('stac_higher.cube_sinks')")
    if (await cur.fetchone())[0] is None:
        await conn.close()
        pytest.skip("migration 032 not applied")
    cube = f"z5-cube-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "SELECT pgstac.create_collection(%s)",
        (
            Jsonb(
                {
                    "type": "Collection",
                    "id": cube,
                    "stac_version": "1.0.0",
                    "description": "itest",
                    "license": "proprietary",
                    "links": [],
                    "keywords": ["user"],
                    "extent": {
                        "spatial": {"bbox": [[-180, -90, 180, 90]]},
                        "temporal": {"interval": [[None, None]]},
                    },
                }
            ),
        ),
    )
    cur = await conn.execute(
        "INSERT INTO stac_higher.cube_sinks"
        " (source_collection_id, cube_collection_id, config, created_by,"
        "  source_prefixes, last_snapshot_id)"
        " VALUES (%s, %s, %s, 'itest', %s, 'SNAP1')"
        " RETURNING id::text, updated_at::text",
        (f"{cube}-src", cube, Jsonb(GOES_CONFIG), PREFIXES),
    )
    sink_id, version = await cur.fetchone()
    sink = CubeSink(
        id=sink_id,
        source_collection_id=f"{cube}-src",
        cube_collection_id=cube,
        enabled=True,
        config=dict(GOES_CONFIG),
        source_prefixes=tuple(PREFIXES),
        last_snapshot_id="SNAP1",
        version=version,
    )
    publisher = PgCollectionPublisher(DATABASE_URL, "platform")
    yield conn, sink, publisher
    await conn.execute("DELETE FROM stac_higher.cube_sinks WHERE id = %s", (sink_id,))
    await conn.execute("SELECT pgstac.delete_collection(%s)", (cube,))
    await conn.close()
    await close_pools()


async def content(conn, collection_id: str) -> dict:
    cur = await conn.execute(
        "SELECT content FROM pgstac.collections WHERE id = %s", (collection_id,)
    )
    return (await cur.fetchone())[0]


async def test_a_publish_merges_the_cube_and_keeps_user_keys(db):
    conn, sink, publisher = db
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == PUBLISHED
    doc = await content(conn, sink.cube_collection_id)
    asset = doc["assets"]["cube"]
    assert asset["href"] == f"s3://platform/assets/{sink.cube_collection_id}/_cube/"
    assert asset["version"] == "SNAP1"
    assert asset["stac_higher:virtual_chunk_prefixes"] == PREFIXES
    assert asset["stac_higher:time_values"] == [
        "2026-10-03T17:00:00.000000000Z",
        "2026-10-03T17:05:00.000000000Z",
    ]
    assert doc["extent"]["temporal"]["interval"] == [
        ["2026-10-03T17:00:00.000000000Z", "2026-10-03T17:05:00.000000000Z"]
    ]
    assert doc["cube:dimensions"]["x"]["reference_system"]["type"] == "ProjectedCRS"
    assert doc["keywords"] == ["user"]
    assert doc["extent"]["spatial"] == {"bbox": [[-180, -90, 180, 90]]}
    # pgstac's generated columns read the nanosecond strings
    cur = await conn.execute(
        "SELECT datetime, end_datetime FROM pgstac.collections WHERE id = %s",
        (sink.cube_collection_id,),
    )
    start, end = await cur.fetchone()
    assert (start.minute, end.minute) == (0, 5)
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == UNCHANGED


async def test_a_removed_asset_comes_back(db):
    conn, sink, publisher = db
    await publisher.publish(sink, CONFIG, result("SNAP1"))
    doc = await content(conn, sink.cube_collection_id)
    del doc["assets"]["cube"]
    doc["title"] = "edited"
    from psycopg.types.json import Jsonb

    await conn.execute("SELECT pgstac.update_collection(%s)", (Jsonb(doc),))
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == PUBLISHED
    doc = await content(conn, sink.cube_collection_id)
    assert doc["assets"]["cube"]["version"] == "SNAP1"
    assert doc["title"] == "edited"


async def test_a_concurrent_bff_edit_is_not_lost(db):
    """The BFF's edit holds the row when the publish starts: the publish waits
    on FOR NO KEY UPDATE, then merges into the edited document. A plain SELECT
    would read the old document and its update would overwrite the edit."""
    import psycopg
    from psycopg.types.json import Jsonb

    conn, sink, publisher = db
    edited = {**await content(conn, sink.cube_collection_id), "title": "bff edit"}
    bff = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        await bff.execute("SELECT pgstac.update_collection(%s)", (Jsonb(edited),))
        task = asyncio.create_task(publisher.publish(sink, CONFIG, result("SNAP1")))
        for _ in range(100):  # until the publish is waiting on the row lock
            cur = await conn.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND query LIKE %s",
                ("%FOR NO KEY UPDATE%",),
            )
            if (await cur.fetchone())[0]:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the publish never waited on the collection row lock")
        assert not task.done()
        await bff.commit()
        assert await task == PUBLISHED
    finally:
        await bff.close()
    doc = await content(conn, sink.cube_collection_id)
    assert doc["title"] == "bff edit"
    assert doc["assets"]["cube"]["version"] == "SNAP1"


async def test_a_row_held_too_long_times_out_instead_of_waiting(db):
    """The job holds the sink's cube:{id} lock while it waits, so the wait is
    bounded: the timeout is a DB error, and the job retries."""
    import psycopg
    from psycopg.types.json import Jsonb

    conn, sink, publisher = db
    publisher.lock_timeout = "200ms"
    held = await content(conn, sink.cube_collection_id)
    bff = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        await bff.execute("SELECT pgstac.update_collection(%s)", (Jsonb(held),))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            await publisher.publish(sink, CONFIG, result("SNAP1"))
    finally:
        await bff.rollback()
        await bff.close()
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == PUBLISHED


async def test_a_tip_that_is_not_the_recorded_one_is_not_published(db):
    conn, sink, publisher = db
    assert await publisher.publish(sink, CONFIG, result("SNAP0")) == SUPERSEDED
    assert "assets" not in await content(conn, sink.cube_collection_id)


async def test_a_missing_cube_collection_is_a_warning_not_an_error(db):
    _, sink, publisher = db
    gone = CubeSink(**{**sink.__dict__, "cube_collection_id": f"{sink.cube_collection_id}-gone"})
    assert await publisher.publish(gone, CONFIG, result("SNAP1")) == MISSING_COLLECTION
