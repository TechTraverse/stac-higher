/**
 * /monitoring alert list (M2-D, spec §7): open (firing + acknowledged) and
 * resolved views with the audited operator ack/resolve verbs. Mounting the
 * card advances the caller's read watermark (M2-C), which zeroes the header
 * bell.
 */
import { useEffect, useRef, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingState,
} from "@stac-higher/shared";
import { BellOff, Check, CheckCheck, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import type { Alert } from "@/lib/monitoring/api";
import {
  useAckAlert,
  useAlerts,
  useMarkAlertsRead,
  useResolveAlert,
} from "@/lib/monitoring/queries";
import { ALERT_STATE_VARIANT, alertKindLabel, timeAgo } from "./shared";

function AlertRow({ alert, canAct }: { alert: Alert; canAct: boolean }) {
  const ack = useAckAlert();
  const resolve = useResolveAlert();

  const context = [
    alert.connection_name && `connection ${alert.connection_name}`,
    alert.collection_id && `collection ${alert.collection_id}`,
    alert.group_id && `group ${alert.group_id}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      data-testid={`alert-row-${alert.id}`}
      className="flex items-start justify-between gap-3 rounded-md border border-border p-3"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={ALERT_STATE_VARIANT[alert.state]}>{alert.state}</Badge>
          <Badge variant="outline">{alert.source}</Badge>
          <span className="text-xs font-medium text-muted-foreground">
            {alertKindLabel(alert.kind)}
          </span>
        </div>
        <p className="mt-1 break-words text-sm">{alert.message}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {context || "context unavailable"} · first {timeAgo(alert.first_seen)} ·
          last {timeAgo(alert.last_seen)}
          {alert.acknowledged_by && ` · ack by ${alert.acknowledged_by}`}
        </p>
      </div>
      {canAct && alert.state !== "resolved" && (
        <div className="flex shrink-0 gap-1.5">
          {alert.state === "firing" && (
            <Button
              variant="outline"
              size="sm"
              disabled={ack.isPending}
              onClick={() =>
                ack.mutate(alert.id, {
                  onSuccess: () => toast.success("Alert acknowledged"),
                  onError: (err) => toast.error(`Ack failed: ${err.message}`),
                })
              }
            >
              <Check className="mr-1 h-3.5 w-3.5" />
              Ack
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={resolve.isPending}
            onClick={() =>
              resolve.mutate(alert.id, {
                onSuccess: () => toast.success("Alert resolved"),
                onError: (err) => toast.error(`Resolve failed: ${err.message}`),
              })
            }
          >
            <CheckCheck className="mr-1 h-3.5 w-3.5" />
            Resolve
          </Button>
        </div>
      )}
    </div>
  );
}

export function AlertsCard({ canAct }: { canAct: boolean }) {
  const [view, setView] = useState<"open" | "resolved">("open");
  const { data: alerts, isLoading, isError, error, refetch } = useAlerts(view);
  const markRead = useMarkAlertsRead();

  // Opening the page = reading the bell. Once per mount (the ref guards
  // against effect re-runs; the mutation identity is render-scoped).
  const marked = useRef(false);
  useEffect(() => {
    if (!marked.current) {
      marked.current = true;
      markRead.mutate();
    }
  });

  return (
    <Card data-testid="alerts-card">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <ShieldAlert className="h-5 w-5" />
          Alerts
        </CardTitle>
        <div className="flex gap-1">
          <Button
            variant={view === "open" ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setView("open")}
          >
            Open
          </Button>
          <Button
            variant={view === "resolved" ? "secondary" : "ghost"}
            size="sm"
            data-testid="alerts-view-resolved"
            onClick={() => setView("resolved")}
          >
            Resolved
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingState message="Loading alerts…" />
        ) : isError ? (
          <ErrorState
            message={error instanceof Error ? error.message : "Failed to load alerts"}
            onRetry={() => refetch()}
          />
        ) : !alerts || alerts.length === 0 ? (
          <EmptyState
            icon={BellOff}
            title={view === "open" ? "No open alerts" : "No resolved alerts"}
            description={
              view === "open"
                ? "Flows are inside their declared expectations and every connection is healthy."
                : "Nothing has been resolved yet."
            }
          />
        ) : (
          <div className="grid gap-2">
            {alerts.map((alert) => (
              <AlertRow key={alert.id} alert={alert} canAct={canAct} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
