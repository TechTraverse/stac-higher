"""The ``process_image_flagged`` alert (C-4, container-images spec §10).

One open alert per LIVE, ENABLED process whose CURRENT revision snapshots a
user image that can no longer be trusted as deployed: ``flagged`` (a rescan
newly failed it), ``revoked``, gone from the registry, any other
non-approved status, or stale (not scanned inside the policy's
``scan_window_days``; spec §4.3 - the same boundary as the deploy gate, so
exactly the window ago is still fresh). Anchored on ``process_id``, source
``health``. A disabled process raises nothing; it cannot run.

It is a CONDITION observed from state every minute (the image drain job runs
it before the drain), so auto-resolve falls out of its absence: the image is
approved and fresh again, or the process deployed a revision that no longer
uses it. An image nobody uses shows on the dashboard only.

This module is the kind's single writer (``alert-kinds.json``
``image_kinds``). It reconciles through the flow monitor's ``sync_alerts``,
scoped to :data:`IMAGE_ALERT_KINDS`, so neither writer ever resolves the
other's rows. Only a NEW alert row notifies (ADR 0010): a later rescan that
adds findings to an image that is already flagged updates the open row's
message, and does not re-notify (ISSUES I-141).
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pipeline.flow.repo import AlertCondition

IMAGE_FLAGGED_KIND = "process_image_flagged"
#: The kinds this module owns (raise AND auto-resolve).
IMAGE_ALERT_KINDS = (IMAGE_FLAGGED_KIND,)
ALERT_SOURCE = "health"
#: Policy reasons named in a message; the rest are counted.
MAX_REASONS = 5

SyncAlerts = Callable[[list[AlertCondition], tuple[str, ...]], Awaitable[tuple[int, int]]]


@dataclass(frozen=True)
class ImageInUse:
    process_id: str
    process_name: str
    image_id: str
    #: The revision's snapshot: what the process runs.
    snapshot_reference: str
    snapshot_digest: str
    #: None when the registry row no longer exists.
    status: str | None
    last_scanned_at: dt.datetime | None
    verdict: dict[str, Any] | None
    #: The latest scan's stored diff (C-4), or None.
    diff: dict[str, Any] | None


class ImageAlertsRepo(abc.ABC):
    @abc.abstractmethod
    async def list_images_in_use(self) -> list[ImageInUse]:
        """One row per live, enabled process whose current revision snapshots
        a user image (kinds 2 and 3), joined to the registry row (if any) and
        its latest scan's stored diff."""


IMAGES_IN_USE_SQL = (
    "SELECT p.id::text, p.name, r.runtime->'image'->>'id',"
    " r.runtime->'image'->>'reference', r.runtime->'image'->>'digest',"
    " i.status, i.last_scanned_at, i.verdict, s.result->'diff'"
    " FROM stac_higher.processes p"
    " JOIN stac_higher.process_revisions r ON r.id = p.current_revision"
    " LEFT JOIN stac_higher.container_images i"
    "   ON i.id::text = r.runtime->'image'->>'id'"
    " LEFT JOIN stac_higher.image_scans s ON s.id = i.last_scan_id"
    " WHERE p.deleted_at IS NULL AND p.enabled"
    " AND r.runtime->'image'->>'id' IS NOT NULL"
)


@dataclass
class PgImageAlertsRepo(ImageAlertsRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_images_in_use(self) -> list[ImageInUse]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(IMAGES_IN_USE_SQL)
            rows = await cur.fetchall()
        return [
            ImageInUse(
                process_id=r[0],
                process_name=r[1],
                image_id=r[2],
                snapshot_reference=r[3] or "",
                snapshot_digest=r[4] or "",
                status=r[5],
                last_scanned_at=r[6],
                verdict=r[7] if isinstance(r[7], dict) else None,
                diff=r[8] if isinstance(r[8], dict) else None,
            )
            for r in rows
        ]


def _is_stale(row: ImageInUse, scan_window_days: int, now: dt.datetime) -> bool:
    if row.status not in ("approved", "flagged"):
        return False
    if row.last_scanned_at is None:
        return True
    return row.last_scanned_at < now - dt.timedelta(days=scan_window_days)


def _reasons(verdict: dict[str, Any] | None) -> str:
    raw = verdict.get("reasons") if isinstance(verdict, dict) else None
    named = [r for r in raw if isinstance(r, str)] if isinstance(raw, list) else []
    if not named:
        return ""
    shown = ", ".join(named[:MAX_REASONS])
    more = len(named) - MAX_REASONS
    return f" ({shown}{f' and {more} more' if more > 0 else ''})"


def _diff(diff: dict[str, Any] | None) -> str:
    if not isinstance(diff, dict):
        return ""
    parts: list[str] = []
    new = diff.get("new")
    new_kev = diff.get("new_kev")
    if isinstance(new, list) and new:
        parts.append(f"{len(new)} new findings")
    if isinstance(new_kev, list) and new_kev:
        parts.append(f"{len(new_kev)} new KEV")
    if diff.get("verdict_changed") is True:
        parts.append("verdict changed")
    return f", since the previous scan: {', '.join(parts)}" if parts else ""


def image_alert_conditions(
    rows: list[ImageInUse], *, scan_window_days: int, now: dt.datetime
) -> list[AlertCondition]:
    """Pure: the currently-true conditions, one per process."""
    found: dict[str, AlertCondition] = {}
    for row in rows:
        if row.process_id in found:
            continue
        stale = _is_stale(row, scan_window_days, now)
        problems: list[str] = []
        if row.status is None:
            problems.append("no longer in the image registry, so its runs die at launch")
        elif row.status == "flagged":
            problems.append(
                f"flagged{_reasons(row.verdict)}{_diff(row.diff)}; new deploys are refused,"
                " runs continue"
            )
        elif row.status == "revoked":
            problems.append("revoked, so its runs die at launch")
        elif row.status != "approved":
            problems.append(f"{row.status}, so its runs die at launch")
        if stale:
            when = row.last_scanned_at.date().isoformat() if row.last_scanned_at else "never"
            problems.append(
                f"stale (last scanned {when}, outside the {scan_window_days}-day scan window),"
                " so its runs are refused until a rescan passes"
            )
        if not problems:
            continue
        named = f"image {row.snapshot_reference}@{row.snapshot_digest[:19]}"
        found[row.process_id] = AlertCondition(
            source=ALERT_SOURCE,
            kind=IMAGE_FLAGGED_KIND,
            process_id=row.process_id,
            message=f"{named}: " + "; ".join(problems),
        )
    return list(found.values())


async def sync_image_alerts(
    repo: ImageAlertsRepo,
    sync_alerts: SyncAlerts,
    *,
    scan_window_days: int,
    now: dt.datetime,
) -> tuple[int, int]:
    """Reconcile the kind's alerts with the current state. Always calls
    ``sync_alerts``, even with no conditions: that is the auto-resolve."""
    rows = await repo.list_images_in_use()
    found = image_alert_conditions(rows, scan_window_days=scan_window_days, now=now)
    return await sync_alerts(found, IMAGE_ALERT_KINDS)
