"""The scan drain (container-images spec §8.1, §4.3, §9.1 dedup).

The scanner is untrusted: its result is believed only when it parses, names
this scan's image and prefix, and matches what the drain asked for. Every
other outcome is a failed scan with an identity-bearing result, and the image
leaves `scanning`."""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from _images_fake import FakeImagesRepo
from pipeline.images.drain import check_identity, drain_one, effective_kind, next_image_status
from pipeline.images.policy import load_image_policy
from pipeline.images.repo import ClaimedScan, ImageRow
from pipeline.images.scan_launch import ScanRun
from pipeline.images.scan_result import parse_scan_result
from pipeline.process.executor import ExitStatus

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
OTHER = "99999999-8888-4777-8666-555555555555"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
OLD = "11111111-2222-4333-8444-555555555555"
DIGEST = "sha256:" + "a" * 64
REF = "docker.io/library/python"
PREFIX = f"scans/{IMG}/{SCAN}/"
POLICY = load_image_policy()
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]


def result_doc(**overrides) -> dict:
    doc = {
        **FIXTURE,
        "kind": "admission",
        "reference": REF,
        "tag": "3.12-slim",
        "digest": DIGEST,
        "platform_digest": DIGEST,
        "sbom_ref": f"{PREFIX}sbom.syft.json",
        "findings_ref": f"{PREFIX}findings.grype.json",
        "counts": {"critical": 0, "high": 0, "medium": 1, "low": 0, "negligible": 0, "unknown": 0},
        "fixed_counts": dict.fromkeys(
            ("critical", "high", "medium", "low", "negligible", "unknown"), 0
        ),
        "kev": [],
        "max_risk": 0.1,
        "top": [],
    }
    doc.update(overrides)
    return doc


def repo_with(status="pending", scan_kind="admission", **image) -> FakeImagesRepo:
    repo = FakeImagesRepo(clock=NOW)
    repo.add_image(id=IMG, reference=REF, tag_at_add="3.12-slim", status=status, **image)
    repo.add_scan(SCAN, IMG, kind=scan_kind)
    return repo


class Scanner:
    """A scripted scanner run plus the result.json it left behind."""

    def __init__(
        self, doc=None, *, status: ExitStatus | None = None, error: Exception | None = None
    ):
        self.doc = doc
        self.status = status or ExitStatus(0)
        self.error = error
        self.kinds: list[str] = []
        self.read_keys: list[str] = []

    async def run_scan(self, scan, kind):
        self.kinds.append(kind)
        if self.error is not None:
            raise self.error
        return ScanRun(status=self.status, handle_id="c1", log_ref=f"{PREFIX}log")

    async def read_result(self, key):
        self.read_keys.append(key)
        return self.doc


async def drain(repo, scanner):
    return await drain_one(
        repo,
        policy=POLICY,
        max_running=1,
        run_scan=scanner.run_scan,
        read_result=scanner.read_result,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("current", "passed", "exception_live", "expected"),
    [
        ("scanning", True, False, "approved"),
        ("scanning", False, False, "rejected"),
        ("scan_failed", True, False, "approved"),
        ("rejected", True, False, "approved"),
        ("rejected", False, False, "rejected"),
        ("approved", False, False, "flagged"),
        ("approved", False, True, "approved"),
        ("flagged", True, False, "approved"),
        ("flagged", False, False, "flagged"),
        ("revoked", True, False, "revoked"),
    ],
)
def test_next_image_status(current, passed, exception_live, expected):
    assert next_image_status(current, passed=passed, exception_live=exception_live) == expected


@pytest.mark.asyncio
async def test_nothing_pending_launches_nothing():
    repo = FakeImagesRepo(clock=NOW)
    scanner = Scanner()
    assert await drain(repo, scanner) is None
    assert scanner.kinds == []


@pytest.mark.asyncio
async def test_a_passing_admission_approves_and_fills_the_row():
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc()))
    assert (outcome.scan_status, outcome.image_status) == ("done", "approved")
    image = repo.images[IMG]
    assert (image.status, image.digest, image.size_bytes) == ("approved", DIGEST, 812345678)
    assert image.sbom_ref == f"{PREFIX}sbom.syft.json"
    assert image.last_scanned_at == NOW
    assert repo.last_scan_ids[IMG] == SCAN
    scan = repo.scans[SCAN]
    assert scan["status"] == "done" and scan["executor_handle"] == "c1"
    assert scan["log_ref"] == f"{PREFIX}log"
    assert scan["findings_ref"] == f"{PREFIX}findings.grype.json"
    assert scan["result"]["verdict"]["pass"] is True and scan["result"]["diff"] is None
    assert parse_scan_result(scan["result"]).digest == DIGEST


@pytest.mark.asyncio
async def test_a_failing_admission_is_rejected_with_reasons():
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc(kev=["CVE-2026-0001"])))
    assert outcome.image_status == "rejected"
    assert repo.verdicts[IMG]["reasons"] == ["kev:CVE-2026-0001"]


@pytest.mark.asyncio
async def test_no_result_json_still_fails_the_scan_with_a_readable_result():
    """Review Focus 3: a crash, an OOM kill or a timeout leaves nothing behind."""
    repo = repo_with()
    outcome = await drain(repo, Scanner(None, status=ExitStatus(137, timed_out=True)))
    assert outcome.scan_status == "failed"
    scan = repo.scans[SCAN]
    parsed = parse_scan_result(scan["result"])
    assert "timed out after 900 s" in parsed.error
    assert (parsed.reference, parsed.tag, parsed.kind) == (REF, "3.12-slim", "admission")
    assert scan["log_ref"] == f"{PREFIX}log"
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
async def test_an_error_result_fails_the_scan_with_the_scanners_message():
    repo = repo_with()
    doc = {"version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
           "error": "image_too_large: 9 bytes of layers exceed the policy's 8"}
    await drain(repo, Scanner(doc, status=ExitStatus(1)))
    assert parse_scan_result(repo.scans[SCAN]["result"]).error.startswith("image_too_large")
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lie",
    [
        {"reference": "docker.io/library/alpine"},
        {"tag": "latest"},
        {"sbom_ref": f"scans/{IMG}/{OLD}/sbom.syft.json"},
        {"findings_ref": "assets/c/i/findings.json"},
        {"kind": "rescan"},
        {"platform": {"os": "linux", "architecture": "arm64"}},
        {"max_risk": float("nan")},
    ],
    ids=["reference", "tag", "sbom", "findings", "kind", "platform", "nan"],
)
async def test_a_scanner_that_lies_is_not_believed(lie):
    """Review Focus 2."""
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc(**lie)))
    assert outcome.scan_status == "failed"
    assert repo.images[IMG].status == "scan_failed"
    assert repo.images[IMG].digest is None


@pytest.mark.asyncio
async def test_a_result_with_a_failed_exit_is_not_believed():
    repo = repo_with()
    await drain(repo, Scanner(result_doc(), status=ExitStatus(1)))
    assert repo.scans[SCAN]["status"] == "failed"


@pytest.mark.asyncio
async def test_a_scan_that_cannot_start_fails_without_a_result_read():
    repo = repo_with()
    scanner = Scanner(error=RuntimeError("docker unreachable"))
    outcome = await drain(repo, scanner)
    assert outcome.scan_status == "failed"
    assert "could not start" in outcome.error and scanner.read_keys == []


@pytest.mark.asyncio
async def test_a_revoked_image_is_never_scanned():
    repo = repo_with(status="revoked")
    scanner = Scanner(result_doc())
    await drain(repo, scanner)
    assert scanner.kinds == []
    assert repo.scans[SCAN]["status"] == "failed"
    assert repo.images[IMG].status == "revoked"


@pytest.mark.asyncio
async def test_a_second_add_of_the_same_digest_folds_into_the_existing_row():
    """Spec §9.1: dedup by digest happens in the drain."""
    repo = repo_with()
    repo.add_image(
        id=OTHER,
        reference=REF,
        tag_at_add="3.12",
        status="approved",
        digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.image_id == OTHER and outcome.image_status == "approved"
    assert IMG in repo.deleted_images and IMG not in repo.images
    assert repo.scans[SCAN]["image_id"] == OTHER
    # The existing row keeps its own SBOM (Decision 8).
    assert repo.images[OTHER].sbom_ref == f"scans/{OTHER}/{OLD}/sbom.syft.json"
    assert repo.last_scan_ids[OTHER] == SCAN


@pytest.mark.asyncio
async def test_a_failing_rescan_flags_an_approved_image():
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    doc = result_doc(
        kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", kev=["CVE-2026-0001"]
    )
    scanner = Scanner(doc)
    outcome = await drain(repo, scanner)
    assert scanner.kinds == ["rescan"]
    assert outcome.image_status == "flagged"
    # A rescan never rewrites the admission fields.
    assert repo.images[IMG].sbom_ref == f"scans/{IMG}/{OLD}/sbom.syft.json"


@pytest.mark.asyncio
async def test_a_live_exception_keeps_a_failing_rescan_approved():
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
        exception_expires_at=NOW + dt.timedelta(days=5),
    )
    doc = result_doc(
        kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", kev=["CVE-2026-0001"]
    )
    assert (await drain(repo, Scanner(doc))).image_status == "approved"


@pytest.mark.asyncio
async def test_a_rescan_of_an_image_without_an_sbom_runs_as_an_admission():
    repo = repo_with(status="scan_failed", scan_kind="rescan")
    scanner = Scanner(result_doc())
    outcome = await drain(repo, scanner)
    assert scanner.kinds == ["admission"]
    assert outcome.image_status == "approved"


def test_effective_kind_needs_a_stored_sbom_and_a_digest():
    base = ImageRow(id=IMG, reference=REF, tag_at_add="3.12-slim", status="approved")
    assert effective_kind(ClaimedScan("s", "rescan", "u", base)) == "admission"
    full = replace(base, digest=DIGEST, sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json")
    assert effective_kind(ClaimedScan("s", "rescan", "u", full)) == "rescan"
    assert effective_kind(ClaimedScan("s", "admission", "u", full)) == "admission"


# ---------------------------------------------------------------------------
# Controller rulings carried into this slice (each gets its own test).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s1_a_disallowed_registry_fails_without_launching_or_resolving_credentials():
    repo = FakeImagesRepo(clock=NOW)
    repo.add_image(
        id=IMG, reference="evil.example.com/library/python", tag_at_add="3.12-slim",
        status="pending",
    )
    repo.add_scan(SCAN, IMG, kind="admission")
    scanner = Scanner(result_doc())
    outcome = await drain(repo, scanner)
    assert outcome.scan_status == "failed"
    assert scanner.kinds == [] and scanner.read_keys == []
    parsed = parse_scan_result(repo.scans[SCAN]["result"])
    assert parsed.error == "registry_not_allowed"
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
async def test_s2_a_digest_match_under_a_different_registry_connection_is_not_merged():
    repo = repo_with(registry_connection_id="conn-a")
    repo.add_image(
        id=OTHER,
        reference=REF,
        tag_at_add="3.12",
        status="approved",
        digest=DIGEST,
        registry_connection_id="conn-b",
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "failed" and outcome.image_id == IMG
    assert repo.images[IMG].status == "scan_failed" and repo.images[IMG].digest is None
    # The existing row is untouched -- no merge happened.
    assert repo.images[OTHER].status == "approved"
    assert repo.scans[SCAN]["image_id"] == IMG


@pytest.mark.asyncio
async def test_s2_none_registry_connection_id_on_both_rows_still_merges():
    repo = repo_with()
    repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="approved", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.image_id == OTHER and outcome.image_status == "approved"


@pytest.mark.asyncio
async def test_s2_a_merge_that_cannot_land_resolves_the_provisional_scan_as_failed():
    repo = repo_with()
    existing = repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="approved", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )

    real_merge_admission = repo.merge_admission

    async def merge_admission_always_misses(**kwargs):
        # Simulate the existing row changing between claim and write on
        # every attempt -- the compare-and-set can never land.
        kwargs["expected_status"] = "some-other-status-entirely"
        return await real_merge_admission(**kwargs)

    repo.merge_admission = merge_admission_always_misses
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "failed"
    # The provisional row must not be left stuck in `scanning`.
    assert repo.images[IMG].status == "scan_failed"
    # The existing row is untouched and NOT deleted.
    assert repo.images[OTHER] == existing
    assert IMG in repo.images


@pytest.mark.asyncio
async def test_m3_a_concurrent_same_digest_admission_falls_back_to_merge():
    repo = repo_with()
    existing = repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="approved", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )

    calls = {"n": 0}

    async def record_admission_races(*args, **kwargs):
        # find_image_by_digest saw nothing (racing scan not committed yet),
        # but by the time we write, the unique index has been hit.
        calls["n"] += 1
        raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")

    real_find = repo.find_image_by_digest
    find_calls = {"n": 0}

    async def find_image_by_digest_first_miss(*args, **kwargs):
        find_calls["n"] += 1
        if find_calls["n"] == 1:
            return None
        return await real_find(*args, **kwargs)

    repo.record_admission = record_admission_races
    repo.find_image_by_digest = find_image_by_digest_first_miss
    outcome = await drain(repo, Scanner(result_doc()))
    assert calls["n"] == 1
    assert outcome.image_id == OTHER and outcome.image_status == "approved"
    assert IMG in repo.deleted_images
    assert repo.images[OTHER].id == existing.id


@pytest.mark.asyncio
async def test_b1_a_revoked_image_mid_scan_stays_revoked():
    """The image is revoked WHILE the scanner runs -- the write must never
    clobber it, and the retry (which also sees `revoked`) must not either."""
    repo = repo_with(status="pending")

    class RevokingScanner(Scanner):
        async def run_scan(self, scan, kind):
            repo._set(IMG, status="revoked")
            return await super().run_scan(scan, kind)

    outcome = await drain(repo, RevokingScanner(result_doc()))
    assert outcome.scan_status == "done"
    assert outcome.image_status is None
    assert repo.images[IMG].status == "revoked"
    assert IMG not in repo.verdicts


@pytest.mark.asyncio
async def test_b1_an_exception_granted_mid_rescan_is_kept():
    """An admin grants an exception WHILE a failing rescan runs -- the
    compare-and-set misses on the stale (no-exception) expiry, re-reads, and
    the retry lands with the exception honoured."""
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    expiry = NOW + dt.timedelta(days=5)

    class ExceptionGrantingScanner(Scanner):
        async def run_scan(self, scan, kind):
            repo._set(IMG, exception_expires_at=expiry)
            return await super().run_scan(scan, kind)

    doc = result_doc(
        kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", kev=["CVE-2026-0001"]
    )
    outcome = await drain(repo, ExceptionGrantingScanner(doc))
    assert outcome.image_status == "approved"
    assert repo.images[IMG].status == "approved"
    assert repo.images[IMG].exception_expires_at == expiry


@pytest.mark.asyncio
async def test_m4_the_scanners_untrusted_text_never_reaches_the_pipelines_own_log(caplog):
    repo = repo_with()
    marker = "SECRET_UNTRUSTED_MARKER_should_never_be_logged"
    doc = {"version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
           "error": marker}
    with caplog.at_level(logging.WARNING, logger="pipeline.images.drain"):
        await drain(repo, Scanner(doc, status=ExitStatus(1)))
    # The message is stored (the dashboard needs it)...
    assert marker in repo.scans[SCAN]["result"]["error"]
    # ...but never appears in the pipeline's own log records.
    for record in caplog.records:
        assert marker not in record.getMessage()
        assert marker not in str(record.__dict__)


# ---------------------------------------------------------------------------
# Task 7 fix round 1.
# ---------------------------------------------------------------------------


def test_check_identity_rescan_digest_mismatch_is_not_believed():
    """Item 4: the rescan branch of check_identity, digest leg."""
    image = ImageRow(
        id=IMG, reference=REF, tag_at_add="3.12-slim", status="approved",
        digest=DIGEST, sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    doc = result_doc(
        kind="rescan", sbom_ref=image.sbom_ref,
        digest="sha256:" + "b" * 64, platform_digest="sha256:" + "b" * 64,
    )
    result = parse_scan_result(doc)
    assert (
        check_identity(result, image, kind="rescan", prefix=PREFIX, policy=POLICY)
        == "the rescan reported a digest other than the row's"
    )


def test_check_identity_rescan_sbom_ref_mismatch_is_not_believed():
    """Item 4: the rescan branch of check_identity, sbom_ref leg -- a key
    that is otherwise well-formed (this scan's own prefix) but is not the
    STORED key must still be refused."""
    image = ImageRow(
        id=IMG, reference=REF, tag_at_add="3.12-slim", status="approved",
        digest=DIGEST, sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    doc = result_doc(kind="rescan", sbom_ref=f"{PREFIX}sbom.syft.json", digest=DIGEST)
    result = parse_scan_result(doc)
    assert (
        check_identity(result, image, kind="rescan", prefix=PREFIX, policy=POLICY)
        == "the rescan read an SBOM other than the stored one"
    )


@pytest.mark.asyncio
async def test_ruling2_a_passing_merge_must_not_raise_a_rejected_existing_row():
    """A digest merge may only keep or worsen the existing row's status,
    never raise it."""
    repo = repo_with()
    repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="rejected", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "failed" and outcome.image_id == IMG
    # The existing row is left COMPLETELY untouched.
    assert repo.images[OTHER].status == "rejected"
    assert OTHER not in repo.verdicts
    assert repo.scans[SCAN]["image_id"] == IMG
    # The provisional row is resolved out of `scanning`.
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
async def test_ruling2_a_passing_merge_must_not_raise_a_flagged_existing_row():
    repo = repo_with()
    repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="flagged", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "failed"
    assert repo.images[OTHER].status == "flagged"
    assert OTHER not in repo.verdicts


@pytest.mark.asyncio
async def test_ruling2_a_worsening_or_same_merge_still_lands():
    """The cap only blocks an IMPROVEMENT; a same-or-worse result still
    merges normally (unaffected by the ruling)."""
    repo = repo_with()
    repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="approved", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc(kev=["CVE-2026-0001"])))
    assert outcome.image_id == OTHER and outcome.image_status == "flagged"
    assert repo.images[OTHER].status == "flagged"


@pytest.mark.asyncio
async def test_ruling2_a_revoked_existing_row_gets_a_clear_reason():
    repo = repo_with()
    repo.add_image(
        id=OTHER, reference=REF, tag_at_add="3.12", status="revoked", digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "failed"
    parsed = parse_scan_result(repo.scans[SCAN]["result"])
    assert "is revoked" in parsed.error
    assert "changed while merging" not in parsed.error
    assert repo.images[OTHER].status == "revoked"


@pytest.mark.asyncio
async def test_fake_finish_scan_only_finishes_a_running_scan():
    """Item 3: mirrors the PgImagesRepo `AND status = 'running'` guard --
    seeded the same way as the DB integration test (fix round 2): the scan
    is 'running' before the first finish, so the first finish actually
    exercises the guard's PASS case, not just its refusal case."""
    repo = FakeImagesRepo(clock=NOW)
    repo.add_image(id=IMG, reference=REF, tag_at_add="3.12-slim", status="scanning")
    repo.add_scan(SCAN, IMG, status="running", started_at=NOW)

    first = await repo.finish_scan(
        SCAN,
        status="failed",
        result={
            "version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
            "error": "the scan stalled",
        },
        findings_ref=None,
        log_ref=None,
        executor_handle=None,
    )
    assert first is True

    second = await repo.finish_scan(
        SCAN, status="done", result={"version": 1}, findings_ref=None, log_ref=None,
        executor_handle="c1",
    )
    assert second is False
    assert repo.scans[SCAN]["status"] == "failed"
    assert repo.scans[SCAN]["result"]["error"] == "the scan stalled"


@pytest.mark.asyncio
async def test_ruling3_a_stall_swept_scan_is_not_overwritten_and_the_image_is_untouched():
    """A scan whose result arrives after `fail_stalled_scans` already failed
    it (e.g. a very slow read_result, a GC pause) must not have its scan row
    overwritten, and the image must not be transitioned on its behalf."""
    repo = repo_with(status="pending")

    class StallingScanner(Scanner):
        async def run_scan(self, scan, kind):
            repo.scans[SCAN]["status"] = "failed"
            repo.scans[SCAN]["result"] = {
                "version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
                "error": "the scan stalled",
            }
            repo._set(IMG, status="scan_failed")
            return await super().run_scan(scan, kind)

    outcome = await drain(repo, StallingScanner(result_doc()))
    assert outcome.scan_status == "already_resolved"
    assert outcome.image_status is None
    assert repo.images[IMG].status == "scan_failed"
    assert repo.scans[SCAN]["result"]["error"] == "the scan stalled"
    assert IMG not in repo.verdicts


@pytest.mark.asyncio
async def test_ruling3_a_stall_swept_scan_during_a_would_be_failure_is_not_overwritten():
    """The same guard on the `_fail` path: a scan that could not even start,
    but was concurrently stall-swept, must not reapply a status transition."""
    repo = repo_with(status="pending")

    class StallingFailureScanner(Scanner):
        async def run_scan(self, scan, kind):
            repo.scans[SCAN]["status"] = "failed"
            repo.scans[SCAN]["result"] = {
                "version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
                "error": "the scan stalled",
            }
            repo._set(IMG, status="scan_failed")
            raise RuntimeError("docker unreachable")

    outcome = await drain(repo, StallingFailureScanner())
    assert outcome.scan_status == "already_resolved"
    assert repo.scans[SCAN]["result"]["error"] == "the scan stalled"
    assert repo.images[IMG].status == "scan_failed"


# ---------------------------------------------------------------------------
# Final-review fix wave item 5: the terminal `finish_scan` write can itself
# race a concurrent stall sweep -- AFTER the `_still_running` pre-check
# passed but before this write lands (a narrower window than ruling 3's,
# which is the pre-check itself missing). The return value must not be
# discarded: a miss here must not be reported as "done" or "failed".
# ---------------------------------------------------------------------------


class _RaceOnFinish(FakeImagesRepo):
    """A stall sweep (or another worker) flips the scan to `failed` in the
    instant between the drain's `_still_running` pre-check and its own
    `finish_scan` write -- so `finish_scan`'s own `status = 'running'` guard
    (mirrored by the fake) is the thing that catches it, not the pre-check."""

    async def finish_scan(self, scan_id, **kwargs):
        self.scans[scan_id]["status"] = "failed"
        return await super().finish_scan(scan_id, **kwargs)


@pytest.mark.asyncio
async def test_a_finish_scan_race_on_the_success_path_is_reported_as_already_resolved(caplog):
    repo = _RaceOnFinish(clock=NOW)
    repo.add_image(id=IMG, reference=REF, tag_at_add="3.12-slim", status="pending")
    repo.add_scan(SCAN, IMG, kind="admission")
    with caplog.at_level(logging.WARNING, logger="pipeline.images.drain"):
        outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.scan_status == "already_resolved"
    assert outcome.image_status is None
    # The scan row is exactly what the race left it as -- not overwritten
    # to "done" by a write that lost the guard.
    assert repo.scans[SCAN]["status"] == "failed"
    assert repo.scans[SCAN]["result"] is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].scan_id == SCAN and warnings[0].image_id == IMG


@pytest.mark.asyncio
async def test_a_finish_scan_race_inside_fail_is_reported_as_already_resolved(caplog):
    repo = _RaceOnFinish(clock=NOW)
    repo.add_image(id=IMG, reference=REF, tag_at_add="3.12-slim", status="revoked")
    repo.add_scan(SCAN, IMG, kind="admission")
    with caplog.at_level(logging.WARNING, logger="pipeline.images.drain"):
        outcome = await drain(repo, Scanner())
    assert outcome.scan_status == "already_resolved"
    assert outcome.image_status is None
    assert repo.scans[SCAN]["status"] == "failed"
    # The race left no result on the row; `_fail`'s own write lost its guard
    # too, so it must not have landed either.
    assert repo.scans[SCAN]["result"] is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].scan_id == SCAN and warnings[0].image_id == IMG
