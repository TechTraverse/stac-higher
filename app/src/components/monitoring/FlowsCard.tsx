/**
 * /monitoring per-association flow view (M2-D, spec §7): every association
 * the caller can see, with the pipeline-written flow_stats rollup — activity
 * recency, delivery latency, per-status delivery counts — and the declared
 * §5.1 expectation with a late/on-time hint derived from the open alerts
 * (the monitor's own verdict, so the hint and the alert list can't disagree).
 * Read-only: flow mutations live on the collection's Data-flow tab, which
 * each row links to.
 */
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingState,
} from "@stac-higher/shared";
import { ArrowDownToLine, ArrowUpFromLine, Waves } from "lucide-react";
import type { Association } from "@/lib/associations/types";
import type { Alert } from "@/lib/monitoring/api";
import { useAlerts, useFlows } from "@/lib/monitoring/queries";
import {
  expectationWindow,
  formatBytes,
  isLate,
  readFlowStats,
  timeAgo,
} from "./shared";

const COUNT_ORDER = ["delivered", "pending", "delivering", "failed", "dead"];

function FlowRow({
  flow,
  openAlerts,
}: {
  flow: Association;
  openAlerts: Alert[];
}) {
  const stats = readFlowStats(flow.flow_stats);
  const window = expectationWindow(flow.direction, flow.expectation);
  const late = isLate(flow.direction, flow.id, openAlerts);
  const DirectionIcon =
    flow.direction === "ingest" ? ArrowDownToLine : ArrowUpFromLine;

  return (
    <div
      data-testid={`flow-row-${flow.id}`}
      className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary">
            <DirectionIcon className="mr-1 h-3 w-3" />
            {flow.direction}
          </Badge>
          <a
            href={`/collections/${encodeURIComponent(flow.collection_id)}`}
            className="text-sm font-medium hover:underline"
          >
            {flow.collection_id}
          </a>
          <span className="text-xs text-muted-foreground">
            via {flow.connection.name ?? "(deleted connection)"}
          </span>
          {!flow.enabled && <Badge variant="outline">disabled</Badge>}
          {flow.connection.status === "error" && (
            <Badge variant="destructive">connection error</Badge>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {stats.files} files · {stats.items} items · {formatBytes(stats.bytes)}
          {stats.failed > 0 && ` · ${stats.failed} failed`}
          {" · last activity "}
          {timeAgo(stats.lastActivityAt)}
          {stats.lastLatencySeconds !== null &&
            ` · last latency ${stats.lastLatencySeconds.toFixed(1)}s`}
        </p>
        {flow.direction === "deliver" && (
          <div className="mt-1 flex flex-wrap gap-1">
            {COUNT_ORDER.filter((status) => (stats.counts[status] ?? 0) > 0).map(
              (status) => (
                <Badge
                  key={status}
                  variant={
                    status === "failed" || status === "dead"
                      ? "destructive"
                      : status === "delivered"
                        ? "default"
                        : "secondary"
                  }
                  className="text-[10px]"
                >
                  {status} {stats.counts[status]}
                </Badge>
              ),
            )}
          </div>
        )}
      </div>
      <div className="shrink-0 text-right">
        {window === null ? (
          <span className="text-xs text-muted-foreground">no expectation</span>
        ) : (
          <Badge variant={late ? "destructive" : "default"}>
            {late ? "late" : "on time"} ·{" "}
            {flow.direction === "ingest" ? "activity ≤" : "deliver ≤"} {window}s
          </Badge>
        )}
      </div>
    </div>
  );
}

export function FlowsCard() {
  const { data: flows, isLoading, isError, error, refetch } = useFlows();
  // Same query the alerts card polls, so the hint tracks it for free; while
  // it loads (or errors) the hint reads on-time, which the list then corrects.
  const openAlerts = useAlerts("open").data ?? [];

  return (
    <Card data-testid="flows-card">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Waves className="h-5 w-5" />
          Data flows
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingState message="Loading flows…" />
        ) : isError ? (
          <ErrorState
            message={error instanceof Error ? error.message : "Failed to load flows"}
            onRetry={() => refetch()}
          />
        ) : !flows || flows.length === 0 ? (
          <EmptyState
            icon={Waves}
            title="No data flows"
            description="Wire a connection to a collection from its Data flow tab to see ingest and delivery telemetry here."
          />
        ) : (
          <div className="grid gap-2">
            {flows.map((flow) => (
              <FlowRow key={flow.id} flow={flow} openAlerts={openAlerts} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
