import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@stac-higher/shared";
import { AlertTriangle, ArrowRight, CheckCircle2 } from "lucide-react";
import { useAlerts } from "@/lib/monitoring/queries";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";

/**
 * Platform overview rollup on the dashboard (ROADMAP §8, M5-F).
 *
 * The dashboard above this is about the CATALOG (is the API up, how many
 * collections). This strip is about the PLATFORM: what is wired and whether
 * it is healthy. Two reads serve it — the graph and the open alert list — and
 * both are already group-scoped, so the counts are the caller's own world
 * rather than a global total they cannot act on.
 *
 * It renders NOTHING when the platform surface is empty. A client-only user
 * browsing an external catalog should not be shown three zeroes and an
 * invitation to worry.
 */
export function PlatformRollup() {
  const { data: graph, isLoading } = usePipelineGraph();
  const { data: openAlerts } = useAlerts("open");

  const counts = {
    connections: graph?.nodes.filter((n) => n.type === "connection").length ?? 0,
    processes: graph?.nodes.filter((n) => n.type === "process").length ?? 0,
    flows: graph?.edges.length ?? 0,
  };
  const firing = (openAlerts ?? []).filter((a) => a.state === "firing").length;

  if (isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-3 mb-8">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );
  }
  // Nothing wired and nothing wrong: this user is not operating a platform.
  if (counts.connections === 0 && counts.processes === 0 && firing === 0) {
    return null;
  }

  return (
    <div className="grid gap-4 md:grid-cols-3 mb-8">
      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Data flows</CardDescription>
          <CardTitle>{counts.flows}</CardTitle>
        </CardHeader>
        <CardContent>
          <a
            href="/graph"
            className="text-xs text-primary hover:underline inline-flex items-center gap-1"
          >
            View the pipeline <ArrowRight className="h-3 w-3" />
          </a>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Processes</CardDescription>
          <CardTitle>{counts.processes}</CardTitle>
        </CardHeader>
        <CardContent>
          <a
            href="/processes"
            className="text-xs text-primary hover:underline inline-flex items-center gap-1"
          >
            Manage processes <ArrowRight className="h-3 w-3" />
          </a>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Open alerts</CardDescription>
          <CardTitle className="flex items-center gap-2">
            {firing > 0 ? (
              <>
                <AlertTriangle className="h-5 w-5 text-destructive" />
                {firing}
              </>
            ) : (
              <>
                <CheckCircle2 className="h-5 w-5 text-green-500" />
                All clear
              </>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center gap-2">
          <a
            href="/monitoring"
            className="text-xs text-primary hover:underline inline-flex items-center gap-1"
          >
            Monitoring <ArrowRight className="h-3 w-3" />
          </a>
          {(openAlerts ?? []).some((a) => a.state === "acknowledged") && (
            <Badge variant="secondary">some acknowledged</Badge>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
