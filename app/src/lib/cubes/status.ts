import { durationSeconds } from "./duration";
import type { ApiCubeSink, CubeLedgerSummary, ReferenceIngestSource } from "./storage";

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

export const CUBE_SINK_HEALTH_STATES = ["disabled", "error", "attention", "stale", "waiting", "ok"] as const;
export type CubeSinkHealthState = (typeof CUBE_SINK_HEALTH_STATES)[number];

export const CUBE_SINK_HEALTH_LABEL: Record<CubeSinkHealthState, string> = {
  disabled: "Disabled",
  error: "Error",
  attention: "Needs attention",
  stale: "No recent data",
  waiting: "Waiting for the first append",
  ok: "Healthy",
};

export interface CubeSinkHealth {
  state: CubeSinkHealthState;
  /** `last_error`, `maintenance_failed`, the maintenance attention reasons
   * verbatim, `failed_rows` and `no_append_within_window`. */
  reasons: string[];
}

/**
 * The one state a sink card shows; first match wins:
 * disabled > error > attention > stale > waiting > ok.
 *
 * - `disabled` is the operator's choice, even when broken (`last_error` still
 *   shows in `reasons`).
 * - `stale` only with `window.max_age`: once nothing was appended for the whole
 *   age, every step has aged out and the next trim empties the cube. A sink
 *   with only `max_steps`, or no window, is never stale: nothing in the row
 *   knows the source's cadence.
 * - `failed_rows` reads `recent` (activity order), not the 7-day counts, so one
 *   old failure cannot pin the card.
 */
export function cubeSinkHealth(
  sink: Pick<ApiCubeSink, "enabled" | "last_error" | "last_maintenance" | "last_appended_at" | "config">,
  ledger: Pick<CubeLedgerSummary, "recent">,
  now: Date = new Date(),
): CubeSinkHealth {
  if (!sink.enabled) return { state: "disabled", reasons: sink.last_error ? ["last_error"] : [] };
  if (sink.last_error) return { state: "error", reasons: ["last_error"] };
  const maintenance = sink.last_maintenance;
  if (maintenance?.status === "failed") return { state: "error", reasons: ["maintenance_failed"] };

  const attention = [...(maintenance?.attention ?? [])];
  if (ledger.recent.some((r) => r.status === "failed")) attention.push("failed_rows");
  if (attention.length > 0) return { state: "attention", reasons: attention };

  if (sink.last_appended_at === null) return { state: "waiting", reasons: [] };

  const maxAge = sink.config.window?.max_age;
  const seconds = maxAge === undefined ? null : durationSeconds(maxAge);
  if (seconds !== null && now.getTime() - Date.parse(sink.last_appended_at) > seconds * 1000) {
    return { state: "stale", reasons: ["no_append_within_window"] };
  }
  return { state: "ok", reasons: [] };
}

export const CUBE_SOURCE_REFUSAL_CODES = [
  "no_reference_ingest",
  "unsupported_source_protocol",
  "signed_source_unsupported",
] as const;
export type CubeSourceRefusalCode = (typeof CUBE_SOURCE_REFUSAL_CODES)[number];

/**
 * Whether a source collection's reference-mode ingest associations qualify
 * for a cube sink, as the 422 PUT/PATCH would answer (null = eligible). The
 * order is the route's: nothing to read, then a non-s3 association, then a
 * signed one. Shared by the routes and the source picker so the picker's
 * pre-filter cannot disagree with PUT. Lives here, not in `storage.ts`,
 * because the route tests mock the whole storage module.
 */
export function referenceSourceRefusal(
  refs: readonly Pick<ReferenceIngestSource, "protocol" | "anonymous">[],
  /** Named in the `no_reference_ingest` message when given. */
  source?: string,
): { code: CubeSourceRefusalCode; error: string } | null {
  if (refs.length === 0) {
    return {
      code: "no_reference_ingest",
      error: `Source collection${source === undefined ? "" : ` '${source}'`} has no enabled reference-mode ingest association`,
    };
  }
  const foreign = [...new Set(refs.filter((r) => r.protocol !== "s3").map((r) => r.protocol))];
  if (foreign.length > 0) {
    return {
      code: "unsupported_source_protocol",
      error: `Cube sinks read reference-mode ingest from s3 connections only; this source also ingests by reference over ${foreign.join(", ")}`,
    };
  }
  if (refs.some((r) => !r.anonymous)) {
    return {
      code: "signed_source_unsupported",
      error: "Cube sinks support anonymous (public) source connections only in v1",
    };
  }
  return null;
}
