/**
 * Process health, read off the RUN LEDGER (UI-5).
 *
 * Why runs and not `flow_stats_daily`: the daily rollup's `runs` / `failed` /
 * `dead` counters are deltas of a live jsonb whose inclusive-or-exclusive
 * semantics are not pinned anywhere the UI can rely on, whereas every row in
 * `/api/processes/[id]/runs` carries an unambiguous `status`. So the rate this
 * module reports is over the FETCHED WINDOW of runs, and the UI labels it that
 * way — "last N runs", never "30d", which would be a number we cannot stand
 * behind. The 30-day daily strip still exists; it lives on the detail page,
 * where its per-source meaning is visible.
 *
 * Process-anchored ALERTS are consulted when the caller passes the open list
 * (I-84: `/api/alerts` returns the effective `process_id`). An open alert is
 * the monitor's verdict (ADR 0010) and outranks the ledger: firing ⇒ error,
 * acknowledged ⇒ warning. Deployment state still comes first — a disabled
 * process is "unknown" whatever the monitor says about its past.
 *
 * C-4: a firing `process_image_flagged` is degraded (warn), not failing: the
 * image blocks new deploys while the process keeps running (container-images
 * spec §10).
 */
import type { LineageHealth } from "@stac-higher/shared";
import type { Alert } from "@/lib/monitoring/api";
import { DEGRADED_ALERT_KINDS, alertKindLabel, openAlertHealth } from "@/components/monitoring/shared";
import type { Process, ProcessRun, ProcessSource } from "@/lib/processes/types";

export interface ProcessVerdict {
  health: LineageHealth;
  label: string;
  /** One line saying why, or null when the label says it all. */
  reason: string | null;
}

/** Runs a human would call "a run" — test runs are excluded from health. */
export function realRuns(runs: ProcessRun[] | undefined): ProcessRun[] {
  return (runs ?? []).filter((r) => !r.is_test);
}

export function runDurationMs(run: ProcessRun): number | null {
  if (!run.started_at || !run.finished_at) return null;
  const ms = Date.parse(run.finished_at) - Date.parse(run.started_at);
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m ${rest}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * Success rate over the fetched window. `null` when nothing has reached a
 * terminal state — an unrun process is unmeasured, not perfect.
 */
export function successRateOverRuns(runs: ProcessRun[]): {
  rate: number | null;
  counted: number;
} {
  const terminal = runs.filter(
    (r) => r.status === "succeeded" || r.status === "dead",
  );
  if (terminal.length === 0) return { rate: null, counted: 0 };
  const ok = terminal.filter((r) => r.status === "succeeded").length;
  return { rate: (ok / terminal.length) * 100, counted: terminal.length };
}

export function processVerdict(
  process: Process,
  runs: ProcessRun[] | undefined,
  sourceCount: number | undefined,
  openAlerts?: Alert[] | undefined,
): ProcessVerdict {
  if (!process.current_revision) {
    return { health: "unknown", label: "No revision", reason: "nothing deployed" };
  }
  if (!process.enabled) {
    return { health: "unknown", label: "Disabled", reason: "will not trigger" };
  }
  if (sourceCount === 0) {
    return { health: "unknown", label: "No trigger", reason: "no source attached" };
  }

  const own = (openAlerts ?? []).filter((a) => a.process_id === process.id);
  const firingAlert = own.find((a) => openAlertHealth(a) === "error");
  const degradedAlert = own.find(
    (a) => a.state === "firing" && DEGRADED_ALERT_KINDS.has(a.kind),
  );
  const acknowledgedAlert = own.find((a) => a.state === "acknowledged");
  if (firingAlert) {
    return { health: "error", label: "Failing", reason: alertKindLabel(firingAlert.kind) };
  }
  if (degradedAlert) {
    return { health: "warn", label: "Degraded", reason: alertKindLabel(degradedAlert.kind) };
  }
  if (acknowledgedAlert) {
    return {
      health: "warn",
      label: "Degraded",
      reason: `${alertKindLabel(acknowledgedAlert.kind)} (acknowledged)`,
    };
  }

  const ledger = realRuns(runs);
  if (ledger.length === 0) {
    return { health: "unknown", label: "Never run", reason: null };
  }

  const dead = ledger.filter((r) => r.status === "dead").length;
  const failing = ledger.filter((r) => r.status === "failed").length;
  const deferred = ledger.filter((r) => r.rate_deferred_until !== null).length;

  if (dead > 0) {
    return {
      health: "error",
      label: "Failing",
      reason: `${dead} dead ${dead === 1 ? "run" : "runs"}`,
    };
  }
  if (failing > 0) {
    return {
      health: "warn",
      label: "Degraded",
      reason: `${failing} retrying`,
    };
  }
  if (deferred > 0) {
    return {
      health: "warn",
      label: "Rate limited",
      reason: `${deferred} deferred by the run ceiling`,
    };
  }
  return { health: "ok", label: "Healthy", reason: null };
}

/** Trigger summary for a card: the cron line, or the item-event phrasing. */
export function triggerSummary(
  sources: ProcessSource[] | undefined,
): { text: string; mono: boolean } {
  if (!sources || sources.length === 0) {
    return { text: "no trigger", mono: false };
  }
  const cron = sources
    .map((s) => {
      const trigger = s.trigger as Record<string, unknown> | null;
      const value = trigger?.cron;
      return typeof value === "string" ? value : null;
    })
    .find(Boolean);
  if (cron) return { text: cron, mono: true };
  return {
    text: sources.length === 1 ? "on new item" : `on new item · ${sources.length} sources`,
    mono: false,
  };
}
