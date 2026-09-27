"""Retention of scan objects and rows (C-4, container-images spec §8.3).

Scan objects are PLATFORM bytes under ``scans/`` (like run logs under
``logs/``), never catalog assets, so ``asset_gc`` is not involved (ADR 0011).
The hourly ``history_retention`` sweep runs this leg after its table legs:

1. **Objects.** Keep, per image, the objects of its ten newest scans
   (``findings.grype.json``, ``result.json``, ``log``) and its CURRENT SBOM
   pair (``sbom_ref`` and the ``sbom.cdx.json`` beside it; every rescan reads
   it). Keep everything under a scan that is still pending or running.
   Delete everything else under ``scans/`` that is older than a 24-hour
   grace: older findings and logs, superseded SBOMs, the orphaned
   ``scans/{provisional_id}/...`` objects a digest dedup leaves (spec §9.1),
   and the objects of rows that no longer exist. The grace covers a scan
   that starts between this sweep's database read and its listing.
2. **Refs.** A terminal row outside its image's ten newest (and not its
   ``last_scan_id``) has its ``findings_ref``/``log_ref`` set to NULL: step 1
   removed those objects, and no row may keep naming them (the findings
   route then answers 404 instead of redirecting to a missing object).
3. **Rows**, AFTER the objects (spec §8.3, the run-log precedent I-62):
   ``image_scans`` rows older than ``HISTORY_RETENTION_DAYS``, outside their
   image's ten newest, terminal, and not the image's ``last_scan_id``. If
   step 1 raises, steps 2 and 3 do not run.

A scan's objects live under the prefix its refs name, because a folded
admission keeps the provisional image id in its keys; otherwise they live
under ``scans/{image_id}/{scan_id}/``.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from pipeline.storage.keys import SCANS_PREFIX, image_scan_prefix

KEEP_SCANS_PER_IMAGE = 10
OBJECT_GRACE = dt.timedelta(hours=24)
SCAN_OBJECT_NAMES = ("findings.grype.json", "result.json", "log")
SBOM_NAMES = ("sbom.syft.json", "sbom.cdx.json")


@dataclass(frozen=True)
class KeptScan:
    scan_id: str
    image_id: str
    status: str
    findings_ref: str | None
    log_ref: str | None


@dataclass(frozen=True)
class RetentionResult:
    objects_deleted: int
    rows_deleted: int


class ScanRetentionRepo(abc.ABC):
    @abc.abstractmethod
    async def list_kept_scans(self, *, keep: int) -> list[KeptScan]:
        """Each image's ``keep`` newest scans (by ``requested_at``), every
        pending or running scan, and every image's ``last_scan_id``."""

    @abc.abstractmethod
    async def list_sbom_refs(self) -> list[str]:
        """Every image's current ``sbom_ref``."""

    @abc.abstractmethod
    async def detach_pruned_refs(self, *, keep: int) -> int:
        """NULL ``findings_ref``/``log_ref`` on terminal rows outside their
        image's ``keep`` newest that are not its ``last_scan_id``."""

    @abc.abstractmethod
    async def prune_scan_rows(self, *, older_than: dt.datetime, keep: int) -> int:
        """Delete terminal rows requested before ``older_than`` that are
        outside their image's ``keep`` newest and are not its
        ``last_scan_id``. Returns how many."""


KEPT_SCANS_SQL = (
    "SELECT s.id::text, s.image_id::text, s.status, s.findings_ref, s.log_ref"
    " FROM (SELECT id, image_id, status, findings_ref, log_ref,"
    "        row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "       FROM stac_higher.image_scans) s"
    " JOIN stac_higher.container_images i ON i.id = s.image_id"
    " WHERE s.rn <= %s OR s.status IN ('pending', 'running') OR s.id = i.last_scan_id"
)

DETACH_PRUNED_REFS_SQL = (
    "UPDATE stac_higher.image_scans s SET findings_ref = NULL, log_ref = NULL"
    " FROM (SELECT id,"
    "        row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "       FROM stac_higher.image_scans) r,"
    "      stac_higher.container_images i"
    " WHERE r.id = s.id AND i.id = s.image_id AND r.rn > %s"
    " AND s.status IN ('done', 'failed')"
    " AND s.id IS DISTINCT FROM i.last_scan_id"
    " AND (s.findings_ref IS NOT NULL OR s.log_ref IS NOT NULL)"
)

PRUNE_SCAN_ROWS_SQL = (
    "DELETE FROM stac_higher.image_scans s"
    " USING (SELECT id,"
    "          row_number() OVER (PARTITION BY image_id ORDER BY requested_at DESC) AS rn"
    "         FROM stac_higher.image_scans) r,"
    "       stac_higher.container_images i"
    " WHERE r.id = s.id AND i.id = s.image_id"
    " AND r.rn > %s AND s.requested_at < %s"
    " AND s.status IN ('done', 'failed')"
    " AND s.id IS DISTINCT FROM i.last_scan_id"
)


@dataclass
class PgScanRetentionRepo(ScanRetentionRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def list_kept_scans(self, *, keep: int) -> list[KeptScan]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(KEPT_SCANS_SQL, (keep,))
            rows = await cur.fetchall()
        return [KeptScan(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def list_sbom_refs(self) -> list[str]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT sbom_ref FROM stac_higher.container_images WHERE sbom_ref IS NOT NULL"
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def detach_pruned_refs(self, *, keep: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(DETACH_PRUNED_REFS_SQL, (keep,))
            count = cur.rowcount or 0
            await conn.commit()
        return count

    async def prune_scan_rows(  # pragma: no cover
        self, *, older_than: dt.datetime, keep: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(PRUNE_SCAN_ROWS_SQL, (keep, older_than))
            count = cur.rowcount or 0
            await conn.commit()
        return count


def scan_prefix(scan: KeptScan) -> str:
    """Where one scan's objects live: the prefix its refs name when they name
    THIS scan (a folded admission keeps its provisional image id), else
    ``scans/{image_id}/{scan_id}/``."""
    for ref in (scan.findings_ref, scan.log_ref):
        parts = (ref or "").split("/")
        if len(parts) >= 4 and parts[0] == SCANS_PREFIX and parts[1] and parts[2] == scan.scan_id:
            return "/".join(parts[:3]) + "/"
    return image_scan_prefix(scan.image_id, scan.scan_id)


def _sbom_pair(sbom_ref: str) -> tuple[str, ...]:
    head, _, _ = sbom_ref.rpartition("/")
    return tuple(f"{head}/{name}" for name in SBOM_NAMES) if head else ()


def keep_set(
    scans: Iterable[KeptScan], sbom_refs: Iterable[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """``(keys to keep, prefixes to keep whole)``."""
    keep: set[str] = set()
    whole: set[str] = set()
    for scan in scans:
        prefix = scan_prefix(scan)
        if scan.status in ("pending", "running"):
            whole.add(prefix)
        else:
            keep.update(prefix + name for name in SCAN_OBJECT_NAMES)
    for ref in sbom_refs:
        keep.update(_sbom_pair(ref))
    return frozenset(keep), frozenset(whole)


def keys_to_delete(
    objects: Iterable[tuple[str, dt.datetime]],
    *,
    keep_keys: frozenset[str],
    keep_prefixes: frozenset[str],
    now: dt.datetime,
    grace: dt.timedelta = OBJECT_GRACE,
) -> list[str]:
    cutoff = now - grace
    return [
        key
        for key, modified in objects
        if key not in keep_keys
        and not any(key.startswith(p) for p in keep_prefixes)
        and modified < cutoff
    ]


async def scan_retention_tick(
    repo: ScanRetentionRepo,
    *,
    list_objects: Callable[[], Awaitable[list[tuple[str, dt.datetime]]]],
    delete_keys: Callable[[list[str]], Awaitable[int]],
    history_days: int,
    now: dt.datetime,
) -> RetentionResult:
    scans = await repo.list_kept_scans(keep=KEEP_SCANS_PER_IMAGE)
    sboms = await repo.list_sbom_refs()
    keep, whole = keep_set(scans, sboms)
    doomed = keys_to_delete(await list_objects(), keep_keys=keep, keep_prefixes=whole, now=now)
    deleted = await delete_keys(doomed) if doomed else 0
    await repo.detach_pruned_refs(keep=KEEP_SCANS_PER_IMAGE)
    rows = await repo.prune_scan_rows(
        older_than=now - dt.timedelta(days=history_days), keep=KEEP_SCANS_PER_IMAGE
    )
    return RetentionResult(objects_deleted=deleted, rows_deleted=rows)
