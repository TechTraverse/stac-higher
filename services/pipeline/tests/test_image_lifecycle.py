"""The hourly image tick (container-images spec §4.3, §4.4, §8.2): exception
expiry re-evaluates and is audited; rescans are requested as rows."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pipeline.images import lifecycle
from pipeline.images.lifecycle import (
    DUE_RESCANS_SQL,
    EXPIRE_EXCEPTION_SQL,
    EXPIRED_EXCEPTIONS_SQL,
    ExpiredException,
    ImageLifecycleRepo,
    decide_expiry,
    lifecycle_tick,
)
from pipeline.images.policy import load_image_policy

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
POLICY = load_image_policy()
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]
ZERO = dict.fromkeys(("critical", "high", "medium", "low", "negligible", "unknown"), 0)
PASSING = {**FIXTURE, "top": [], "kev": [], "fixed_counts": ZERO, "counts": ZERO}
FAILING = {**PASSING, "kev": ["CVE-2026-0001"]}


@dataclass
class Img:
    status: str
    exception_expires_at: dt.datetime | None = None
    exception_by: str | None = None
    last_scan_id: str | None = None
    verdict: dict[str, Any] | None = None
    sbom_ref: str | None = "scans/i/s/sbom.syft.json"
    digest: str | None = "sha256:" + "a" * 64
    last_scanned_at: dt.datetime | None = None
    open_scan: bool = False


@dataclass
class FakeLifecycleRepo(ImageLifecycleRepo):
    """The behavioural contract the three SQL constants must match."""

    images: dict[str, Img] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)
    #: Simulates an admin acting between the tick's read and its write.
    before_write: Any = None

    async def list_expired_exceptions(self, *, now):
        return [
            ExpiredException(
                image_id=i,
                status=img.status,
                exception_expires_at=img.exception_expires_at,
                exception_by=img.exception_by,
                last_scan_id=img.last_scan_id,
            )
            for i, img in self.images.items()
            if img.exception_expires_at is not None
            and img.exception_expires_at <= now
            and img.status != "revoked"
        ]

    async def get_scan_result(self, scan_id):
        return self.results.get(scan_id)

    async def expire_exception(
        self, image_id, *, expected_status, expected_expires_at, status, verdict, audit_detail
    ):
        if self.before_write is not None:
            self.before_write(self.images[image_id])
        img = self.images[image_id]
        if (
            img.status != expected_status
            or img.status == "revoked"
            or img.exception_expires_at != expected_expires_at
        ):
            return False
        img.status = status
        if verdict is not None:
            img.verdict = verdict
        img.exception_expires_at = None
        img.exception_by = None
        self.audit.append(
            {
                "actor": lifecycle.PIPELINE_ACTOR,
                "action": lifecycle.EXPIRY_AUDIT_ACTION,
                "resource_type": lifecycle.AUDIT_RESOURCE_TYPE,
                "resource_id": image_id,
                "detail": audit_detail,
            }
        )
        return True

    async def request_due_rescans(self, *, scanned_before):
        count = 0
        for i, img in self.images.items():
            if (
                img.status in ("approved", "flagged")
                and img.sbom_ref
                and img.digest
                and (img.last_scanned_at is None or img.last_scanned_at <= scanned_before)
                and not img.open_scan
            ):
                img.open_scan = True
                self.requested.append(i)
                count += 1
        return count


def expired(status="approved", **kw) -> Img:
    return Img(
        status=status,
        exception_expires_at=NOW - dt.timedelta(minutes=5),
        exception_by="admin-1",
        last_scan_id="scan-1",
        last_scanned_at=NOW - dt.timedelta(hours=1),
        **kw,
    )


async def tick(repo, policy=POLICY):
    return await lifecycle_tick(repo, policy=policy, now=NOW)


async def test_a_passing_re_evaluation_stays_approved_and_clears_the_exception():
    repo = FakeLifecycleRepo(images={"i1": expired()}, results={"scan-1": PASSING})
    result = await tick(repo)
    img = repo.images["i1"]
    assert (img.status, img.exception_expires_at, img.exception_by) == ("approved", None, None)
    assert img.verdict["pass"] is True
    assert result.exceptions_expired == 1 and result.flagged_on_expiry == 0
    (row,) = repo.audit
    assert (row["actor"], row["action"], row["resource_type"]) == (
        "pipeline", "exception_expired", "container_image"
    )
    assert row["detail"]["status"] == "approved" and row["detail"]["verdict_pass"] is True
    assert row["detail"]["previous_status"] == "approved"


async def test_a_failing_re_evaluation_flags_never_rejects():
    repo = FakeLifecycleRepo(images={"i1": expired()}, results={"scan-1": FAILING})
    result = await tick(repo)
    assert repo.images["i1"].status == "flagged"
    assert result.flagged_on_expiry == 1
    assert repo.audit[0]["detail"]["reasons"] == ["kev:CVE-2026-0001"]


async def test_the_current_policy_decides_not_the_stored_verdict():
    """A result that passed when scanned fails a tightened policy on expiry."""
    img = expired(verdict={"pass": True, "reasons": []})
    stricter = replace(POLICY, allowed_registries=("docker.io",))
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": PASSING})
    await tick(repo, policy=stricter)
    assert repo.images["i1"].status == "flagged"
    assert repo.images["i1"].verdict["reasons"] == ["registry_not_allowed"]


async def test_an_unreadable_latest_result_fails_closed():
    for results in ({}, {"scan-1": {"nonsense": True}}, {"scan-1": {**FAILING, "error": "x"}}):
        repo = FakeLifecycleRepo(images={"i1": expired()}, results=results)
        await tick(repo)
        assert repo.images["i1"].status == "flagged"
        assert repo.audit[0]["detail"]["verdict_pass"] is None


async def test_a_flagged_row_only_loses_its_expired_columns():
    kept = {"pass": False, "reasons": ["kev:CVE-2026-0009"]}
    repo = FakeLifecycleRepo(
        images={"i1": expired(status="flagged", verdict=kept)}, results={"scan-1": PASSING}
    )
    await tick(repo)
    img = repo.images["i1"]
    assert (img.status, img.verdict, img.exception_expires_at) == ("flagged", kept, None)
    assert repo.audit[0]["detail"]["status"] == "flagged"


async def test_an_admin_acting_between_the_ticks_read_and_write_wins():
    regranted = NOW + dt.timedelta(days=30)

    def regrant(img):
        img.exception_expires_at = regranted

    repo = FakeLifecycleRepo(
        images={"i1": expired()}, results={"scan-1": FAILING}, before_write=regrant
    )
    result = await tick(repo)
    assert repo.images["i1"].status == "approved"
    assert repo.images["i1"].exception_expires_at == regranted
    assert repo.audit == [] and result.exceptions_expired == 0


async def test_a_live_exception_is_left_alone():
    img = expired()
    img.exception_expires_at = NOW + dt.timedelta(seconds=1)
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": FAILING})
    await tick(repo)
    assert repo.images["i1"].status == "approved" and repo.audit == []


async def test_rescans_are_requested_once_for_due_approved_and_flagged_images():
    old = NOW - dt.timedelta(hours=POLICY.rescan_interval_hours)
    repo = FakeLifecycleRepo(
        images={
            "due-approved": Img("approved", last_scanned_at=old),
            "due-flagged": Img("flagged", last_scanned_at=old - dt.timedelta(days=3)),
            "fresh": Img("approved", last_scanned_at=old + dt.timedelta(minutes=1)),
            "open": Img("approved", last_scanned_at=old, open_scan=True),
            "rejected": Img("rejected", last_scanned_at=old),
            "no-sbom": Img("approved", last_scanned_at=old, sbom_ref=None),
        }
    )
    result = await tick(repo)
    assert sorted(repo.requested) == ["due-approved", "due-flagged"]
    assert result.rescans_requested == 2
    assert (await tick(repo)).rescans_requested == 0  # one open scan per image


async def test_expiry_runs_before_the_rescan_requests():
    """An image flagged by its expiry is still rescanned in the same tick."""
    img = expired()
    img.last_scanned_at = NOW - dt.timedelta(days=2)
    repo = FakeLifecycleRepo(images={"i1": img}, results={"scan-1": FAILING})
    await tick(repo)
    assert repo.requested == ["i1"] and repo.images["i1"].status == "flagged"


def test_decide_expiry_is_pure_and_names_the_expiry():
    row = ExpiredException("i1", "approved", NOW, "admin-1", "scan-1")
    decision = decide_expiry(row, PASSING, POLICY, NOW)
    assert decision.detail["expired_at"] == NOW.isoformat()
    assert decision.detail["exception_by"] == "admin-1"


def test_the_sql_keeps_the_boundaries_and_the_guards():
    """The Pg statements match the fake's contract (DB-gated SQL is not run
    in unit tests, so its load-bearing fragments are pinned here)."""
    assert "exception_expires_at <= %s" in EXPIRED_EXCEPTIONS_SQL
    assert "status <> 'revoked'" in EXPIRED_EXCEPTIONS_SQL
    for fragment in (
        "AND status = %s AND status <> 'revoked'",
        "AND exception_expires_at = %s",
        "exception_reason = NULL, exception_by = NULL",
        "exception_at = NULL, exception_expires_at = NULL",
        "INSERT INTO stac_higher.audit_log",
        "FROM changed",
    ):
        assert fragment in EXPIRE_EXCEPTION_SQL, fragment
    for fragment in (
        "i.status IN ('approved', 'flagged')",
        "i.sbom_ref IS NOT NULL AND i.digest IS NOT NULL",
        "i.last_scanned_at <= %s",
        "s.status IN ('pending', 'running')",
        "FOR UPDATE OF i SKIP LOCKED",
    ):
        assert fragment in DUE_RESCANS_SQL, fragment
