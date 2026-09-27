"""The rescan tick and exception expiry (C-4, container-images spec §8.2, §4.3, §4.4).

``pipeline.image_rescan_tick`` runs HOURLY (``15 * * * *``) and does two
things, in this order:

1. **Exception expiry.** Every image whose ``exception_expires_at`` has
   passed (the deploy gate's boundary: an expiry exactly now counts as
   expired) loses the four ``exception_*`` columns, which migration 030 ties
   together. An ``approved`` image is RE-EVALUATED: its latest stored scan
   result against the CURRENT policy. A pass keeps it ``approved`` with the
   fresh verdict. A fail makes it ``flagged``, never ``rejected``, because it
   may be in use (spec §4.3). A result that cannot be read fails closed
   (``flagged``). A row that is no longer approved (the drain flagged it
   after the exception lapsed) only has the columns cleared. The update and
   its ``audit_log`` row (actor ``pipeline``, action ``exception_expired`` on
   ``container_image``) are ONE statement, a compare-and-set on
   ``(status, exception_expires_at, last_scan_id)``: an admin who re-grants
   or revokes in between wins, and so does a rescan the drain records in
   between (the tick's stale verdict on an older scan is never written over
   a fresher one), and nothing is audited for a write that did not happen.
   After this, the deploy gate's ``exceptionLapsed`` rule (C-3) is defence
   in depth. The pipeline actor has no groups, so these audit rows carry an
   empty ``actor_groups`` (accepted: there is no group to attribute a
   scheduled tick to). One row's failure (a DB error reading its scan
   result, say) is logged and skipped; it never aborts the tick or the
   rescan requests that follow.
2. **Rescan requests.** Every ``approved`` or ``flagged`` image with a stored
   SBOM and digest, last scanned at least ``rescan_interval_hours`` ago,
   with no scan pending or running, AND with no ``rescan`` row of any status
   requested within the interval, gets ONE ``rescan`` row
   (``requested_by = 'pipeline'``). The last condition is the backoff: a
   scan that FAILS never moves ``last_scanned_at``, so without it a
   systemic fault (a missing scanner image, a dead egress sidecar) would be
   re-requested every hour forever; measuring from the last rescan REQUEST
   instead retries at most once per interval. The drain does the work (spec
   §4.2: a row, never a call). The image rows are locked ``FOR UPDATE SKIP
   LOCKED``, which serializes with the app's "Rescan now" (it locks the same
   row); a narrow snapshot race (the ``NOT EXISTS`` subquery reads the same
   statement snapshot as the lock) can still add one redundant rescan, which
   is harmless.

Hourly with an interval check, rather than spec §8.2's daily ``15 3 * * *``:
a daily tick with a 24-hour interval skips every image whose previous rescan
finished after 03:15, and an expiring exception is enforced within the hour
instead of within the day.
"""

from __future__ import annotations

import abc
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from pipeline.images.policy import ImagePolicy, evaluate
from pipeline.images.scan_result import ScanResultError, parse_scan_result

logger = logging.getLogger(__name__)

PIPELINE_ACTOR = "pipeline"
EXPIRY_AUDIT_ACTION = "exception_expired"
AUDIT_RESOURCE_TYPE = "container_image"
#: One tick's bound; the rest wait an hour.
EXPIRY_BATCH = 500


@dataclass(frozen=True)
class ExpiredException:
    image_id: str
    status: str
    exception_expires_at: dt.datetime
    exception_by: str | None
    last_scan_id: str | None


@dataclass(frozen=True)
class ExpiryDecision:
    status: str
    #: The fresh verdict to store, or None to keep the stored one.
    verdict: dict[str, Any] | None
    #: The audit row's ``detail``.
    detail: dict[str, Any]


@dataclass(frozen=True)
class LifecycleResult:
    exceptions_expired: int
    flagged_on_expiry: int
    rescans_requested: int


class ImageLifecycleRepo(abc.ABC):
    @abc.abstractmethod
    async def list_expired_exceptions(self, *, now: dt.datetime) -> list[ExpiredException]:
        """Rows whose ``exception_expires_at <= now``, never ``revoked``."""

    @abc.abstractmethod
    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:
        """The stored ``image_scans.result`` of one scan, or None."""

    @abc.abstractmethod
    async def expire_exception(
        self,
        image_id: str,
        *,
        expected_status: str,
        expected_expires_at: dt.datetime,
        expected_last_scan_id: str | None,
        status: str,
        verdict: dict[str, Any] | None,
        audit_detail: dict[str, Any],
    ) -> bool:
        """Compare-and-set on ``(status, exception_expires_at, last_scan_id)``,
        never onto a revoked row: write ``status`` (and ``verdict`` when
        given), clear the four ``exception_*`` columns, and append the audit
        row in the same statement. ``expected_last_scan_id`` catches a rescan
        the drain records between this tick's read and its write: without it
        the tick's re-evaluation of the OLDER scan would overwrite the
        drain's fresher verdict. Returns whether the row changed."""

    @abc.abstractmethod
    async def request_due_rescans(self, *, scanned_before: dt.datetime) -> int:
        """Insert one pipeline ``rescan`` row per due image; return how many."""


EXPIRED_EXCEPTIONS_SQL = (
    "SELECT id::text, status, exception_expires_at, exception_by, last_scan_id::text"
    " FROM stac_higher.container_images"
    " WHERE exception_expires_at IS NOT NULL AND exception_expires_at <= %s"
    " AND status <> 'revoked'"
    " ORDER BY exception_expires_at LIMIT %s"
)

EXPIRE_EXCEPTION_SQL = (
    "WITH changed AS ("
    "  UPDATE stac_higher.container_images"
    "     SET status = %s, verdict = COALESCE(%s::jsonb, verdict),"
    "         exception_reason = NULL, exception_by = NULL,"
    "         exception_at = NULL, exception_expires_at = NULL, updated_at = now()"
    "   WHERE id = %s::uuid AND status = %s AND status <> 'revoked'"
    "     AND exception_expires_at = %s"
    "     AND last_scan_id IS NOT DISTINCT FROM %s::uuid"
    "  RETURNING id"
    ")"
    " INSERT INTO stac_higher.audit_log (actor, action, resource_type, resource_id, detail)"
    " SELECT %s, %s, %s, id::text, %s FROM changed"
    " RETURNING 1"
)

#: The backoff bound (item 1): a rescan row of ANY status requested within
#: the interval blocks another request, so a failing rescan (which never
#: moves ``last_scanned_at``) is retried at most once per interval instead
#: of every hourly tick.
DUE_RESCANS_SQL = (
    "WITH due AS ("
    "  SELECT i.id FROM stac_higher.container_images i"
    "   WHERE i.status IN ('approved', 'flagged')"
    "     AND i.sbom_ref IS NOT NULL AND i.digest IS NOT NULL"
    "     AND (i.last_scanned_at IS NULL OR i.last_scanned_at <= %s)"
    "     AND NOT EXISTS ("
    "       SELECT 1 FROM stac_higher.image_scans s"
    "        WHERE s.image_id = i.id"
    "          AND (s.status IN ('pending', 'running')"
    "               OR (s.kind = 'rescan' AND s.requested_at > %s)))"
    "   FOR UPDATE OF i SKIP LOCKED"
    ")"
    " INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)"
    " SELECT id, 'rescan', %s FROM due"
    " RETURNING id"
)


@dataclass
class PgImageLifecycleRepo(ImageLifecycleRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_expired_exceptions(  # pragma: no cover
        self, *, now: dt.datetime
    ) -> list[ExpiredException]:
        async with await self._connect() as conn:
            cur = await conn.execute(EXPIRED_EXCEPTIONS_SQL, (now, EXPIRY_BATCH))
            rows = await cur.fetchall()
        return [ExpiredException(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT result FROM stac_higher.image_scans WHERE id = %s::uuid", (scan_id,)
            )
            row = await cur.fetchone()
        return row[0] if row and isinstance(row[0], dict) else None

    async def expire_exception(  # pragma: no cover
        self,
        image_id: str,
        *,
        expected_status: str,
        expected_expires_at: dt.datetime,
        expected_last_scan_id: str | None,
        status: str,
        verdict: dict[str, Any] | None,
        audit_detail: dict[str, Any],
    ) -> bool:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            cur = await conn.execute(
                EXPIRE_EXCEPTION_SQL,
                (
                    status,
                    Json(verdict) if verdict is not None else None,
                    image_id,
                    expected_status,
                    expected_expires_at,
                    expected_last_scan_id,
                    PIPELINE_ACTOR,
                    EXPIRY_AUDIT_ACTION,
                    AUDIT_RESOURCE_TYPE,
                    Json(audit_detail),
                ),
            )
            changed = await cur.fetchone() is not None
            await conn.commit()
        return changed

    async def request_due_rescans(  # pragma: no cover
        self, *, scanned_before: dt.datetime
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                DUE_RESCANS_SQL, (scanned_before, scanned_before, PIPELINE_ACTOR)
            )
            rows = await cur.fetchall()
            await conn.commit()
        return len(rows)


def decide_expiry(
    row: ExpiredException, result_doc: Any, policy: ImagePolicy, now: dt.datetime
) -> ExpiryDecision:
    """Pure: what an expired exception leaves behind (spec §4.3)."""
    detail: dict[str, Any] = {
        "previous_status": row.status,
        "expired_at": row.exception_expires_at.isoformat(),
        "exception_by": row.exception_by,
    }
    if row.status != "approved":
        return ExpiryDecision(row.status, None, {**detail, "status": row.status})
    try:
        if not isinstance(result_doc, dict):
            raise ScanResultError("no stored scan result")
        verdict = evaluate(parse_scan_result(result_doc), policy, now=now)
    except ScanResultError:
        return ExpiryDecision(
            "flagged",
            None,
            {
                **detail,
                "status": "flagged",
                "verdict_pass": None,
                "note": "the latest scan result could not be read; fails closed",
            },
        )
    status = "approved" if verdict.passed else "flagged"
    return ExpiryDecision(
        status,
        verdict.as_json(),
        {
            **detail,
            "status": status,
            "verdict_pass": verdict.passed,
            "reasons": list(verdict.reasons),
        },
    )


#: Logged when one row's expiry raises (final-review fix wave item 6): the
#: tick logs, skips that image, and moves on -- one bad row must not abort
#: the whole tick, nor the rescan requests that follow it. Ids and the
#: exception's type name only, never its text (ruling M4's rule applied here
#: too: the exception could in principle wrap untrusted data).
LIFECYCLE_ROW_FAILED_MSG = "exception expiry row failed: skipping this image, the tick continues"


async def lifecycle_tick(
    repo: ImageLifecycleRepo, *, policy: ImagePolicy, now: dt.datetime
) -> LifecycleResult:
    expired = flagged = 0
    for row in await repo.list_expired_exceptions(now=now):
        try:
            doc = await repo.get_scan_result(row.last_scan_id) if row.last_scan_id else None
            decision = decide_expiry(row, doc, policy, now)
            changed = await repo.expire_exception(
                row.image_id,
                expected_status=row.status,
                expected_expires_at=row.exception_expires_at,
                expected_last_scan_id=row.last_scan_id,
                status=decision.status,
                verdict=decision.verdict,
                audit_detail=decision.detail,
            )
        except Exception as err:  # one row's fault must not abort the tick or the rescans after it
            logger.warning(
                LIFECYCLE_ROW_FAILED_MSG,
                extra={"image_id": row.image_id, "error_type": type(err).__name__},
            )
            continue
        if not changed:
            logger.info(
                "exception expiry skipped: the image changed meanwhile",
                extra={"image_id": row.image_id},
            )
            continue
        expired += 1
        if row.status == "approved" and decision.status == "flagged":
            flagged += 1
    requested = await repo.request_due_rescans(
        scanned_before=now - dt.timedelta(hours=policy.rescan_interval_hours)
    )
    return LifecycleResult(expired, flagged, requested)
