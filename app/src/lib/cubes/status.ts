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
  "no_source_connection",
  "unsupported_layout",
  "source_missing",
  "no_datetime",
] as const;
export type CubeSkipReason = (typeof CUBE_SKIP_REASONS)[number];

/** Reasons on `appended` rows: the step was already in the cube (a no-op). */
export const CUBE_APPENDED_REASONS = ["duplicate"] as const;
export type CubeAppendedReason = (typeof CUBE_APPENDED_REASONS)[number];

/** The closed part of `failed` reasons; any other failed reason is error text (<= 500 chars). */
export const CUBE_FAILED_REASONS = ["crash_loop"] as const;
export type CubeFailedReason = (typeof CUBE_FAILED_REASONS)[number];

export const CUBE_APPEND_STATUS_LABEL: Record<CubeAppendStatus, string> = {
  pending: "Pending",
  appended: "Appended",
  skipped: "Skipped",
  failed: "Failed",
};

export const CUBE_SKIP_REASON_LABEL: Record<CubeSkipReason, string> = {
  late: "Arrived after a newer step",
  no_source_connection: "No reference ingest claims the file",
  unsupported_layout: "Layout differs from the cube",
  source_missing: "Source file is gone",
  no_datetime: "Item has no datetime",
};

export const CUBE_APPENDED_REASON_LABEL: Record<CubeAppendedReason, string> = {
  duplicate: "Already in the cube",
};

export const CUBE_FAILED_REASON_LABEL: Record<CubeFailedReason, string> = {
  crash_loop: "Gave up after repeated attempts",
};

/**
 * Label for a ledger row's reason. A reason is looked up only in its own
 * status's map; anything unknown (notably free failed-error text) is returned
 * verbatim.
 */
export function cubeLedgerReasonLabel(status: CubeAppendStatus, reason: string | null): string | null {
  if (reason === null) return null;
  const maps: Partial<Record<CubeAppendStatus, Record<string, string>>> = {
    skipped: CUBE_SKIP_REASON_LABEL,
    appended: CUBE_APPENDED_REASON_LABEL,
    failed: CUBE_FAILED_REASON_LABEL,
  };
  const map = maps[status];
  return map && Object.hasOwn(map, reason) ? map[reason] : reason;
}

/**
 * stac_higher.cube_sinks.last_maintenance vocabulary (docs/monitoring.md
 * "Cube repositories"). Pinned by tests/contract-fixtures/cube-maintenance-summary.json
 * against the pipeline's producer (pipeline/cubes/maintain.py). A newer
 * pipeline may write values outside these lists; the reader keeps them.
 */
export const CUBE_MAINTENANCE_STATUSES = ["ok", "attention", "failed"] as const;
export type CubeMaintenanceStatus = (typeof CUBE_MAINTENANCE_STATUSES)[number];

export const CUBE_MAINTENANCE_ATTENTION = [
  "repo_size",
  "gc_delete_failures",
  "unrecorded_tip",
  "publish_failed",
  "expiry_skipped",
  "measure_failed",
] as const;
export type CubeMaintenanceAttention = (typeof CUBE_MAINTENANCE_ATTENTION)[number];

export const CUBE_PUBLISH_OUTCOMES = [
  "published",
  "unchanged",
  "superseded",
  "missing_collection",
  "error",
] as const;
export type CubePublishOutcome = (typeof CUBE_PUBLISH_OUTCOMES)[number];

export const CUBE_SIZE_KINDS = ["transactions", "overwritten", "manifests", "snapshots", "chunks", "other"] as const;
export type CubeSizeKind = (typeof CUBE_SIZE_KINDS)[number];

export const CUBE_MAINTENANCE_STATUS_LABEL: Record<CubeMaintenanceStatus, string> = {
  ok: "OK",
  attention: "Needs attention",
  failed: "Failed",
};

export const CUBE_MAINTENANCE_ATTENTION_LABEL: Record<CubeMaintenanceAttention, string> = {
  repo_size: "Repository at or above the size warning",
  gc_delete_failures: "GC could not delete some objects",
  unrecorded_tip: "Repository tip is not the recorded snapshot",
  publish_failed: "Collection republish failed",
  expiry_skipped: "Expiry and GC skipped this run",
  measure_failed: "Size readout failed",
};

export const CUBE_PUBLISH_OUTCOME_LABEL: Record<CubePublishOutcome, string> = {
  published: "Published",
  unchanged: "Already current",
  superseded: "Superseded by a newer snapshot",
  missing_collection: "Cube collection missing",
  error: "Republish failed",
};
