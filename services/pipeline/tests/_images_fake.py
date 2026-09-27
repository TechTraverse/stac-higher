"""In-memory ImagesRepo for C-2 unit tests: the behavioural contract of
pipeline/images/repo.py, which its Pg SQL must match."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pipeline.images.repo import (
    STALLED_SCAN_ERROR,
    ClaimedScan,
    ImageRow,
    ImagesRepo,
    RegistryCredentialRow,
)
from pipeline.images.scan_result import ScanResult, failure_result


@dataclass
class FakeImagesRepo(ImagesRepo):
    images: dict[str, ImageRow] = field(default_factory=dict)
    scans: dict[str, dict[str, Any]] = field(default_factory=dict)
    credentials: dict[str, RegistryCredentialRow] = field(default_factory=dict)
    verdicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_scan_ids: dict[str, str] = field(default_factory=dict)
    tag_digests: dict[str, str] = field(default_factory=dict)
    deleted_images: list[str] = field(default_factory=list)
    clock: dt.datetime = field(
        default_factory=lambda: dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
    )

    # -- test set-up --------------------------------------------------------

    def add_image(self, **fields: Any) -> ImageRow:
        row = ImageRow(**fields)
        self.images[row.id] = row
        return row

    def add_scan(
        self,
        scan_id: str,
        image_id: str,
        kind: str = "admission",
        *,
        requested_at: dt.datetime | None = None,
        status: str = "pending",
        started_at: dt.datetime | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.scans[scan_id] = {
            "id": scan_id,
            "image_id": image_id,
            "kind": kind,
            "status": status,
            "requested_by": "user-1",
            "requested_at": requested_at or self.clock,
            "started_at": started_at,
            "result": result,
            "findings_ref": None,
            "log_ref": None,
            "executor_handle": None,
        }

    def add_credential(self, row: RegistryCredentialRow) -> None:
        self.credentials[row.connection_id] = row

    def _set(self, image_id: str, **changes: Any) -> None:
        self.images[image_id] = replace(self.images[image_id], **changes)

    def _cas(
        self,
        image_id: str,
        expected_status: str,
        expected_exception_expires_at: dt.datetime | None,
    ) -> bool:
        """The same guard the Pg UPDATE's WHERE clause enforces: the row's
        status must still equal ``expected_status``, its exception expiry
        must still equal ``expected_exception_expires_at``, and the status
        must never be ``revoked``."""
        image = self.images.get(image_id)
        if image is None:
            return False
        return (
            image.status == expected_status
            and image.status != "revoked"
            and image.exception_expires_at == expected_exception_expires_at
        )

    # -- ImagesRepo ---------------------------------------------------------

    async def get_image(self, image_id: str) -> ImageRow | None:
        return self.images.get(image_id)

    async def get_scan_result(self, scan_id: str) -> dict[str, Any] | None:
        scan = self.scans.get(scan_id)
        return scan["result"] if scan else None

    async def claim_pending_scan(self, *, max_running: int) -> ClaimedScan | None:
        if sum(1 for s in self.scans.values() if s["status"] == "running") >= max_running:
            return None
        pending = sorted(
            (s for s in self.scans.values() if s["status"] == "pending"),
            key=lambda s: s["requested_at"],
        )
        if not pending:
            return None
        scan = pending[0]
        scan["status"] = "running"
        scan["started_at"] = self.clock
        image = self.images[scan["image_id"]]
        if scan["kind"] == "admission" and image.status in ("pending", "scan_failed"):
            self._set(image.id, status="scanning")
        return ClaimedScan(
            id=scan["id"],
            kind=scan["kind"],
            requested_by=scan["requested_by"],
            image=self.images[image.id],
        )

    async def fail_stalled_scans(self, *, started_before: dt.datetime) -> int:
        count = 0
        for scan in self.scans.values():
            if scan["status"] != "running" or scan["started_at"] >= started_before:
                continue
            image = self.images[scan["image_id"]]
            scan["status"] = "failed"
            scan["result"] = failure_result(
                scan["kind"], image.reference, image.tag_at_add, STALLED_SCAN_ERROR
            )
            if image.status in ("pending", "scanning"):
                self._set(image.id, status="scan_failed")
            count += 1
        return count

    async def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> bool:
        scan = self.scans.get(scan_id)
        if scan is None or scan["status"] != "running":
            return False
        scan.update(
            status=status,
            result=result,
            findings_ref=findings_ref,
            log_ref=log_ref,
            executor_handle=executor_handle,
        )
        return True

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
        if not self._cas(image_id, expected_status, expected_exception_expires_at):
            return False
        self._set(
            image_id,
            digest=result.digest,
            platform_digest=result.platform_digest,
            platform=result.platform,
            size_bytes=result.size_bytes,
            config=result.config,
            sbom_ref=result.sbom_ref,
            status=status,
            last_scanned_at=at,
            last_scan_id=scan_id,
        )
        self.verdicts[image_id] = verdict
        self.last_scan_ids[image_id] = scan_id
        return True

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
        tag_current_digest: str | None = None,
    ) -> bool:
        if not self._cas(image_id, expected_status, expected_exception_expires_at):
            return False
        self._set(image_id, status=status, last_scanned_at=at, last_scan_id=scan_id)
        self.verdicts[image_id] = verdict
        self.last_scan_ids[image_id] = scan_id
        if tag_current_digest is not None:
            self.tag_digests[image_id] = tag_current_digest
        return True

    async def find_image_by_digest(
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        for row in self.images.values():
            if row.id != exclude_id and row.reference == reference and row.digest == digest:
                return row
        return None

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
        if not self._cas(existing_id, expected_status, expected_exception_expires_at):
            return False
        self.scans[scan_id]["image_id"] = existing_id
        self._set(existing_id, status=status, last_scanned_at=at, last_scan_id=scan_id)
        self.verdicts[existing_id] = verdict
        self.last_scan_ids[existing_id] = scan_id
        if self.images[provisional_id].status in ("pending", "scanning", "scan_failed"):
            del self.images[provisional_id]
            self.deleted_images.append(provisional_id)
        return True

    async def mark_image_scan_failed(self, image_id: str) -> None:
        image = self.images.get(image_id)
        if image is not None and image.status in ("pending", "scanning"):
            self._set(image_id, status="scan_failed")

    async def scan_statuses(self, scan_ids: Sequence[str]) -> dict[str, str]:
        return {s: self.scans[s]["status"] for s in scan_ids if s in self.scans}

    async def get_registry_credential(self, connection_id: str) -> RegistryCredentialRow | None:
        return self.credentials.get(connection_id)
