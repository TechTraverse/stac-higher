/** Display helpers for the /monitoring surfaces (M2-D). */
import type { Alert, AlertState } from "@/lib/monitoring/api";

/** Compact relative time ("3m ago") for telemetry timestamps. */
export function timeAgo(isoValue: string | null | undefined): string {
  if (!isoValue) return "never";
  const then = Date.parse(isoValue);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = "B";
  for (const next of units) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${unit}`;
}

export const ALERT_STATE_VARIANT: Record<
  AlertState,
  "destructive" | "secondary" | "outline"
> = {
  firing: "destructive",
  acknowledged: "secondary",
  resolved: "outline",
};

/** Best-effort read of the pipeline-written flow_stats jsonb. */
export interface FlowStatsView {
  files: number;
  items: number;
  bytes: number;
  failed: number;
  lastActivityAt: string | null;
  lastErrorAt: string | null;
  lastLatencySeconds: number | null;
  counts: Record<string, number>;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function isoOrNull(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function readFlowStats(raw: Record<string, unknown>): FlowStatsView {
  const counts: Record<string, number> = {};
  if (raw.counts && typeof raw.counts === "object") {
    for (const [key, value] of Object.entries(raw.counts as object)) {
      if (typeof value === "number") counts[key] = value;
    }
  }
  return {
    files: num(raw.files),
    items: num(raw.items),
    bytes: num(raw.bytes),
    failed: num(raw.failed),
    lastActivityAt: isoOrNull(raw.last_activity_at),
    lastErrorAt: isoOrNull(raw.last_error_at),
    lastLatencySeconds:
      typeof raw.last_latency_seconds === "number"
        ? raw.last_latency_seconds
        : null,
    counts,
  };
}

/** The declared §5.1 window for the association's direction, if any. */
export function expectationWindow(
  direction: "ingest" | "deliver",
  expectation: Record<string, unknown> | null,
): number | null {
  if (!expectation) return null;
  const key =
    direction === "ingest"
      ? "expect_activity_within_seconds"
      : "deliver_within_seconds";
  const value = expectation[key];
  return typeof value === "number" && value >= 1 ? value : null;
}

/** The monitor kind that fires when a direction's declared window is blown. */
const EXPECTATION_BREACH_KIND: Record<"ingest" | "deliver", string> = {
  ingest: "ingest_inactivity",
  deliver: "delivery_slo",
};

/** True when the monitor holds an open expectation-breach alert for this
 * association — the alert row's own verdict (edited_at fallback, outstanding
 * deliveries and all), not a local re-derivation of it. */
export function isLate(
  direction: "ingest" | "deliver",
  associationId: string,
  openAlerts: Alert[],
): boolean {
  return openAlerts.some(
    (a) =>
      a.association_id === associationId &&
      a.kind === EXPECTATION_BREACH_KIND[direction] &&
      a.state !== "resolved",
  );
}
