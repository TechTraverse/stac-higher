"""The container-image vocabularies (C-1, container-images spec section 4.3).

Pinned by ``tests/contract-fixtures/image-status.json`` against
``app/src/lib/images/status.ts`` and migration 030's CHECK constraints. Stale is
not a status: it is computed from ``last_scanned_at`` and the policy's
``scan_window_days``.
"""

from __future__ import annotations

IMAGE_STATUSES = (
    "pending",
    "scanning",
    "approved",
    "rejected",
    "flagged",
    "revoked",
    "scan_failed",
)
#: A NEW revision may snapshot only these.
DEPLOY_STATUSES = ("approved",)
#: A triggered run may still launch on these (flagged blocks deploys, never runs).
LAUNCH_STATUSES = ("approved", "flagged")
SCAN_KINDS = ("admission", "rescan")
SCAN_STATUSES = ("pending", "running", "done", "failed")
#: The app's deploy-gate reasons. C-2's launch path reports the same strings.
GATE_REASONS = (
    "image_not_approved",
    "image_stale",
    "image_group_mismatch",
    "image_digest_mismatch",
)
