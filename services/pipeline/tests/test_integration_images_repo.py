"""PgImagesRepo against a migrated database -- auto-skips unless DATABASE_URL
is set AND migration 030 has been applied (hit any app API route once).

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \\
        uv run pytest tests/test_integration_images_repo.py
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

DIGEST = "sha256:" + "c" * 64


@pytest.fixture
async def seeded():
    import psycopg

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute("SELECT to_regclass('stac_higher.container_images')")
        if (await cur.fetchone())[0] is None:
            pytest.skip("migration 030 is not applied to this database")
        reference = f"ghcr.io/itest/img-{uuid.uuid4().hex[:12]}"
        cur = await conn.execute(
            "INSERT INTO stac_higher.container_images (reference, tag_at_add, added_by)"
            " VALUES (%s, '1.0', 'itest') RETURNING id::text",
            (reference,),
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(
            "INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)"
            " VALUES (%s::uuid, 'admission', 'itest') RETURNING id::text",
            (image_id,),
        )
        (scan_id,) = await cur.fetchone()
    yield reference, image_id, scan_id
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )
    from pipeline.db.pool import close_pools

    await close_pools()


async def test_claim_record_and_finish_round_trip(seeded):
    import psycopg

    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import SEVERITIES, parse_scan_result

    reference, image_id, scan_id = seeded

    # This database is shared across sessions/tests. Rather than claim
    # first and discover afterward that we grabbed a foreign row (which
    # would leave that row "scanning"/"running" for its own test to trip
    # over), check BEFORE claiming: if another scan is already pending,
    # skip outright so this test never claims or strands a row it does not
    # own.
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.image_scans"
            " WHERE status = 'pending' AND id <> %s::uuid",
            (scan_id,),
        )
        (other_pending,) = await cur.fetchone()
    if other_pending:
        pytest.skip(
            "another pending scan already exists on this shared database; "
            "run on a quiet stack"
        )

    repo = PgImagesRepo(DATABASE_URL)
    claimed = await repo.claim_pending_scan(max_running=1000)
    assert claimed is not None and claimed.id == scan_id
    assert claimed.image.status == "scanning"
    result = parse_scan_result(
        {
            "version": 1, "kind": "admission", "reference": reference, "tag": "1.0",
            "digest": DIGEST, "platform_digest": DIGEST,
            "platform": {"os": "linux", "architecture": "amd64"}, "size_bytes": 10,
            "config": {"user": "", "entrypoint": None, "cmd": None},
            "scanner": {"syft": "1", "grype": "1", "db_built_at": "2026-09-27T00:00:00Z"},
            "sbom_ref": f"scans/{image_id}/{scan_id}/sbom.syft.json",
            "findings_ref": f"scans/{image_id}/{scan_id}/findings.grype.json",
            "counts": dict.fromkeys(SEVERITIES, 0),
            "fixed_counts": dict.fromkeys(SEVERITIES, 0),
            "kev": [], "max_risk": 0.0, "top": [], "tag_drift": None, "error": None,
        }
    )
    now = dt.datetime.now(dt.UTC)
    changed = await repo.record_admission(
        image_id,
        scan_id=scan_id,
        result=result,
        verdict={"pass": True},
        status="approved",
        expected_status="scanning",
        at=now,
    )
    assert changed is True
    await repo.finish_scan(
        scan_id, status="done", result={"version": 1}, findings_ref=result.findings_ref,
        log_ref=None, executor_handle="c1",
    )
    row = await repo.get_image(image_id)
    assert (row.status, row.digest, row.last_scanned_at is not None) == ("approved", DIGEST, True)
    assert await repo.scan_statuses([scan_id]) == {scan_id: "done"}
    assert await repo.find_image_by_digest(reference, DIGEST, exclude_id=str(uuid.uuid4())) == row
    assert await repo.fail_stalled_scans(started_before=now - dt.timedelta(days=3650)) == 0

    # B1: a stale expected_status is a no-op that changes nothing.
    stale = await repo.record_admission(
        image_id,
        scan_id=scan_id,
        result=result,
        verdict={"pass": False},
        status="rejected",
        expected_status="pending",
        at=now,
    )
    assert stale is False
    assert (await repo.get_image(image_id)).status == "approved"
