import { useMemo } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingState,
} from "@stac-higher/shared";
import { Share2 } from "lucide-react";
import { useAlerts } from "@/lib/monitoring/queries";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";
import type { GraphNode, PipelineGraph as Graph } from "@/lib/monitoring/graph-api";
import type { GraphEdge } from "@/lib/graph/edges";

/**
 * `/graph` — the pipeline as columns, not a force-directed blob (M5-F).
 *
 * The graph is a PIPELINE: sources flow left to right into collections,
 * through processes, out to destinations. A layout that respects that reads
 * at a glance; a physics simulation of the same data does not, and would cost
 * a rendering dependency to be less useful.
 *
 * Health colouring comes from the open-alert list, joined client-side exactly
 * like the M2-D flow hints — the graph endpoint deliberately does not embed
 * alert state, so one alert read serves the whole page.
 */

const COLUMN_TITLES: Record<GraphNode["type"], string> = {
  connection: "Connections",
  collection: "Collections",
  process: "Processes",
};

/** Alert kinds that indict a node, by the anchor the alert carries. */
function unhealthyNodeIds(
  alerts: { kind: string; connection_id?: string | null; collection_id?: string | null }[],
): Set<string> {
  const ids = new Set<string>();
  for (const alert of alerts) {
    if (alert.connection_id) ids.add(`conn:${alert.connection_id}`);
    if (alert.collection_id) ids.add(`coll:${alert.collection_id}`);
  }
  return ids;
}

function NodeCard({
  node,
  unhealthy,
  degree,
}: {
  node: GraphNode;
  unhealthy: boolean;
  degree: number;
}) {
  const href =
    node.type === "process"
      ? `/processes/${node.id.slice("proc:".length)}`
      : node.type === "collection"
        ? `/collections/${encodeURIComponent(node.id.slice("coll:".length))}`
        : "/connections";

  return (
    <a
      href={href}
      className={
        "block rounded-md border p-3 transition-colors hover:bg-accent " +
        (unhealthy ? "border-destructive" : "border-border")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium truncate">{node.label}</span>
        {unhealthy && <Badge variant="destructive">alert</Badge>}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
        {typeof node.meta.protocol === "string" && <span>{node.meta.protocol}</span>}
        {node.meta.archived === true && <span>archived</span>}
        {node.meta.enabled === false && <span>disabled</span>}
        {node.meta.deployed === false && <span>no revision</span>}
        {/* Degree is the useful structural fact a column layout loses: an
            isolated node is wired to nothing, which is usually a mistake. */}
        <span>
          {degree} {degree === 1 ? "connection" : "connections"}
        </span>
      </div>
    </a>
  );
}

function EdgeList({ edges, nodes }: { edges: GraphEdge[]; nodes: GraphNode[] }) {
  const label = new Map(nodes.map((n) => [n.id, n.label]));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Flows</CardTitle>
        <CardDescription>
          Every wiring the platform owns. A loop through a connection is not
          drawn as a cycle — those are not decidable, and the run-rate ceiling
          is what bounds them.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {edges.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing is wired yet.</p>
        )}
        {edges.map((edge) => (
          <div
            key={`${edge.kind}:${edge.id}`}
            className="flex items-center gap-2 text-sm"
          >
            <span className="truncate">{label.get(edge.from) ?? edge.from}</span>
            <span className="text-muted-foreground">→</span>
            <span className="truncate">{label.get(edge.to) ?? edge.to}</span>
            <Badge variant="outline">{edge.kind.replace("_", " ")}</Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function GraphContent() {
  const { data: graph, isLoading, error, refetch } = usePipelineGraph();
  const { data: alerts } = useAlerts("open");

  const unhealthy = useMemo(
    () => unhealthyNodeIds(alerts ?? []),
    [alerts],
  );
  const degrees = useMemo(() => {
    const counts = new Map<string, number>();
    for (const edge of graph?.edges ?? []) {
      counts.set(edge.from, (counts.get(edge.from) ?? 0) + 1);
      counts.set(edge.to, (counts.get(edge.to) ?? 0) + 1);
    }
    return counts;
  }, [graph]);

  const columns = useMemo(() => {
    const empty: Record<GraphNode["type"], GraphNode[]> = {
      connection: [],
      collection: [],
      process: [],
    };
    for (const node of graph?.nodes ?? []) empty[node.type].push(node);
    for (const list of Object.values(empty)) {
      list.sort((a, b) => a.label.localeCompare(b.label));
    }
    return empty;
  }, [graph]);

  if (isLoading) return <LoadingState message="Loading the pipeline graph…" />;
  if (error) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : "Failed to load"}
        onRetry={() => refetch()}
      />
    );
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <EmptyState
        icon={Share2}
        title="Nothing wired yet"
        description="Connections, ingest and delivery associations, and processes appear here once they exist."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        {(["connection", "collection", "process"] as const).map((type) => (
          <Card key={type}>
            <CardHeader>
              <CardTitle>{COLUMN_TITLES[type]}</CardTitle>
              <CardDescription>{columns[type].length} in view</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2">
              {columns[type].length === 0 && (
                <p className="text-sm text-muted-foreground">None.</p>
              )}
              {columns[type].map((node) => (
                <NodeCard
                  key={node.id}
                  node={node}
                  unhealthy={unhealthy.has(node.id)}
                  degree={degrees.get(node.id) ?? 0}
                />
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
      <EdgeList edges={graph.edges} nodes={graph.nodes} />
    </div>
  );
}

export function PipelineGraphPage() {
  return (
    <AppShell>
      <div className="container mx-auto px-4 py-8 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Pipeline</h1>
          <p className="text-muted-foreground">
            How data moves: sources into collections, through processes, out to
            destinations.
          </p>
        </div>
        <GraphContent />
      </div>
    </AppShell>
  );
}

export type { Graph };
