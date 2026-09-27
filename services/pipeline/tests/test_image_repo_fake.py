"""The behavioural contract of the images repository, pinned on the fake
(the Pg SQL is `# pragma: no cover` by convention and exercised by the
DB-gated test_integration_images_repo.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from _images_fake import FakeImagesRepo
from pipeline.images.scan_result import (
    ERROR_MAX_CHARS,
    SEVERITIES,
    ScanResult,
    failure_result,
    parse_scan_result,
    scan_result_to_json,
)

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
EXISTING = "9f8e7d6c-5b4a-3c2d-1e0f-0a1b2c3d4e5f"


def repo_with(status="pending", **image):
    repo = FakeImagesRepo()
    repo.add_image(
        id=IMG, reference="docker.io/library/python", tag_at_add="3.12-slim", status=status, **image
    )
    return repo


def admitted_result(**overrides):
    fields = dict(
        kind="admission",
        reference="docker.io/library/python",
        tag="3.12-slim",
        error=None,
        digest="sha256:" + "b" * 64,
    )
    fields.update(overrides)
    return ScanResult(**fields)


@pytest.mark.asyncio
async def test_claim_takes_the_oldest_pending_and_marks_an_admission_scanning():
    repo = repo_with()
    repo.add_scan("s2", IMG, requested_at=NOW)
    repo.add_scan("s1", IMG, requested_at=NOW - dt.timedelta(minutes=1))
    claimed = await repo.claim_pending_scan(max_running=1)
    assert claimed is not None and claimed.id == "s1"
    assert claimed.image.status == "scanning"
    assert repo.scans["s1"]["status"] == "running"


@pytest.mark.asyncio
async def test_claim_takes_an_admission_ahead_of_an_older_pending_rescan():
    """Item 9: a batch of tick-requested rescans must not starve a user's
    "Add image" -- an admission claims first even when a rescan has been
    pending longer."""
    repo = repo_with()
    repo.add_scan("r1", IMG, kind="rescan", requested_at=NOW - dt.timedelta(hours=1))
    repo.add_scan("a1", IMG, kind="admission", requested_at=NOW)
    claimed = await repo.claim_pending_scan(max_running=1)
    assert claimed is not None and claimed.id == "a1"


def test_the_claim_sql_orders_admissions_before_rescans_at_equal_age():
    from pipeline.images.repo import CLAIM_PENDING_SCAN_SQL

    assert "ORDER BY (kind = 'rescan'), requested_at" in CLAIM_PENDING_SCAN_SQL


@pytest.mark.asyncio
async def test_claim_respects_the_deployment_wide_cap():
    repo = repo_with()
    repo.add_scan("s0", IMG, status="running", started_at=NOW)
    repo.add_scan("s1", IMG)
    assert await repo.claim_pending_scan(max_running=1) is None
    assert (await repo.claim_pending_scan(max_running=2)).id == "s1"


@pytest.mark.asyncio
async def test_a_rescan_claim_leaves_the_visible_status_alone():
    repo = repo_with(status="approved", digest="sha256:" + "a" * 64)
    repo.add_scan("s1", IMG, kind="rescan")
    claimed = await repo.claim_pending_scan(max_running=1)
    assert claimed.image.status == "approved"


@pytest.mark.asyncio
async def test_stalled_scans_fail_with_an_identity_bearing_result():
    repo = repo_with(status="scanning")
    repo.add_scan("s1", IMG, status="running", started_at=NOW - dt.timedelta(hours=2))
    repo.add_scan("s2", IMG, status="running", started_at=NOW)
    assert await repo.fail_stalled_scans(started_before=NOW - dt.timedelta(hours=1)) == 1
    assert repo.scans["s1"]["status"] == "failed"
    assert repo.scans["s2"]["status"] == "running"
    # The C-1 readers accept what the sweep writes (Decision 1).
    parsed = parse_scan_result(repo.scans["s1"]["result"])
    assert parsed.error and parsed.reference == "docker.io/library/python"
    assert repo.images[IMG].status == "scan_failed"


def test_failure_result_is_the_c1_shape_and_is_bounded():
    doc = failure_result("admission", "ghcr.io/example/tool", "1.0", "x" * 5000)
    assert set(doc) == {"version", "kind", "reference", "tag", "error"}
    assert len(doc["error"]) == ERROR_MAX_CHARS
    assert parse_scan_result(doc).error is not None
    assert failure_result("rescan", "ghcr.io/example/tool", "1.0", "   ")["error"] == "scan failed"


def test_the_canonical_result_round_trips_the_fixture_document():
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
        .read_text()
    )
    doc = {**fixture["document"], "unknown_key": [1, 2]}
    parsed = parse_scan_result(doc)
    canonical = scan_result_to_json(parsed)
    assert "unknown_key" not in canonical
    assert parse_scan_result(canonical) == parsed
    assert canonical["top"][0]["id"] == "CVE-2026-1234"


def test_tag_drift_is_rebuilt_to_exactly_current_digest_and_drifted():
    """M2: the scanner's tag_drift is untrusted like the rest of its document;
    the stored copy carries only {current_digest, drifted}, never whatever
    other keys the scanner put next to them."""
    doc = {
        "version": 1,
        "kind": "rescan",
        "reference": "ghcr.io/example/tool",
        "tag": "1.0",
        "digest": "sha256:" + "a" * 64,
        "platform_digest": "sha256:" + "a" * 64,
        "platform": {"os": "linux", "architecture": "amd64"},
        "size_bytes": 1,
        "config": {"user": "", "entrypoint": None, "cmd": None},
        "scanner": {"syft": "1", "grype": "1", "db_built_at": "2026-09-27T00:00:00Z"},
        "sbom_ref": "s",
        "findings_ref": "f",
        "counts": dict.fromkeys(SEVERITIES, 0),
        "fixed_counts": dict.fromkeys(SEVERITIES, 0),
        "kev": [],
        "max_risk": 0.0,
        "top": [],
        "tag_drift": {
            "current_digest": "sha256:" + "b" * 64,
            "drifted": True,
            "unexpected": "the scanner should not be able to add this",
        },
        "error": None,
    }
    parsed = parse_scan_result(doc)
    canonical = scan_result_to_json(parsed)
    assert canonical["tag_drift"] == {"current_digest": "sha256:" + "b" * 64, "drifted": True}


# ---------------------------------------------------------------------------
# B1 (controller ruling): record_admission/record_rescan/merge_admission are
# compare-and-set on the image's status AND exception expiry read at claim
# time, and never touch a revoked row even if the caller (wrongly) expects
# "revoked".
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_admission_with_a_stale_expected_status_changes_nothing():
    repo = repo_with(status="approved")
    changed = await repo.record_admission(
        IMG,
        scan_id="s1",
        result=admitted_result(),
        verdict={"pass": True},
        status="approved",
        expected_status="pending",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.images[IMG].status == "approved"
    assert repo.images[IMG].digest is None
    assert IMG not in repo.verdicts
    assert IMG not in repo.last_scan_ids


@pytest.mark.asyncio
async def test_record_admission_never_changes_a_revoked_row():
    repo = repo_with(status="revoked")
    changed = await repo.record_admission(
        IMG,
        scan_id="s1",
        result=admitted_result(),
        verdict={"pass": True},
        status="approved",
        expected_status="revoked",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.images[IMG].status == "revoked"
    assert repo.images[IMG].digest is None


@pytest.mark.asyncio
async def test_an_exception_granted_between_claim_and_write_is_not_overwritten():
    repo = repo_with(status="flagged")
    # The drain claimed the scan while the image read "flagged". Before it
    # writes, an admin grants an exception: flagged -> approved, exception
    # columns set. The stale "flagged" expectation must not clobber this.
    exception_expires_at = NOW + dt.timedelta(days=30)
    repo._set(IMG, status="approved", exception_expires_at=exception_expires_at)
    changed = await repo.record_admission(
        IMG,
        scan_id="s1",
        result=admitted_result(),
        verdict={"pass": False},
        status="rejected",
        expected_status="flagged",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.images[IMG].status == "approved"
    assert repo.images[IMG].exception_expires_at == exception_expires_at
    assert IMG not in repo.verdicts


@pytest.mark.asyncio
async def test_an_exception_expiry_that_changed_between_claim_and_write_is_a_noop():
    """B1 ruling 2: the CAS also keys on the exception expiry, not only the
    status. An admin can extend, shorten or clear an exception without the
    status itself moving, and a stale scan result must not overwrite that
    either."""
    exception_expires_at = NOW + dt.timedelta(days=10)
    repo = repo_with(status="approved", exception_expires_at=exception_expires_at)
    # An admin extends the exception between claim and write. The status is
    # unchanged (still "approved"), but the expiry the drain read at claim
    # time no longer matches.
    extended = NOW + dt.timedelta(days=40)
    repo._set(IMG, exception_expires_at=extended)
    changed = await repo.record_admission(
        IMG,
        scan_id="s1",
        result=admitted_result(),
        verdict={"pass": True},
        status="approved",
        expected_status="approved",
        expected_exception_expires_at=exception_expires_at,
        at=NOW,
    )
    assert changed is False
    assert repo.images[IMG].exception_expires_at == extended
    assert IMG not in repo.verdicts


@pytest.mark.asyncio
async def test_record_rescan_with_a_stale_expected_status_changes_nothing():
    repo = repo_with(status="approved")
    changed = await repo.record_rescan(
        IMG,
        scan_id="s1",
        verdict={"pass": True},
        status="approved",
        expected_status="flagged",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert IMG not in repo.verdicts
    assert IMG not in repo.last_scan_ids


@pytest.mark.asyncio
async def test_record_rescan_never_changes_a_revoked_row():
    repo = repo_with(status="revoked")
    changed = await repo.record_rescan(
        IMG,
        scan_id="s1",
        verdict={"pass": True},
        status="approved",
        expected_status="revoked",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.images[IMG].status == "revoked"


@pytest.mark.asyncio
async def test_merge_admission_with_a_stale_expected_status_on_the_existing_row_is_a_noop():
    repo = repo_with(status="pending")
    repo.add_image(
        id=EXISTING,
        reference="docker.io/library/python",
        tag_at_add="3.12-slim",
        status="approved",
        digest="sha256:" + "d" * 64,
    )
    repo.add_scan("s1", IMG)
    changed = await repo.merge_admission(
        provisional_id=IMG,
        existing_id=EXISTING,
        scan_id="s1",
        verdict={"pass": True},
        status="approved",
        expected_status="flagged",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.scans["s1"]["image_id"] == IMG
    assert IMG in repo.images
    assert EXISTING not in repo.verdicts
    assert EXISTING not in repo.deleted_images


@pytest.mark.asyncio
async def test_merge_admission_never_changes_a_revoked_existing_row():
    repo = repo_with(status="pending")
    repo.add_image(
        id=EXISTING,
        reference="docker.io/library/python",
        tag_at_add="3.12-slim",
        status="revoked",
    )
    repo.add_scan("s1", IMG)
    changed = await repo.merge_admission(
        provisional_id=IMG,
        existing_id=EXISTING,
        scan_id="s1",
        verdict={"pass": True},
        status="approved",
        expected_status="revoked",
        expected_exception_expires_at=None,
        at=NOW,
    )
    assert changed is False
    assert repo.images[EXISTING].status == "revoked"
    assert repo.scans["s1"]["image_id"] == IMG
    assert IMG in repo.images
