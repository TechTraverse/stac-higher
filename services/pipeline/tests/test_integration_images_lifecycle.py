"""PgImageLifecycleRepo, PgImageAlertsRepo and PgScanRetentionRepo against a
migrated database (final-review fix wave item 3: none of the six SQL
statements C-4 added had a DB-gated test). Auto-skips unless DATABASE_URL is
set AND migration 030 has been applied (hit any app API route once).

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \\
        uv run pytest tests/test_integration_images_lifecycle.py

This database is SHARED with a live demo and other sessions (the same
precondition as test_integration_images_repo.py). Every test inserts only
the rows it needs (a uuid-suffixed reference/name it generates itself) and
deletes exactly those rows in a ``finally``. ``audit_log`` is append-only
(migration 001's trigger refuses UPDATE/DELETE/TRUNCATE), so the one audit
row the EXPIRE_EXCEPTION hit test writes is never cleaned up -- accepted,
the same as any real audit row, and it is a single small row per test run.

``request_due_rescans``, ``detach_pruned_refs`` and ``prune_scan_rows`` sweep
the WHOLE table with no per-caller scope (like ``claim_pending_scan`` and
``fail_stalled_scans`` in test_integration_images_repo.py): each guards
first that no OTHER row already in the swept state exists, skipping rather
than requesting, detaching or deleting a row this test does not own.
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

DIGEST = "sha256:" + "d" * 64

INSERT_IMAGE_SQL = (
    "INSERT INTO stac_higher.container_images"
    " (reference, tag_at_add, digest, status, sbom_ref, last_scanned_at, added_by,"
    "  verdict, exception_reason, exception_by, exception_at, exception_expires_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, 'itest', %s, %s, %s, %s, %s) RETURNING id::text"
)

INSERT_SCAN_SQL = (
    "INSERT INTO stac_higher.image_scans"
    " (image_id, kind, status, requested_by, requested_at, result, findings_ref, log_ref)"
    " VALUES (%s::uuid, %s, %s, 'itest', %s, %s, %s, %s) RETURNING id::text"
)

INSERT_PROCESS_SQL = (
    "INSERT INTO stac_higher.processes (name, group_id, enabled, created_by, deleted_at)"
    " VALUES (%s, 'itest-group', %s, 'itest', %s) RETURNING id::text"
)

INSERT_REVISION_SQL = (
    "INSERT INTO stac_higher.process_revisions (process_id, runtime, created_by)"
    " VALUES (%s::uuid, %s, 'itest') RETURNING id::text"
)


@pytest.fixture
async def conn():
    """One raw connection per test for setup/teardown SQL, skipping outright
    if migration 030 has not been applied to this database. Also closes the
    process-wide async pool a Pg repo in the test opened (see
    test_integration_images_repo.py's identical fixture for why)."""
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


async def _insert_image(conn, *, reference, status, exception_expires_at=None, **overrides):
    from psycopg.types.json import Json

    fields = {
        "digest": DIGEST,
        "sbom_ref": f"scans/{reference}/s/sbom.syft.json",
        "last_scanned_at": None,
        "verdict": None,
        "exception_reason": None,
        "exception_by": None,
        "exception_at": None,
    }
    fields.update(overrides)
    verdict = fields["verdict"]
    cur = await conn.execute(
        INSERT_IMAGE_SQL,
        (
            reference,
            "1.0",
            fields["digest"],
            status,
            fields["sbom_ref"],
            fields["last_scanned_at"],
            Json(verdict) if verdict is not None else None,
            fields["exception_reason"],
            fields["exception_by"],
            fields["exception_at"],
            exception_expires_at,
        ),
    )
    (image_id,) = await cur.fetchone()
    return image_id


async def _insert_scan(
    conn,
    image_id,
    *,
    kind="admission",
    status="done",
    requested_at=None,
    result=None,
    findings_ref=None,
    log_ref=None,
):
    from psycopg.types.json import Json

    cur = await conn.execute(
        INSERT_SCAN_SQL,
        (
            image_id,
            kind,
            status,
            requested_at or dt.datetime.now(dt.UTC),
            Json(result) if result is not None else None,
            findings_ref,
            log_ref,
        ),
    )
    (scan_id,) = await cur.fetchone()
    return scan_id


# ---------------------------------------------------------------------------
# EXPIRE_EXCEPTION (lifecycle.py): expire_exception is a targeted, single-row
# compare-and-set (never a table sweep), so no foreign-row guard is needed.
# ---------------------------------------------------------------------------


async def test_expire_exception_hit_clears_the_four_columns_and_writes_one_audit_row(conn):
    from pipeline.images.lifecycle import PgImageLifecycleRepo

    reference = f"ghcr.io/itest/expire-hit-{uuid.uuid4().hex[:12]}"
    expires = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    try:
        image_id = await _insert_image(
            conn,
            reference=reference,
            status="approved",
            exception_expires_at=expires,
            exception_reason="vendor fix pending",
            exception_by="admin-1",
            exception_at=expires - dt.timedelta(days=30),
            verdict={"pass": False, "reasons": ["kev:CVE-2026-0001"]},
        )
        scan_id = await _insert_scan(conn, image_id, status="done")
        await conn.execute(
            "UPDATE stac_higher.container_images SET last_scan_id = %s::uuid WHERE id = %s::uuid",
            (scan_id, image_id),
        )

        repo = PgImageLifecycleRepo(DATABASE_URL)
        changed = await repo.expire_exception(
            image_id,
            expected_status="approved",
            expected_expires_at=expires,
            expected_last_scan_id=scan_id,
            status="approved",
            verdict={"pass": True, "reasons": []},
            audit_detail={"note": "itest hit"},
        )
        assert changed is True

        cur = await conn.execute(
            "SELECT status, exception_reason, exception_by, exception_at,"
            " exception_expires_at, verdict"
            " FROM stac_higher.container_images WHERE id = %s::uuid",
            (image_id,),
        )
        status, reason, by, at, expires_at, verdict = await cur.fetchone()
        assert status == "approved"
        assert (reason, by, at, expires_at) == (None, None, None, None)
        assert verdict == {"pass": True, "reasons": []}

        cur = await conn.execute(
            "SELECT actor, action, resource_type FROM stac_higher.audit_log"
            " WHERE resource_id = %s AND action = 'exception_expired'",
            (image_id,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0] == ("pipeline", "exception_expired", "container_image")
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_expire_exception_miss_on_a_stale_last_scan_id_writes_nothing(conn):
    """Important 2: a rescan the drain records between the tick's read and
    its write changes last_scan_id; the CAS must then miss and write
    nothing, matching the fake's contract in test_image_lifecycle.py."""
    from pipeline.images.lifecycle import PgImageLifecycleRepo

    reference = f"ghcr.io/itest/expire-miss-{uuid.uuid4().hex[:12]}"
    expires = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    try:
        image_id = await _insert_image(
            conn,
            reference=reference,
            status="approved",
            exception_expires_at=expires,
            exception_reason="vendor fix pending",
            exception_by="admin-1",
            exception_at=expires - dt.timedelta(days=30),
        )
        scan_a = await _insert_scan(conn, image_id, status="done")
        scan_b = await _insert_scan(conn, image_id, status="done")
        # The drain landed scan_b in between: last_scan_id now points past
        # what the tick read (scan_a).
        await conn.execute(
            "UPDATE stac_higher.container_images SET last_scan_id = %s::uuid WHERE id = %s::uuid",
            (scan_b, image_id),
        )

        repo = PgImageLifecycleRepo(DATABASE_URL)
        changed = await repo.expire_exception(
            image_id,
            expected_status="approved",
            expected_expires_at=expires,
            expected_last_scan_id=scan_a,
            status="flagged",
            verdict=None,
            audit_detail={"note": "itest miss"},
        )
        assert changed is False

        cur = await conn.execute(
            "SELECT status, exception_expires_at, last_scan_id::text"
            " FROM stac_higher.container_images WHERE id = %s::uuid",
            (image_id,),
        )
        status, expires_at, last_scan_id = await cur.fetchone()
        assert status == "approved"
        assert expires_at is not None
        assert last_scan_id == scan_b

        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.audit_log"
            " WHERE resource_id = %s AND action = 'exception_expired'",
            (image_id,),
        )
        (count,) = await cur.fetchone()
        assert count == 0
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


# ---------------------------------------------------------------------------
# DUE_RESCANS (lifecycle.py): request_due_rescans sweeps the WHOLE table.
# ---------------------------------------------------------------------------


async def _due_rescans_guard(conn, *, scanned_before, exclude_ids):
    """How many OTHER images the DUE_RESCANS predicate would also pick up
    (mirrors DUE_RESCANS_SQL's own WHERE, item 1's backoff included)."""
    cur = await conn.execute(
        "SELECT count(*) FROM stac_higher.container_images i"
        " WHERE i.status IN ('approved', 'flagged')"
        "   AND i.sbom_ref IS NOT NULL AND i.digest IS NOT NULL"
        "   AND (i.last_scanned_at IS NULL OR i.last_scanned_at <= %s)"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM stac_higher.image_scans s"
        "      WHERE s.image_id = i.id"
        "        AND (s.status IN ('pending', 'running')"
        "             OR (s.kind = 'rescan' AND s.requested_at > %s)))"
        "   AND NOT (i.id::text = ANY(%s))",
        (scanned_before, scanned_before, list(exclude_ids)),
    )
    (count,) = await cur.fetchone()
    return count


async def test_due_rescans_is_idempotent_while_a_rescan_is_pending(conn):
    from pipeline.images.lifecycle import PgImageLifecycleRepo

    reference = f"ghcr.io/itest/due-idempotent-{uuid.uuid4().hex[:12]}"
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(hours=24)
    try:
        image_id = await _insert_image(
            conn,
            reference=reference,
            status="approved",
            last_scanned_at=cutoff - dt.timedelta(hours=1),
        )
        if await _due_rescans_guard(conn, scanned_before=cutoff, exclude_ids=[image_id]):
            pytest.skip(
                "another image is already due for a rescan on this shared database; "
                "run on a quiet stack"
            )

        repo = PgImageLifecycleRepo(DATABASE_URL)
        first = await repo.request_due_rescans(scanned_before=cutoff)
        assert first == 1
        second = await repo.request_due_rescans(scanned_before=cutoff)
        assert second == 0

        cur = await conn.execute(
            "SELECT count(*) FROM stac_higher.image_scans"
            " WHERE image_id = %s::uuid AND kind = 'rescan' AND status = 'pending'"
            " AND requested_by = 'pipeline'",
            (image_id,),
        )
        (count,) = await cur.fetchone()
        assert count == 1
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_due_rescans_backs_off_a_failed_rescan_until_the_interval_passes(conn):
    """Important 1: a failed rescan never moves last_scanned_at; the backoff
    (item 1) blocks a re-request until its own requested_at clears the
    interval, regardless of the failed scan's status."""
    from pipeline.images.lifecycle import PgImageLifecycleRepo

    reference = f"ghcr.io/itest/due-backoff-{uuid.uuid4().hex[:12]}"
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(hours=24)
    try:
        image_id = await _insert_image(
            conn,
            reference=reference,
            status="approved",
            last_scanned_at=cutoff - dt.timedelta(hours=1),
        )
        await _insert_scan(
            conn,
            image_id,
            kind="rescan",
            status="failed",
            requested_at=now - dt.timedelta(hours=2),
            result={"version": 1, "kind": "rescan", "error": "the scan stalled"},
        )
        if await _due_rescans_guard(conn, scanned_before=cutoff, exclude_ids=[image_id]):
            pytest.skip(
                "another image is already due for a rescan on this shared database; "
                "run on a quiet stack"
            )

        repo = PgImageLifecycleRepo(DATABASE_URL)
        assert await repo.request_due_rescans(scanned_before=cutoff) == 0

        # The same failed rescan, requested before the interval: now re-requested.
        await conn.execute(
            "UPDATE stac_higher.image_scans SET requested_at = %s"
            " WHERE image_id = %s::uuid AND kind = 'rescan'",
            (cutoff - dt.timedelta(hours=1), image_id),
        )
        assert await repo.request_due_rescans(scanned_before=cutoff) == 1
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


# ---------------------------------------------------------------------------
# Retention (retention.py): detach_pruned_refs and prune_scan_rows sweep the
# WHOLE table by rank alone (no date filter on DETACH), so the guard below
# checks that no OTHER image already has more than the keep window's worth
# of scans -- if none does, neither statement can rank a foreign scan past
# the keep window, so neither can touch a row this test does not own.
# ---------------------------------------------------------------------------


async def test_kept_detach_and_prune_agree_on_thirteen_scans(conn):
    from pipeline.images.retention import KEEP_SCANS_PER_IMAGE, PgScanRetentionRepo

    cur = await conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT image_id FROM stac_higher.image_scans"
        "   GROUP BY image_id HAVING count(*) > %s"
        ") t",
        (KEEP_SCANS_PER_IMAGE,),
    )
    (foreign_over_keep,) = await cur.fetchone()
    if foreign_over_keep:
        pytest.skip(
            "another image already has more than the keep window's scans on this "
            "shared database; run on a quiet stack"
        )

    reference = f"ghcr.io/itest/retention-{uuid.uuid4().hex[:12]}"
    base = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    try:
        image_id = await _insert_image(conn, reference=reference, status="approved")
        scan_ids = []
        for i in range(13):
            scan_id = await _insert_scan(
                conn,
                image_id,
                status="done",
                requested_at=base + dt.timedelta(days=i),
                findings_ref=f"scans/{image_id}/s{i}/findings.grype.json",
                log_ref=f"scans/{image_id}/s{i}/log",
            )
            scan_ids.append(scan_id)
        newest_id = scan_ids[-1]  # i=12, the latest requested_at
        oldest_three = scan_ids[:3]  # i=0,1,2, the three that fall outside top 10
        await conn.execute(
            "UPDATE stac_higher.container_images SET last_scan_id = %s::uuid WHERE id = %s::uuid",
            (newest_id, image_id),
        )

        repo = PgScanRetentionRepo(DATABASE_URL)
        kept = await repo.list_kept_scans(keep=KEEP_SCANS_PER_IMAGE)
        kept_ids = {s.scan_id for s in kept if s.image_id == image_id}
        assert kept_ids == set(scan_ids[3:])  # the ten newest

        detached = await repo.detach_pruned_refs(keep=KEEP_SCANS_PER_IMAGE)
        assert detached == 3
        cur = await conn.execute(
            "SELECT id::text, findings_ref, log_ref FROM stac_higher.image_scans"
            " WHERE image_id = %s::uuid",
            (image_id,),
        )
        rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
        for scan_id in oldest_three:
            assert rows[scan_id] == (None, None)
        for scan_id in scan_ids[3:]:
            assert rows[scan_id] != (None, None)

        pruned = await repo.prune_scan_rows(
            older_than=dt.datetime.now(dt.UTC), keep=KEEP_SCANS_PER_IMAGE
        )
        assert pruned == 3
        cur = await conn.execute(
            "SELECT id::text FROM stac_higher.image_scans WHERE image_id = %s::uuid",
            (image_id,),
        )
        remaining = {r[0] for r in await cur.fetchall()}
        assert remaining == set(scan_ids[3:])  # the three oldest are gone
        assert newest_id in remaining  # last_scan_id survives regardless of rank
    finally:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


# ---------------------------------------------------------------------------
# IMAGES_IN_USE (alerts.py): scoped to processes this test creates and
# deletes, so no foreign-row guard is needed (unlike the sweeps above).
# ---------------------------------------------------------------------------


async def test_images_in_use_finds_a_flagged_image_on_a_live_enabled_process(conn):
    from pipeline.images.alerts import PgImageAlertsRepo

    reference = f"ghcr.io/itest/inuse-{uuid.uuid4().hex[:12]}"
    process_name = f"itest-process-{uuid.uuid4().hex[:12]}"
    try:
        image_id = await _insert_image(
            conn,
            reference=reference,
            status="flagged",
            verdict={"pass": False, "reasons": ["kev:CVE-2026-0001"]},
            last_scanned_at=dt.datetime.now(dt.UTC),
        )
        cur = await conn.execute(INSERT_PROCESS_SQL, (process_name, True, None))
        (process_id,) = await cur.fetchone()
        from psycopg.types.json import Json

        image_runtime = Json(
            {"image": {"id": image_id, "reference": reference, "digest": DIGEST}}
        )
        cur = await conn.execute(INSERT_REVISION_SQL, (process_id, image_runtime))
        (revision_id,) = await cur.fetchone()
        await conn.execute(
            "UPDATE stac_higher.processes SET current_revision = %s::uuid WHERE id = %s::uuid",
            (revision_id, process_id),
        )

        repo = PgImageAlertsRepo(DATABASE_URL)
        rows = await repo.list_images_in_use()
        (row,) = [r for r in rows if r.process_id == process_id]
        assert row.image_id == image_id
        assert row.status == "flagged"
        assert row.snapshot_reference == reference
    finally:
        # processes.current_revision and process_revisions.process_id are
        # both ON DELETE RESTRICT, so the process must be un-pointed and its
        # revision(s) removed before the process row itself can go.
        await conn.execute(
            "UPDATE stac_higher.processes SET current_revision = NULL WHERE name = %s",
            (process_name,),
        )
        await conn.execute(
            "DELETE FROM stac_higher.process_revisions WHERE process_id IN"
            " (SELECT id FROM stac_higher.processes WHERE name = %s)",
            (process_name,),
        )
        await conn.execute("DELETE FROM stac_higher.processes WHERE name = %s", (process_name,))
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )


async def test_images_in_use_ignores_a_disabled_or_a_deleted_process(conn):
    from psycopg.types.json import Json

    from pipeline.images.alerts import PgImageAlertsRepo

    reference = f"ghcr.io/itest/inuse-skip-{uuid.uuid4().hex[:12]}"
    disabled_name = f"itest-disabled-{uuid.uuid4().hex[:12]}"
    deleted_name = f"itest-deleted-{uuid.uuid4().hex[:12]}"
    try:
        image_id = await _insert_image(conn, reference=reference, status="flagged")
        runtime = Json({"image": {"id": image_id, "reference": reference, "digest": DIGEST}})

        cur = await conn.execute(INSERT_PROCESS_SQL, (disabled_name, False, None))
        (disabled_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_REVISION_SQL, (disabled_id, runtime))
        (disabled_rev,) = await cur.fetchone()
        await conn.execute(
            "UPDATE stac_higher.processes SET current_revision = %s::uuid WHERE id = %s::uuid",
            (disabled_rev, disabled_id),
        )

        cur = await conn.execute(
            INSERT_PROCESS_SQL, (deleted_name, True, dt.datetime.now(dt.UTC))
        )
        (deleted_id,) = await cur.fetchone()
        cur = await conn.execute(INSERT_REVISION_SQL, (deleted_id, runtime))
        (deleted_rev,) = await cur.fetchone()
        await conn.execute(
            "UPDATE stac_higher.processes SET current_revision = %s::uuid WHERE id = %s::uuid",
            (deleted_rev, deleted_id),
        )

        repo = PgImageAlertsRepo(DATABASE_URL)
        rows = await repo.list_images_in_use()
        assert not any(r.process_id in (disabled_id, deleted_id) for r in rows)
    finally:
        await conn.execute(
            "UPDATE stac_higher.processes SET current_revision = NULL WHERE name IN (%s, %s)",
            (disabled_name, deleted_name),
        )
        await conn.execute(
            "DELETE FROM stac_higher.process_revisions WHERE process_id IN"
            " (SELECT id FROM stac_higher.processes WHERE name IN (%s, %s))",
            (disabled_name, deleted_name),
        )
        await conn.execute(
            "DELETE FROM stac_higher.processes WHERE name IN (%s, %s)",
            (disabled_name, deleted_name),
        )
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )
