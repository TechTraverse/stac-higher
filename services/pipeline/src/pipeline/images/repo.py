"""Repository seam over ``container_images`` + ``image_scans`` (C-2,
container-images spec §4, §8.1, §8.4).

The pipeline reads and writes ROWS here and never DDL (ADR 0001; migration
030 is the app's). The drain and the launch path depend on the ABC, so
their logic is tested against ``tests/_images_fake.py``;
:class:`PgImagesRepo` is the psycopg implementation, exercised by the
DB-gated ``tests/test_integration_images_repo.py``.

**Compare-and-set status writes.** ``record_admission``, ``record_rescan``
and ``merge_admission`` take the image status AND the exception expiry the
caller read at claim time (``expected_status``, ``expected_exception_expires_at``)
and only write when the row's status still matches, its exception expiry
still matches, and the status is not ``revoked``; each returns whether it
changed a row. This closes the window between the drain reading an image's
status and writing its verdict -- an admin can grant or revoke an exception,
or revoke the image outright, in between, and that decision must never be
clobbered by a stale scan result. On a miss, the drain re-reads the row with
:meth:`ImagesRepo.get_image` (its current ``status`` and
``exception_expires_at`` are enough to recompute), and retries once (Task 7).
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.images.scan_result import SCAN_RESULT_VERSION, ScanResult

#: The error a stall sweep writes (a scan still `running` long after its
#: timeout: the worker that ran it is gone).
STALLED_SCAN_ERROR = "the scan stalled: the worker running it was lost before it finished"
#: Serialises claims across workers so the running-scan cap holds.
CLAIM_LOCK_KEY = "stac_higher.image_scans.claim"


@dataclass(frozen=True)
class ImageRow:
    """The columns of one ``container_images`` row the pipeline reads."""

    id: str
    reference: str
    tag_at_add: str
    status: str
    digest: str | None = None
    platform_digest: str | None = None
    platform: dict[str, Any] | None = None
    size_bytes: int | None = None
    config: dict[str, Any] | None = None
    sbom_ref: str | None = None
    last_scanned_at: dt.datetime | None = None
    registry_connection_id: str | None = None
    exception_expires_at: dt.datetime | None = None


@dataclass(frozen=True)
class ClaimedScan:
    """A scan row the drain now owns (status flipped to ``running``), with its
    image as it was AFTER the claim."""

    id: str
    kind: str
    requested_by: str
    image: ImageRow


@dataclass(frozen=True)
class RegistryCredentialRow:
    connection_id: str
    protocol: str
    config: dict[str, Any]
    #: raw credential envelope (bytea) -- decrypted only at launch.
    credentials: bytes | None
    deleted: bool


class ImagesRepo(abc.ABC):
    """DB access the scan drain and the launch path depend on."""

    @abc.abstractmethod
    async def get_image(self, image_id: str) -> ImageRow | None:
        """The row by id, or None. Also the re-read the drain uses after a
        compare-and-set miss on ``record_admission``/``record_rescan``/
        ``merge_admission``: its ``status`` and ``exception_expires_at`` are
        enough to recompute what happened between claim and write and retry
        once."""

    @abc.abstractmethod
    async def claim_pending_scan(self, *, max_running: int) -> ClaimedScan | None:
        """Claim the oldest pending scan unless ``max_running`` scans already
        run (deployment-wide). Flips it to ``running`` with ``started_at``,
        and an ADMISSION scan's image from ``pending``/``scan_failed`` to
        ``scanning`` (a rescan leaves the visible status alone, spec §8.1)."""

    @abc.abstractmethod
    async def fail_stalled_scans(self, *, started_before: dt.datetime) -> int:
        """Fail every ``running`` scan started before the cutoff with
        :data:`STALLED_SCAN_ERROR` (an identity-bearing result), flip its
        image from ``pending``/``scanning`` to ``scan_failed``; return how
        many scans were failed."""

    @abc.abstractmethod
    async def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> None:
        """Write the terminal scan row (``done``/``failed``) with ``finished_at``."""

    @abc.abstractmethod
    async def record_admission(
        self,
        image_id: str,
        *,
        scan_id: str,
        result: ScanResult,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        """Spec §8.1: fill digest, platform_digest, platform, size_bytes,
        config, sbom_ref, verdict, status, last_scan_id, last_scanned_at --
        but only when the row's status still equals ``expected_status`` AND
        its ``exception_expires_at`` still equals
        ``expected_exception_expires_at`` (both read by the caller at claim
        time) and the status is not ``revoked``. Returns whether the row
        changed; a miss means an admin acted on the image between claim and
        write, and the caller must not overwrite that decision."""

    @abc.abstractmethod
    async def record_rescan(
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        """Spec §8.1: only verdict, status, last_scan_id, last_scanned_at --
        the same compare-and-set as :meth:`record_admission`."""

    @abc.abstractmethod
    async def find_image_by_digest(
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        """Another row with the same ``(reference, digest)`` (spec §9.1 dedup)."""

    @abc.abstractmethod
    async def merge_admission(
        self,
        *,
        provisional_id: str,
        existing_id: str,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        """Spec §9.1 dedup, atomically: re-point the scan at the existing row,
        record the verdict there (its own SBOM stays), delete the provisional
        row while it is still ``pending``/``scanning``/``scan_failed`` --
        but only when the EXISTING row's status still equals
        ``expected_status`` AND its exception expiry still equals
        ``expected_exception_expires_at`` (the same compare-and-set as
        :meth:`record_admission`). On a miss nothing is written -- the scan
        stays pointed at the provisional row and the provisional row is not
        deleted -- and the caller retries after re-reading."""

    @abc.abstractmethod
    async def mark_image_scan_failed(self, image_id: str) -> None:
        """``pending``/``scanning`` -> ``scan_failed``; any other status stays."""

    @abc.abstractmethod
    async def scan_statuses(self, scan_ids: Sequence[str]) -> dict[str, str]:
        """``{scan_id: status}`` for the reaper; unknown ids are absent."""

    @abc.abstractmethod
    async def get_registry_credential(self, connection_id: str) -> RegistryCredentialRow | None:
        """The connection behind an image's pull credential, soft-deleted
        ones included (``deleted`` says so), or None."""


_IMAGE_COLUMNS = (
    "id::text, reference, tag_at_add, status, digest, platform_digest, platform,"
    " size_bytes, config, sbom_ref, last_scanned_at, registry_connection_id::text,"
    " exception_expires_at"
)


def _to_image(row: Sequence[Any]) -> ImageRow:
    return ImageRow(
        id=row[0],
        reference=row[1],
        tag_at_add=row[2],
        status=row[3],
        digest=row[4],
        platform_digest=row[5],
        platform=row[6],
        size_bytes=int(row[7]) if row[7] is not None else None,
        config=row[8],
        sbom_ref=row[9],
        last_scanned_at=row[10],
        registry_connection_id=row[11],
        exception_expires_at=row[12],
    )


@dataclass
class PgImagesRepo(ImagesRepo):
    """psycopg-backed repo over the process-wide async pool (M3-B)."""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def get_image(self, image_id: str) -> ImageRow | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                " WHERE id = %s::uuid",
                (image_id,),
            )
            row = await cur.fetchone()
        return _to_image(row) if row else None

    async def claim_pending_scan(  # pragma: no cover
        self, *, max_running: int
    ) -> ClaimedScan | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                # Held to the end of this transaction: two workers cannot
                # both count "0 running" and both claim.
                await cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (CLAIM_LOCK_KEY,))
                await cur.execute(
                    "SELECT count(*) FROM stac_higher.image_scans WHERE status = 'running'"
                )
                (running,) = await cur.fetchone()
                if running >= max_running:
                    await conn.commit()
                    return None
                await cur.execute(
                    "SELECT id::text, image_id::text, kind, requested_by"
                    " FROM stac_higher.image_scans WHERE status = 'pending'"
                    " ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                claimed = await cur.fetchone()
                if claimed is None:
                    await conn.commit()
                    return None
                scan_id, image_id, kind, requested_by = claimed
                await cur.execute(
                    "UPDATE stac_higher.image_scans SET status = 'running', started_at = now()"
                    " WHERE id = %s::uuid",
                    (scan_id,),
                )
                if kind == "admission":
                    await cur.execute(
                        "UPDATE stac_higher.container_images"
                        " SET status = 'scanning', updated_at = now()"
                        " WHERE id = %s::uuid AND status IN ('pending', 'scan_failed')",
                        (image_id,),
                    )
                await cur.execute(
                    f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                    " WHERE id = %s::uuid",
                    (image_id,),
                )
                image_row = await cur.fetchone()
            await conn.commit()
        return ClaimedScan(
            id=scan_id, kind=kind, requested_by=requested_by, image=_to_image(image_row)
        )

    async def fail_stalled_scans(  # pragma: no cover
        self, *, started_before: dt.datetime
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH stalled AS ("
                "  UPDATE stac_higher.image_scans s"
                "     SET status = 'failed', finished_at = now(),"
                "         result = jsonb_build_object("
                "           'version', %s::int, 'kind', s.kind, 'reference', ci.reference,"
                "           'tag', ci.tag_at_add, 'error', %s::text)"
                "    FROM stac_higher.container_images ci"
                "   WHERE ci.id = s.image_id AND s.status = 'running'"
                "     AND s.started_at < %s"
                "  RETURNING s.image_id"
                "), flipped AS ("
                "  UPDATE stac_higher.container_images"
                "     SET status = 'scan_failed', updated_at = now()"
                "   WHERE id IN (SELECT image_id FROM stalled)"
                "     AND status IN ('pending', 'scanning')"
                "  RETURNING 1"
                ") SELECT count(*) FROM stalled",
                (SCAN_RESULT_VERSION, STALLED_SCAN_ERROR, started_before),
            )
            (count,) = await cur.fetchone()
            await conn.commit()
        return int(count)

    async def finish_scan(  # pragma: no cover
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.image_scans SET status = %s, result = %s,"
                " findings_ref = %s, log_ref = %s, executor_handle = %s, finished_at = now()"
                " WHERE id = %s::uuid",
                (status, Json(result), findings_ref, log_ref, executor_handle, scan_id),
            )
            await conn.commit()

    async def record_admission(  # pragma: no cover
        self,
        image_id: str,
        *,
        scan_id: str,
        result: ScanResult,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.container_images SET digest = %s, platform_digest = %s,"
                " platform = %s, size_bytes = %s, config = %s, sbom_ref = %s, verdict = %s,"
                " status = %s, last_scan_id = %s::uuid, last_scanned_at = %s,"
                " updated_at = now() WHERE id = %s::uuid AND status = %s"
                " AND status <> 'revoked'"
                " AND exception_expires_at IS NOT DISTINCT FROM %s RETURNING id",
                (
                    result.digest,
                    result.platform_digest,
                    Json(result.platform),
                    result.size_bytes,
                    Json(result.config),
                    result.sbom_ref,
                    Json(verdict),
                    status,
                    scan_id,
                    at,
                    image_id,
                    expected_status,
                    expected_exception_expires_at,
                ),
            )
            changed = await cur.fetchone() is not None
            await conn.commit()
        return changed

    async def record_rescan(  # pragma: no cover
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.container_images SET verdict = %s, status = %s,"
                " last_scan_id = %s::uuid, last_scanned_at = %s, updated_at = now()"
                " WHERE id = %s::uuid AND status = %s AND status <> 'revoked'"
                " AND exception_expires_at IS NOT DISTINCT FROM %s RETURNING id",
                (
                    Json(verdict),
                    status,
                    scan_id,
                    at,
                    image_id,
                    expected_status,
                    expected_exception_expires_at,
                ),
            )
            changed = await cur.fetchone() is not None
            await conn.commit()
        return changed

    async def find_image_by_digest(  # pragma: no cover
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                " WHERE reference = %s AND digest = %s AND id <> %s::uuid",
                (reference, digest, exclude_id),
            )
            row = await cur.fetchone()
        return _to_image(row) if row else None

    async def merge_admission(  # pragma: no cover
        self,
        *,
        provisional_id: str,
        existing_id: str,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.container_images SET verdict = %s, status = %s,"
                " last_scan_id = %s::uuid, last_scanned_at = %s, updated_at = now()"
                " WHERE id = %s::uuid AND status = %s AND status <> 'revoked'"
                " AND exception_expires_at IS NOT DISTINCT FROM %s RETURNING id",
                (
                    Json(verdict),
                    status,
                    scan_id,
                    at,
                    existing_id,
                    expected_status,
                    expected_exception_expires_at,
                ),
            )
            changed = await cur.fetchone() is not None
            if changed:
                await conn.execute(
                    "UPDATE stac_higher.image_scans SET image_id = %s::uuid WHERE id = %s::uuid",
                    (existing_id, scan_id),
                )
                await conn.execute(
                    "DELETE FROM stac_higher.container_images WHERE id = %s::uuid"
                    " AND status IN ('pending', 'scanning', 'scan_failed')",
                    (provisional_id,),
                )
            await conn.commit()
        return changed

    async def mark_image_scan_failed(self, image_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.container_images SET status = 'scan_failed',"
                " updated_at = now() WHERE id = %s::uuid AND status IN ('pending', 'scanning')",
                (image_id,),
            )
            await conn.commit()

    async def scan_statuses(  # pragma: no cover
        self, scan_ids: Sequence[str]
    ) -> dict[str, str]:
        ids = list(scan_ids)
        if not ids:
            return {}
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, status FROM stac_higher.image_scans"
                " WHERE id = ANY(%s::uuid[])",
                (ids,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_registry_credential(  # pragma: no cover
        self, connection_id: str
    ) -> RegistryCredentialRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, protocol, config, credentials, deleted_at IS NOT NULL"
                " FROM stac_higher.connections WHERE id = %s::uuid",
                (connection_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return RegistryCredentialRow(
            connection_id=row[0],
            protocol=row[1],
            config=row[2] or {},
            credentials=bytes(row[3]) if row[3] is not None else None,
            deleted=bool(row[4]),
        )
