"""PgImagesRepo against a migrated database -- auto-skips unless DATABASE_URL
is set AND migration 030 has been applied (hit any app API route once).

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \\
        uv run pytest tests/test_integration_images_repo.py

This database is SHARED with a live demo and other sessions. Every test
inserts only the rows it needs (by a uuid-suffixed reference/name it
generates itself), deletes exactly those rows in a ``finally`` (or fixture
teardown), and -- for the two methods that sweep the WHOLE table with no
per-caller scope (``claim_pending_scan``, ``fail_stalled_scans``) -- checks
before acting that no OTHER row already in that state exists, skipping
rather than claiming or stalling a row this test does not own.
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

INSERT_IMAGE_SQL = (
    "INSERT INTO stac_higher.container_images"
    " (reference, tag_at_add, digest, status, added_by,"
    "  exception_reason, exception_by, exception_at, exception_expires_at)"
    " VALUES (%s, %s, %s, %s, 'itest', %s, %s, %s, %s) RETURNING id::text"
)

INSERT_SCAN_SQL = (
    "INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)"
    " VALUES (%s::uuid, %s, 'itest') RETURNING id::text"
)


def _admission_doc(reference: str, image_id: str, scan_id: str, *, digest: str = DIGEST) -> dict:
    from pipeline.images.scan_result import SEVERITIES

    return {
        "version": 1, "kind": "admission", "reference": reference, "tag": "1.0",
        "digest": digest, "platform_digest": digest,
        "platform": {"os": "linux", "architecture": "amd64"}, "size_bytes": 10,
        "config": {"user": "", "entrypoint": None, "cmd": None},
        "scanner": {"syft": "1", "grype": "1", "db_built_at": "2026-09-27T00:00:00Z"},
        "sbom_ref": f"scans/{image_id}/{scan_id}/sbom.syft.json",
        "findings_ref": f"scans/{image_id}/{scan_id}/findings.grype.json",
        "counts": dict.fromkeys(SEVERITIES, 0),
        "fixed_counts": dict.fromkeys(SEVERITIES, 0),
        "kev": [], "max_risk": 0.0, "top": [], "tag_drift": None, "error": None,
    }


@pytest.fixture
async def conn():
    """One raw connection per test for setup/teardown SQL, skipping outright
    if migration 030 has not been applied to this database. Also closes the
    process-wide async pool a ``PgImagesRepo`` in the test opened, function
    scoped like everything else here (a module-scoped async fixture would
    outlive the per-test event loop pytest-asyncio's default "auto" mode
    creates)."""
    import psycopg

    from pipeline.db.pool import close_pools

    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
    try:
        cur = await connection.execute("SELECT to_regclass('stac_higher.container_images')")
        if (await cur.fetchone())[0] is None:
            pytest.skip("migration 030 is not applied to this database")
        yield connection
    finally:
        await connection.close()
        await close_pools()


@pytest.fixture
async def seeded(conn):
    reference = f"ghcr.io/itest/img-{uuid.uuid4().hex[:12]}"
    cur = await conn.execute(
        INSERT_IMAGE_SQL, (reference, "1.0", None, "pending", None, None, None, None)
    )
    (image_id,) = await cur.fetchone()
    cur = await conn.execute(INSERT_SCAN_SQL, (image_id, "admission"))
    (scan_id,) = await cur.fetchone()
    try:
        yield reference, image_id, scan_id
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_claim_record_and_finish_round_trip(conn, seeded):
    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import parse_scan_result

    reference, image_id, scan_id = seeded

    # This database is shared across sessions/tests. Rather than claim
    # first and discover afterward that we grabbed a foreign row (which
    # would leave that row "scanning"/"running" for its own test to trip
    # over), check BEFORE claiming: if another scan is already pending,
    # skip outright so this test never claims or strands a row it does not
    # own.
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
    result = parse_scan_result(_admission_doc(reference, image_id, scan_id))
    now = dt.datetime.now(dt.UTC)
    changed = await repo.record_admission(
        image_id,
        scan_id=scan_id,
        result=result,
        verdict={"pass": True},
        status="approved",
        expected_status="scanning",
        expected_exception_expires_at=None,
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
        expected_exception_expires_at=None,
        at=now,
    )
    assert stale is False
    assert (await repo.get_image(image_id)).status == "approved"


async def test_record_admission_never_changes_a_revoked_row(conn):
    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import parse_scan_result

    reference = f"ghcr.io/itest/admit-revoked-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "revoked", None, None, None, None)
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (image_id, "admission"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        result = parse_scan_result(_admission_doc(reference, image_id, scan_id))
        now = dt.datetime.now(dt.UTC)
        changed = await repo.record_admission(
            image_id,
            scan_id=scan_id,
            result=result,
            verdict={"pass": True},
            status="approved",
            expected_status="revoked",
            expected_exception_expires_at=None,
            at=now,
        )
        assert changed is False
        row = await repo.get_image(image_id)
        assert (row.status, row.digest) == ("revoked", None)
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_record_admission_exception_expiry_mismatch_is_a_noop(conn):
    """B1 ruling 2: the compare-and-set also keys on exception_expires_at.
    An exception granted between claim and write must not be clobbered even
    though the status itself (here "approved") did not move."""
    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import parse_scan_result

    reference = f"ghcr.io/itest/exception-{uuid.uuid4().hex[:12]}"
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL,
            (reference, "1.0", DIGEST, "approved", "cve fixed upstream", "admin", expires, expires),
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (image_id, "admission"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        result = parse_scan_result(_admission_doc(reference, image_id, scan_id))
        now = dt.datetime.now(dt.UTC)
        # The caller read the image before the exception was granted, so it
        # expects no exception (None) even though status="approved" matches.
        changed = await repo.record_admission(
            image_id,
            scan_id=scan_id,
            result=result,
            verdict={"pass": True},
            status="approved",
            expected_status="approved",
            expected_exception_expires_at=None,
            at=now,
        )
        assert changed is False
        row = await repo.get_image(image_id)
        assert row.exception_expires_at is not None
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_record_rescan_hit_and_miss(conn):
    from pipeline.images.repo import PgImagesRepo

    reference = f"ghcr.io/itest/rescan-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", DIGEST, "approved", None, None, None, None)
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (image_id, "rescan"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        now = dt.datetime.now(dt.UTC)

        stale = await repo.record_rescan(
            image_id,
            scan_id=scan_id,
            verdict={"pass": True},
            status="approved",
            expected_status="flagged",
            expected_exception_expires_at=None,
            at=now,
        )
        assert stale is False
        assert (await repo.get_image(image_id)).status == "approved"

        changed = await repo.record_rescan(
            image_id,
            scan_id=scan_id,
            verdict={"pass": False},
            status="flagged",
            expected_status="approved",
            expected_exception_expires_at=None,
            at=now,
        )
        assert changed is True
        row = await repo.get_image(image_id)
        assert row.status == "flagged"
        assert row.last_scanned_at is not None
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_record_rescan_never_changes_a_revoked_row(conn):
    from pipeline.images.repo import PgImagesRepo

    reference = f"ghcr.io/itest/rescan-revoked-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "revoked", None, None, None, None)
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (image_id, "rescan"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        changed = await repo.record_rescan(
            image_id,
            scan_id=scan_id,
            verdict={"pass": True},
            status="approved",
            expected_status="revoked",
            expected_exception_expires_at=None,
            at=dt.datetime.now(dt.UTC),
        )
        assert changed is False
        assert (await repo.get_image(image_id)).status == "revoked"
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_merge_admission_stale_then_matching_expected_status(conn):
    """§9.1 dedup: a stale expected_status leaves the scan pointed at the
    provisional row and the provisional row in place; the matching call
    re-points the scan and deletes the provisional row."""
    from pipeline.images.repo import PgImagesRepo

    reference = f"ghcr.io/itest/merge-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "pending", None, None, None, None)
        )
        (provisional_id,) = await cur.fetchone()
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", DIGEST, "approved", None, None, None, None)
        )
        (existing_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (provisional_id, "admission"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        now = dt.datetime.now(dt.UTC)

        stale = await repo.merge_admission(
            provisional_id=provisional_id,
            existing_id=existing_id,
            scan_id=scan_id,
            verdict={"pass": True},
            status="approved",
            expected_status="rejected",
            expected_exception_expires_at=None,
            at=now,
        )
        assert stale is False
        cur = await conn.execute(
            "SELECT image_id::text FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
        )
        assert (await cur.fetchone())[0] == provisional_id
        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.container_images WHERE id = %s::uuid",
            (provisional_id,),
        )
        assert (await cur.fetchone())[0] == 1

        changed = await repo.merge_admission(
            provisional_id=provisional_id,
            existing_id=existing_id,
            scan_id=scan_id,
            verdict={"pass": True},
            status="approved",
            expected_status="approved",
            expected_exception_expires_at=None,
            at=now,
        )
        assert changed is True
        cur = await conn.execute(
            "SELECT image_id::text FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
        )
        assert (await cur.fetchone())[0] == existing_id
        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.container_images WHERE id = %s::uuid",
            (provisional_id,),
        )
        assert (await cur.fetchone())[0] == 0
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_merge_admission_never_changes_a_revoked_existing_row(conn):
    from pipeline.images.repo import PgImagesRepo

    reference = f"ghcr.io/itest/merge-revoked-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "pending", None, None, None, None)
        )
        (provisional_id,) = await cur.fetchone()
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "revoked", None, None, None, None)
        )
        (existing_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_SCAN_SQL, (provisional_id, "admission"))
        (scan_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        changed = await repo.merge_admission(
            provisional_id=provisional_id,
            existing_id=existing_id,
            scan_id=scan_id,
            verdict={"pass": True},
            status="approved",
            expected_status="revoked",
            expected_exception_expires_at=None,
            at=dt.datetime.now(dt.UTC),
        )
        assert changed is False
        assert (await repo.get_image(existing_id)).status == "revoked"
        cur = await conn.execute(
            "SELECT image_id::text FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
        )
        assert (await cur.fetchone())[0] == provisional_id
        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.container_images WHERE id = %s::uuid",
            (provisional_id,),
        )
        assert (await cur.fetchone())[0] == 1
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_fail_stalled_scans_fails_a_stalled_scan_and_flips_the_image(conn):
    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import parse_scan_result

    reference = f"ghcr.io/itest/stall-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "scanning", None, None, None, None)
        )
        (image_id,) = await cur.fetchone()
        started_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        cur = await conn.execute(
            "INSERT INTO stac_higher.image_scans (image_id, kind, status, requested_by, started_at)"
            " VALUES (%s::uuid, 'admission', 'running', 'itest', %s) RETURNING id::text",
            (image_id, started_at),
        )
        (scan_id,) = await cur.fetchone()

        # fail_stalled_scans sweeps EVERY running scan on the shared database
        # (no per-caller scope). Check first that no OTHER running scan
        # exists that this sweep would also touch and this test does not
        # own; skip rather than fail a foreign scan.
        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.image_scans"
            " WHERE status = 'running' AND id <> %s::uuid",
            (scan_id,),
        )
        (other_running,) = await cur.fetchone()
        if other_running:
            pytest.skip(
                "another running scan already exists on this shared database; "
                "run on a quiet stack"
            )

        repo = PgImagesRepo(DATABASE_URL)
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        count = await repo.fail_stalled_scans(started_before=cutoff)
        assert count == 1

        cur = await conn.execute(
            "SELECT status, result FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
        )
        status, result = await cur.fetchone()
        assert status == "failed"
        parsed = parse_scan_result(result)
        assert parsed.error is not None
        assert parsed.reference == reference

        row = await repo.get_image(image_id)
        assert row.status == "scan_failed"
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_mark_image_scan_failed(conn):
    from pipeline.images.repo import PgImagesRepo

    reference = f"ghcr.io/itest/markfail-{uuid.uuid4().hex[:12]}"
    try:
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", None, "pending", None, None, None, None)
        )
        (pending_id,) = await cur.fetchone()
        cur = await conn.execute(
            INSERT_IMAGE_SQL, (reference, "1.0", DIGEST, "approved", None, None, None, None)
        )
        (approved_id,) = await cur.fetchone()

        repo = PgImagesRepo(DATABASE_URL)
        await repo.mark_image_scan_failed(pending_id)
        await repo.mark_image_scan_failed(approved_id)
        assert (await repo.get_image(pending_id)).status == "scan_failed"
        assert (await repo.get_image(approved_id)).status == "approved"
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_get_registry_credential_round_trips_bytes_and_reports_soft_delete(conn):
    from psycopg.types.json import Json

    from pipeline.images.repo import PgImagesRepo

    payload = b"itest-envelope-bytes-not-a-real-credential"
    cur = await conn.execute(
        "INSERT INTO stac_higher.connections"
        " (name, protocol, config, credentials, group_id, created_by)"
        " VALUES (%s, 'registry', %s, %s, 'itest-group', 'itest') RETURNING id::text",
        (f"itest-registry-{uuid.uuid4().hex[:8]}", Json({"host": "registry.example"}), payload),
    )
    (connection_id,) = await cur.fetchone()
    try:
        repo = PgImagesRepo(DATABASE_URL)
        row = await repo.get_registry_credential(connection_id)
        assert row is not None
        assert row.protocol == "registry"
        assert row.config == {"host": "registry.example"}
        assert isinstance(row.credentials, bytes)
        assert row.credentials == payload
        assert row.deleted is False

        await conn.execute(
            "UPDATE stac_higher.connections SET deleted_at = now() WHERE id = %s::uuid",
            (connection_id,),
        )
        soft_deleted = await repo.get_registry_credential(connection_id)
        assert soft_deleted is not None and soft_deleted.deleted is True
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.connections WHERE id = %s::uuid", (connection_id,)
        )
