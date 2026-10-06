/**
 * stac_higher.cube_appends vocabulary (virtual cube spec §3.2, §5.2). Pinned
 * by tests/contract-fixtures/cube-append-status.json against
 * pipeline/cubes/config.py and migration 032's CHECK constraint.
 */
export const CUBE_APPEND_STATUSES = ["pending", "appended", "skipped", "failed"] as const;
export type CubeAppendStatus = (typeof CUBE_APPEND_STATUSES)[number];

export const CUBE_APPEND_TERMINAL = ["appended", "skipped", "failed"] as const;

export const CUBE_SKIP_REASONS = [
  "late",
  "duplicate",
  "no_source_connection",
  "unsupported_layout",
  "source_missing",
  "no_datetime",
] as const;
export type CubeSkipReason = (typeof CUBE_SKIP_REASONS)[number];

export const CUBE_APPEND_STATUS_LABEL: Record<CubeAppendStatus, string> = {
  pending: "Pending",
  appended: "Appended",
  skipped: "Skipped",
  failed: "Failed",
};

export const CUBE_SKIP_REASON_LABEL: Record<CubeSkipReason, string> = {
  late: "Arrived after a newer step",
  duplicate: "Already in the cube",
  no_source_connection: "No reference ingest claims the file",
  unsupported_layout: "Layout differs from the cube",
  source_missing: "Source file is gone",
  no_datetime: "Item has no datetime",
};
