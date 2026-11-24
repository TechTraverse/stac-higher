/**
 * Read-only status block of the Virtual cube sink card (Z-8b): the
 * `cubeSinkHealth` badge and its reasons, ledger counts, last error, last
 * snapshot and last maintenance (docs/monitoring.md "Cube repositories").
 */
import { Badge } from "@stac-higher/shared";
import {
  CUBE_APPEND_STATUSES,
  CUBE_APPEND_STATUS_LABEL,
  CUBE_MAINTENANCE_ATTENTION_LABEL,
  CUBE_MAINTENANCE_STATUS_LABEL,
  CUBE_PUBLISH_OUTCOME_LABEL,
  CUBE_SINK_HEALTH_LABEL,
  cubeLedgerReasonLabel,
  cubeSinkHealth,
} from "@/lib/cubes/status";
import type { CubeSinkView } from "@/lib/cubes/queries";
import { formatBytes, timeAgo } from "@/components/monitoring/shared";

const HEALTH_REASON_LABEL: Record<string, string> = {
  ...CUBE_MAINTENANCE_ATTENTION_LABEL,
  last_error: "Last append failed",
  maintenance_failed: "Last maintenance failed",
  failed_rows: "Recent ledger rows failed",
  no_append_within_window: "Nothing appended within the window's max age",
};

const lookup = (map: Record<string, string>, key: string) =>
  Object.hasOwn(map, key) ? map[key] : key;

const HEALTH_VARIANT = {
  ok: "default",
  waiting: "secondary",
  disabled: "outline",
  stale: "secondary",
  attention: "secondary",
  error: "destructive",
} as const;

export function CubeSinkStatus({ view }: { view: CubeSinkView }) {
  const { sink, ledger } = view;
  const health = cubeSinkHealth(sink, ledger);
  const m = sink.last_maintenance;
  return (
    <div className="grid gap-3 text-sm" data-testid="cube-sink-status">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={HEALTH_VARIANT[health.state]}
          data-testid="cube-sink-health"
          data-state={health.state}
        >
          {CUBE_SINK_HEALTH_LABEL[health.state]}
        </Badge>
        <span className="text-muted-foreground">
          Source{" "}
          <a
            className="text-primary hover:underline"
            href={`/collections/${encodeURIComponent(sink.source_collection_id)}`}
          >
            {sink.source_collection_id}
          </a>
        </span>
      </div>

      {health.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-xs" data-testid="cube-sink-reasons">
          {health.reasons.map((r) => (
            <li key={r}>{lookup(HEALTH_REASON_LABEL, r)}</li>
          ))}
        </ul>
      )}

      <dl
        className="grid grid-cols-4 gap-2 text-xs"
        data-testid="cube-sink-counts"
      >
        {CUBE_APPEND_STATUSES.map((s) => (
          <div key={s} className="rounded-md border p-2">
            <dt className="text-muted-foreground">
              {CUBE_APPEND_STATUS_LABEL[s]}
            </dt>
            <dd className="text-base font-medium">{ledger.counts[s] ?? 0}</dd>
          </div>
        ))}
      </dl>

      {sink.last_error && (
        <pre
          className="whitespace-pre-wrap rounded-md border border-destructive/40 p-2 text-xs text-destructive"
          data-testid="cube-sink-last-error"
        >
          {sink.last_error}
        </pre>
      )}

      <p data-testid="cube-sink-snapshot">
        Last snapshot:{" "}
        {sink.last_snapshot_id ? (
          <>
            <code>{sink.last_snapshot_id.slice(0, 12)}</code>,{" "}
            {timeAgo(sink.last_appended_at)}
          </>
        ) : (
          <span className="text-muted-foreground">none yet</span>
        )}
      </p>

      {m && (
        <p data-testid="cube-sink-maintenance">
          Last maintenance: {lookup(CUBE_MAINTENANCE_STATUS_LABEL, m.status)},{" "}
          {timeAgo(m.finished_at ?? m.started_at)}
          {m.publish_outcome
            ? `, ${lookup(CUBE_PUBLISH_OUTCOME_LABEL, m.publish_outcome)}`
            : ""}
          {m.gc ? `, ${formatBytes(m.gc.bytes_deleted)} deleted` : ""}
          {m.total_bytes !== undefined
            ? `, ${formatBytes(m.total_bytes)}${
                m.warn_bytes !== undefined
                  ? ` of ${formatBytes(m.warn_bytes)}`
                  : ""
              }`
            : ", Size readout pending"}
        </p>
      )}

      {ledger.recent.length > 0 && (
        <ul className="grid gap-1 text-xs" data-testid="cube-sink-recent">
          {ledger.recent.slice(0, 10).map((r) => {
            const reason = cubeLedgerReasonLabel(r.status, r.reason);
            return (
              <li key={r.item_id} className="flex flex-wrap gap-x-2">
                <code>{r.item_id}</code>
                <span>{CUBE_APPEND_STATUS_LABEL[r.status]}</span>
                {reason && (
                  <span className="text-muted-foreground">{reason}</span>
                )}
                <span className="text-muted-foreground">
                  {timeAgo(r.updated_at)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
