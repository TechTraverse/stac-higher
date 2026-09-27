/**
 * The container-image vocabularies (C-1, container-images spec §4.3, ADR 0021).
 * A cross-runtime contract: `tests/contract-fixtures/image-status.json` pins
 * every list against `pipeline/images/status.py` and against migration 030's
 * CHECK constraints. Stale is not a status. It is computed from
 * `last_scanned_at` and the policy's `scan_window_days`, and never stored.
 */
export const IMAGE_STATUSES = [
  "pending",
  "scanning",
  "approved",
  "rejected",
  "flagged",
  "revoked",
  "scan_failed",
] as const;
export type ImageStatus = (typeof IMAGE_STATUSES)[number];

/** The statuses a NEW revision may snapshot (spec §4.3). */
export const DEPLOY_IMAGE_STATUSES = ["approved"] as const;
/** The statuses a triggered run may still launch on. Flagged blocks deploys,
 * never runs (spec decision 4). */
export const LAUNCH_IMAGE_STATUSES = ["approved", "flagged"] as const;

export const IMAGE_SCAN_KINDS = ["admission", "rescan"] as const;
export type ImageScanKind = (typeof IMAGE_SCAN_KINDS)[number];
/** The `connection_checks` shape (spec §4.2). */
export const IMAGE_SCAN_STATUSES = ["pending", "running", "done", "failed"] as const;

/** The deploy gate's four 422 reasons (spec §3). */
export const IMAGE_GATE_REASONS = [
  "image_not_approved",
  "image_stale",
  "image_group_mismatch",
  "image_digest_mismatch",
] as const;
export type ImageGateReason = (typeof IMAGE_GATE_REASONS)[number];

/** Badge labels for C-3's dashboard. The fixture consumer asserts this covers
 * every status, so a new status cannot land unlabelled. */
export const IMAGE_STATUS_LABEL: Record<ImageStatus, string> = {
  pending: "Waiting for scan",
  scanning: "Scanning",
  approved: "Approved",
  rejected: "Rejected",
  flagged: "Flagged",
  revoked: "Revoked",
  scan_failed: "Scan failed",
};
