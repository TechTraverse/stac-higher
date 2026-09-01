/**
 * /monitoring island (M2-D, spec §7): alert list with ack/resolve,
 * per-association flow telemetry, and notification-channel management.
 * Operator-only verbs render only for operator/admin identities (the server
 * guard enforces regardless).
 */
import { AppShell } from "@/components/layout/AppShell";
import { useAuthMe } from "@/lib/query/auth";
import { AlertsCard } from "./AlertsCard";
import { FlowsCard } from "./FlowsCard";
import { ChannelsCard } from "./ChannelsCard";

function MonitoringInner() {
  const { data: auth } = useAuthMe();
  const identity = auth?.authenticated ? auth.identity : null;
  const canAct =
    identity?.roles.some((role) => role === "operator" || role === "admin") ??
    false;

  return (
    <>
      <main className="w-full flex-1 space-y-5 p-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Monitoring</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Alerts, per-flow telemetry, and the channels that get notified
          </p>
        </div>
        <div className="grid gap-5">
          <AlertsCard canAct={canAct} />
          <FlowsCard />
          <ChannelsCard canAct={canAct} groups={identity?.groups ?? []} />
        </div>
      </main>
    </>
  );
}

export function MonitoringPage() {
  return (
    <AppShell>
      <MonitoringInner />
    </AppShell>
  );
}
