"""The scan drain's logic (C-2, container-images spec §8.1, §4.3, §9.1 dedup).

One claimed scan: launch the scanner (through an injected ``run_scan``), read
``result.json`` back (``read_result``), believe it only if it parses AND
names this scan's image, tag, kind, prefix and platform, evaluate it against
the policy, and transition the image. Everything else is a FAILED scan whose
result still carries the image's identity (the C-1 failure shape), and an
image in ``pending``/``scanning`` goes ``scan_failed``.

Transitions (spec §4.3, :func:`next_image_status`): a passing scan approves
(a passing scan auto-approves, spec decision 3, a ``rejected`` or
``scan_failed`` image included); a failing admission rejects; a failing
rescan flags an ``approved`` image unless a live exception covers it;
``revoked`` is terminal.

A rescan's stored result carries the diff against the image's previous scan
(C-4, ``pipeline/images/diff.py``) and the pipeline's own tag-drift HEAD
(``pipeline/images/drift.py``, injected as ``check_drift``); the tag columns
follow a HEAD that answered. An admission stores ``diff: null`` and never
HEADs. The rescan tick, alerts and exception expiry live beside the drain
(``lifecycle.py``, ``alerts.py``).

**Compare-and-set writes (controller ruling B1).** ``record_admission``,
``record_rescan`` and ``merge_admission`` only write when the row's status
and exception expiry still match what was read at claim time -- an admin can
revoke the image or grant/revoke an exception while the scanner runs, and
that decision must never be clobbered by a stale verdict. On a miss the
drain re-reads the row, recomputes the target status from the FRESH row, and
retries once. A revoked row is never written (the compare-and-set guard
itself refuses it); if the retry also misses, the image is left as-is, the
scan row is still finished, and only ids are logged -- never the scanner's
untrusted text (ruling M4).

**Digest folding (ruling S2, spec §9.1).** A second admission of an
already-registered ``(reference, digest)`` merges its verdict into the
EXISTING row instead of creating a duplicate -- but only when the two rows
share the same ``registry_connection_id`` (``None`` on both counts as a
match); otherwise the provisional scan fails outright, nothing is merged.
The compare-and-set for a merge is judged against the EXISTING row, never
the provisional one. If the merge's compare-and-set exhausts its retry, the
provisional row must not linger in ``scanning`` -- it resolves as a failed
scan. A merge may only KEEP OR WORSEN the existing row's status, never raise
it (Task 7 fix round 1): a rejected or flagged row stays that way even when
the folded-in verdict passes -- the row is left completely untouched rather
than partially updated. A revoked existing row gets its own clear reason
(``existing_image_revoked``), not the generic "changed while merging" one.

**Concurrent same-digest admissions (ruling M3).** ``find_image_by_digest``
is checked before ``record_admission``, but two scans can still race between
that read and the write; the database's ``UNIQUE (reference, digest)``
constraint is the backstop, surfaced as a unique-violation on the UPDATE.
That is caught and re-driven through the merge path.

**A scan already resolved by the stall sweep is left alone (Task 7 fix round
1).** ``finish_scan`` only writes a scan row still ``running``, and the
drain checks that BEFORE attempting any image write, not just at the final
``finish_scan`` call -- a scan whose result arrives after
``fail_stalled_scans`` already failed it must not overwrite that row NOR
apply a status transition to the image.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import psycopg

from pipeline.images.diff import scan_diff
from pipeline.images.policy import ImagePolicy, evaluate, registry_allowed
from pipeline.images.reference import registry_host
from pipeline.images.repo import ClaimedScan, ImageRow, ImagesRepo
from pipeline.images.scan_launch import ScanRun
from pipeline.images.scan_result import (
    ScanResult,
    ScanResultError,
    failure_result,
    parse_scan_result,
    scan_result_to_json,
)
from pipeline.storage.keys import image_scan_prefix

logger = logging.getLogger(__name__)

#: A scan still `running` this long past the policy timeout is presumed lost.
STALL_GRACE_SECONDS = 600

RunScan = Callable[[ClaimedScan, str], Awaitable[ScanRun]]
ReadResult = Callable[[str], Awaitable[dict[str, Any] | None]]
#: The pipeline's drift HEAD for a rescan (C-4, spec §8.2): the §6.4
#: ``tag_drift`` record, or None when no check ran.
CheckDrift = Callable[[ImageRow], Awaitable[dict[str, Any] | None]]

#: A trusted, caller-chosen classification for the log line -- never the
#: scanner's own (untrusted) message text (ruling M4).
REGISTRY_NOT_ALLOWED_REASON = "registry_not_allowed"

#: A scan's outcome when a stall sweep (or another worker) already resolved
#: it before this drain's own write landed (Task 7 fix round 1).
ALREADY_RESOLVED = "already_resolved"

#: Logged when the terminal `finish_scan` write itself misses its `status =
#: 'running'` guard -- a narrower race than ruling 3's (which the
#: `_still_running` pre-check catches): the row was still `running` at the
#: pre-check but a stall sweep (or another worker) resolved it in the
#: instant before this write landed (final-review fix wave item 5). Ids
#: only, never the scanner's own text (ruling M4).
FINISH_SCAN_RACED_MSG = "image scan finish raced: leaving the scan row and the image as-is"

#: Logged when the injected ``check_drift`` callable itself raises (lead
#: ruling F2) -- a narrower case than :func:`pipeline.images.drift.tag_drift`
#: catching its own failures, since ``check_drift`` also resolves the pull
#: credential. The scan still finishes; tag_drift is recorded as unchecked.
#: Never the exception's text, which may carry a credential -- the type name
#: only (ruling M4's rule, applied here too).
CHECK_DRIFT_FAILED_MSG = "image scan drift check failed: recording tag_drift as unchecked"

#: A merge may only keep or worsen the EXISTING row's status, never raise it
#: (Task 7 fix round 1 ruling): folding in a fresh, unrelated admission's
#: clean verdict must not un-reject or un-flag an image whose own history
#: earned that status. Higher = better.
_STATUS_RANK: dict[str, int] = {
    "rejected": 0,
    "scan_failed": 0,
    "pending": 0,
    "scanning": 0,
    "flagged": 1,
    "approved": 2,
}


class _ConcurrentDigest(Exception):
    """Internal signal: ``record_admission`` hit the ``(reference, digest)``
    unique constraint -- another scan admitted it first (ruling M3)."""


@dataclass(frozen=True)
class DrainOutcome:
    scan_id: str
    #: The authoritative image id (differs from the claimed one after a dedup).
    image_id: str
    scan_status: str
    image_status: str | None
    error: str | None = None


def next_image_status(current: str, *, passed: bool, exception_live: bool) -> str:
    if current == "revoked":
        return "revoked"
    if passed or exception_live:
        return "approved"
    if current in ("approved", "flagged"):
        return "flagged"
    return "rejected"


def exception_is_live(image: ImageRow, now: dt.datetime) -> bool:
    return image.exception_expires_at is not None and image.exception_expires_at > now


def effective_kind(scan: ClaimedScan) -> str:
    """A rescan needs a stored SBOM and digest; without them it runs as an
    admission (Decision 7), e.g. a re-request after ``scan_failed``."""
    image = scan.image
    if scan.kind == "rescan" and image.sbom_ref and image.digest:
        return "rescan"
    return "admission"


def check_identity(
    result: ScanResult, image: ImageRow, *, kind: str, prefix: str, policy: ImagePolicy
) -> str | None:
    """Why the result must not be believed, or None."""
    if result.kind != kind:
        return f"the scanner ran a {result.kind} scan; the drain asked for {kind}"
    if result.reference != image.reference:
        return f"the scanner reported {result.reference}; the row is {image.reference}"
    if result.tag != image.tag_at_add:
        return f"the scanner reported tag {result.tag!r}; the row has {image.tag_at_add!r}"
    if result.findings_ref != f"{prefix}findings.grype.json":
        return "findings_ref is outside this scan's prefix"
    if kind == "admission":
        if result.sbom_ref != f"{prefix}sbom.syft.json":
            return "sbom_ref is outside this scan's prefix"
        os_, arch = policy.platform.split("/")[:2]
        if result.platform != {"os": os_, "architecture": arch}:
            return f"the scanner scanned {result.platform}; the policy requires {policy.platform}"
        return None
    if result.sbom_ref != image.sbom_ref:
        return "the rescan read an SBOM other than the stored one"
    if result.digest != image.digest:
        return "the rescan reported a digest other than the row's"
    return None


async def _still_running(repo: ImagesRepo, scan_id: str) -> bool:
    """Whether the claimed scan is still ``running`` -- False when a stall
    sweep (or another worker) already resolved it. Checked BEFORE applying
    any image status transition (Task 7 fix round 1 ruling): a scan
    ``fail_stalled_scans`` already finished must not be overwritten, and the
    image must not be transitioned a second time on its behalf."""
    return (await repo.scan_statuses([scan_id])).get(scan_id) == "running"


async def _fail(
    repo: ImagesRepo,
    scan: ClaimedScan,
    kind: str,
    error: str,
    run: ScanRun | None,
    *,
    reason: str,
) -> DrainOutcome:
    image = scan.image
    if not await _still_running(repo, scan.id):
        logger.info(
            "image scan already resolved: leaving the scan row and the image as-is",
            extra={"scan_id": scan.id, "image_id": image.id},
        )
        return DrainOutcome(scan.id, image.id, ALREADY_RESOLVED, None, error)
    await repo.mark_image_scan_failed(image.id)
    finished = await repo.finish_scan(
        scan.id,
        status="failed",
        result=failure_result(kind, image.reference, image.tag_at_add, error),
        findings_ref=None,
        log_ref=run.log_ref if run else None,
        executor_handle=run.handle_id if run else None,
    )
    if not finished:
        # Item 5: the write itself missed its guard -- a narrower race than
        # the pre-check above catches. The scan row is whatever the
        # concurrent resolver left it as; this call must not be reported as
        # the "failed" outcome it asked for.
        logger.warning(
            FINISH_SCAN_RACED_MSG, extra={"scan_id": scan.id, "image_id": image.id}
        )
        return DrainOutcome(scan.id, image.id, ALREADY_RESOLVED, None, error)
    # M4: the classification is a fixed, caller-chosen code -- never the
    # scanner's own text, which `error` may carry.
    logger.warning(
        "image scan failed",
        extra={"scan_id": scan.id, "image_id": image.id, "kind": kind, "reason": reason},
    )
    return DrainOutcome(scan.id, image.id, "failed", None, error)


async def _cas_retry(
    repo: ImagesRepo,
    write: Callable[[str, str, dt.datetime | None], Awaitable[bool]],
    initial: ImageRow,
    *,
    passed: bool,
    now: dt.datetime,
    refresh_id: str,
) -> str | None:
    """One compare-and-set write, retried once against a fresh read on a miss
    (ruling B1). Returns the status that was written, or None when both
    attempts missed (or the row is gone) -- the caller must not treat that as
    a scan failure; the image is simply left as it now is."""
    status = next_image_status(
        initial.status, passed=passed, exception_live=exception_is_live(initial, now)
    )
    if await write(status, initial.status, initial.exception_expires_at):
        return status
    fresh = await repo.get_image(refresh_id)
    if fresh is None:
        return None
    status = next_image_status(
        fresh.status, passed=passed, exception_live=exception_is_live(fresh, now)
    )
    if await write(status, fresh.status, fresh.exception_expires_at):
        return status
    return None


def _merge_status(row: ImageRow, *, passed: bool, now: dt.datetime) -> str | None:
    """The status a merge may write onto ``row``, or None when the candidate
    would RAISE it above what it already has -- the merge must then not
    happen at all, and ``row`` is left completely untouched (Task 7 fix
    round 1 ruling)."""
    candidate = next_image_status(
        row.status, passed=passed, exception_live=exception_is_live(row, now)
    )
    if _STATUS_RANK.get(candidate, 0) > _STATUS_RANK.get(row.status, 0):
        return None
    return candidate


async def _merge_or_fail(
    repo: ImagesRepo,
    scan: ClaimedScan,
    kind: str,
    run: ScanRun,
    *,
    provisional: ImageRow,
    existing: ImageRow,
    scan_id: str,
    verdict_json: dict[str, Any],
    passed: bool,
    now: dt.datetime,
) -> DrainOutcome | tuple[str, str]:
    """Try to fold ``provisional`` into ``existing`` (spec §9.1, ruling S2).
    Returns ``(image_id, status)`` on success, or a terminal
    :class:`DrainOutcome` (a failed scan) when the merge must not happen or
    could not land."""
    if existing.registry_connection_id != provisional.registry_connection_id:
        return await _fail(
            repo,
            scan,
            kind,
            f"digest already registered on image {existing.id} under a different "
            "registry connection; refusing to merge",
            run,
            reason="registry_mismatch",
        )

    async def write(status: str, expected_status: str, expected_exp: dt.datetime | None) -> bool:
        return await repo.merge_admission(
            provisional_id=provisional.id,
            existing_id=existing.id,
            scan_id=scan_id,
            verdict=verdict_json,
            status=status,
            expected_status=expected_status,
            expected_exception_expires_at=expected_exp,
            at=now,
        )

    async def resolve(row: ImageRow) -> tuple[str, str] | DrainOutcome | None:
        """Try to write onto ``row``. A success tuple, a terminal
        :class:`DrainOutcome` when the merge must not happen at all, or None
        on a plain compare-and-set miss (the caller re-reads and retries
        once)."""
        if row.status == "revoked":
            return await _fail(
                repo,
                scan,
                kind,
                f"the matching image {row.id} is revoked; a duplicate admission of the "
                "same digest cannot be merged into it",
                run,
                reason="existing_image_revoked",
            )
        status = _merge_status(row, passed=passed, now=now)
        if status is None:
            return await _fail(
                repo,
                scan,
                kind,
                f"image {row.id} already has status {row.status!r}; a merge must not raise "
                "it, so this duplicate digest was not folded in",
                run,
                reason="merge_would_improve_status",
            )
        if await write(status, row.status, row.exception_expires_at):
            return row.id, status
        return None

    outcome = await resolve(existing)
    if outcome is not None:
        return outcome
    fresh = await repo.get_image(existing.id)
    if fresh is None:
        return await _fail(
            repo, scan, kind, f"image {existing.id} no longer exists", run,
            reason="merge_conflict",
        )
    outcome = await resolve(fresh)
    if outcome is not None:
        return outcome
    # S2: a merge that cannot land must not leave the provisional row stuck
    # in `scanning` -- resolve it as a failed scan instead.
    return await _fail(
        repo,
        scan,
        kind,
        f"image {existing.id} changed while merging this duplicate digest",
        run,
        reason="merge_conflict",
    )


async def drain_one(
    repo: ImagesRepo,
    *,
    policy: ImagePolicy,
    max_running: int,
    run_scan: RunScan,
    read_result: ReadResult,
    clock: Callable[[], dt.datetime],
    check_drift: CheckDrift | None = None,
) -> DrainOutcome | None:
    """Claim and process at most one scan. None when nothing was claimable."""
    scan = await repo.claim_pending_scan(max_running=max_running)
    if scan is None:
        return None
    image = scan.image
    kind = effective_kind(scan)
    if image.status == "revoked":
        return await _fail(
            repo, scan, kind, "the image is revoked; nothing is scanned", None,
            reason="revoked_image",
        )
    if kind == "admission" and not registry_allowed(
        registry_host(image.reference), policy.allowed_registries
    ):
        # S1: checked before launching the scanner or resolving a pull
        # credential -- a disallowed registry never gets that far.
        return await _fail(
            repo, scan, kind, REGISTRY_NOT_ALLOWED_REASON, None,
            reason=REGISTRY_NOT_ALLOWED_REASON,
        )

    try:
        run = await run_scan(scan, kind)
    except Exception as err:  # never started: fail the request, nothing retries silently
        return await _fail(
            repo, scan, kind, f"the scan could not start: {type(err).__name__}: {err}", None,
            reason="could_not_start",
        )

    prefix = image_scan_prefix(image.id, scan.id)
    try:
        doc = await read_result(f"{prefix}result.json")
    except ScanResultError as err:
        return await _fail(
            repo, scan, kind, f"scanner result rejected: {err}", run, reason="result_rejected"
        )
    if doc is None:
        if run.status.timed_out:
            reason_text = f"the scan timed out after {policy.scan_timeout_seconds} s"
        else:
            reason_text = f"the scanner exited {run.status.exit_code} without writing a result"
        if run.status.error:
            reason_text = f"{reason_text} ({run.status.error})"
        return await _fail(repo, scan, kind, reason_text, run, reason="no_result")
    try:
        result = parse_scan_result(doc)
    except ScanResultError as err:
        return await _fail(
            repo, scan, kind, f"scanner result rejected: {err}", run, reason="result_rejected"
        )
    if result.error is not None:
        return await _fail(repo, scan, kind, result.error, run, reason="scanner_error")
    if not run.status.ok:
        return await _fail(
            repo,
            scan,
            kind,
            f"the scanner exited {run.status.exit_code} after writing a result",
            run,
            reason="exit_nonzero",
        )
    mismatch = check_identity(result, image, kind=kind, prefix=prefix, policy=policy)
    if mismatch is not None:
        return await _fail(
            repo, scan, kind, f"scanner result rejected: {mismatch}", run,
            reason="identity_mismatch",
        )

    drift: dict[str, Any] | None = None
    if (
        kind == "rescan"
        and check_drift is not None
        and registry_allowed(registry_host(image.reference), policy.allowed_registries)
    ):
        try:
            drift = await check_drift(image)
        except Exception as err:  # F2: the injected check itself must never fail the scan
            logger.warning(
                CHECK_DRIFT_FAILED_MSG,
                extra={
                    "scan_id": scan.id,
                    "image_id": image.id,
                    "error_type": type(err).__name__,
                },
            )
            drift = {"current_digest": None, "drifted": False}

    now = clock()
    verdict = evaluate(result, policy, now=now)
    verdict_json = verdict.as_json()

    diff: dict[str, Any] | None = None
    if kind == "rescan" and image.last_scan_id and image.last_scan_id != scan.id:
        found = scan_diff(
            await repo.get_scan_result(image.last_scan_id),
            result,
            current_pass=verdict.passed,
            previous_scan_id=image.last_scan_id,
        )
        diff = found.as_json() if found is not None else None

    if not await _still_running(repo, scan.id):
        # Task 7 fix round 1: checked BEFORE any image write, not just at
        # the final `finish_scan` call below -- a scan a stall sweep already
        # resolved must not have its result overwritten, and the image must
        # not be transitioned on its behalf.
        logger.info(
            "image scan already resolved: skipping the image transition",
            extra={"scan_id": scan.id, "image_id": image.id},
        )
        return DrainOutcome(scan.id, image.id, ALREADY_RESOLVED, None)

    image_id = image.id
    final_status: str | None

    if kind == "admission":
        assert result.digest is not None
        existing = await repo.find_image_by_digest(
            result.reference, result.digest, exclude_id=image.id
        )
        if existing is not None:
            outcome = await _merge_or_fail(
                repo, scan, kind, run,
                provisional=image, existing=existing, scan_id=scan.id,
                verdict_json=verdict_json, passed=verdict.passed, now=now,
            )
            if isinstance(outcome, DrainOutcome):
                return outcome
            image_id, final_status = outcome
        else:

            async def write(
                status: str, expected_status: str, expected_exp: dt.datetime | None
            ) -> bool:
                try:
                    return await repo.record_admission(
                        image.id,
                        scan_id=scan.id,
                        result=result,
                        verdict=verdict_json,
                        status=status,
                        expected_status=expected_status,
                        expected_exception_expires_at=expected_exp,
                        at=now,
                    )
                except psycopg.errors.UniqueViolation as err:
                    raise _ConcurrentDigest from err

            try:
                final_status = await _cas_retry(
                    repo, write, image, passed=verdict.passed, now=now, refresh_id=image.id
                )
            except _ConcurrentDigest:
                # M3: another scan admitted the same (reference, digest)
                # between our read and our write -- re-drive through merge.
                raced = await repo.find_image_by_digest(
                    result.reference, result.digest, exclude_id=image.id
                )
                if raced is None:
                    return await _fail(
                        repo, scan, kind,
                        "a concurrent admission of the same digest could not be resolved",
                        run, reason="concurrent_digest",
                    )
                outcome = await _merge_or_fail(
                    repo, scan, kind, run,
                    provisional=image, existing=raced, scan_id=scan.id,
                    verdict_json=verdict_json, passed=verdict.passed, now=now,
                )
                if isinstance(outcome, DrainOutcome):
                    return outcome
                image_id, final_status = outcome
            else:
                image_id = image.id
                if final_status is None:
                    logger.info(
                        "image scan write skipped: the image changed between claim and write",
                        extra={"scan_id": scan.id, "image_id": image.id},
                    )
    else:

        async def write(
            status: str, expected_status: str, expected_exp: dt.datetime | None
        ) -> bool:
            return await repo.record_rescan(
                image.id,
                scan_id=scan.id,
                verdict=verdict_json,
                status=status,
                expected_status=expected_status,
                expected_exception_expires_at=expected_exp,
                at=now,
                tag_current_digest=(drift or {}).get("current_digest"),
            )

        final_status = await _cas_retry(
            repo, write, image, passed=verdict.passed, now=now, refresh_id=image.id
        )
        if final_status is None:
            logger.info(
                "image scan write skipped: the image changed between claim and write",
                extra={"scan_id": scan.id, "image_id": image.id},
            )

    stored = scan_result_to_json(result)
    # F10 (fix round 1): unconditional -- the scanner's own tag_drift is
    # untrusted (an admission's scanner never runs a HEAD, but nothing stops
    # it from writing a tag_drift into result.json anyway); `drift` is the
    # pipeline's own record, always None for an admission or when no check
    # ran, and must always win, never merged with or gated on `kind`.
    stored["tag_drift"] = drift
    stored = {**stored, "verdict": verdict_json, "diff": diff}
    finished = await repo.finish_scan(
        scan.id,
        status="done",
        result=stored,
        findings_ref=result.findings_ref,
        log_ref=run.log_ref,
        executor_handle=run.handle_id,
    )
    if not finished:
        # Item 5: same race as in `_fail`, on the success tail -- the image
        # may already have been transitioned above (a separate write, spec
        # gap I-138-adjacent), but the scan row itself must not be reported
        # as "done" when this write lost its guard.
        logger.warning(
            FINISH_SCAN_RACED_MSG, extra={"scan_id": scan.id, "image_id": image_id}
        )
        return DrainOutcome(scan.id, image_id, ALREADY_RESOLVED, None)
    return DrainOutcome(scan.id, image_id, "done", final_status)
