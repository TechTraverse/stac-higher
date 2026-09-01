import { useMemo } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  KIND_COLOR_VAR,
  LineageStrip,
  LoadingState,
  type LineageGroup,
  type LineageHealth,
  type LineageKind,
  type LineageNode,
} from "@stac-higher/shared";
import { Share2 } from "lucide-react";
import { useAlerts } from "@/lib/monitoring/queries";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";
import type { GraphNode, PipelineGraph as Graph } from "@/lib/monitoring/graph-api";
import type { GraphEdge } from "@/lib/graph/edges";

/**
 * `/graph` — the pipeline as columns, not a force-directed blob (M5-F, restyled
 * UI-7 into the ADR 0017 lineage language).
 *
 * The graph is a PIPELINE: source connections feed products, processes derive
 * new products, destinations receive them. A layout that respects that reads at
 * a glance; a physics simulation of the same data does not, and would cost a
 * rendering dependency to be less useful.
 *
 * **What the columns do and do not claim.** Adjacency between columns is the
 * direction of flow. It is NOT a claim about which node feeds which — five
 * columns cannot align arbitrary N:M wiring without drawing edges that are
 * wrong. The exact wiring stays in the Flows list underneath, which is the
 * page's source of truth for edges; the columns are the shape.
 *
 * Health colouring comes from the open-alert list, joined client-side exactly
 * like the M2-D flow hints — the graph endpoint deliberately does not embed
 * alert state, so one alert read serves the whole page. Process-anchored alerts
 * carry no id the client can read (`/api/alerts` omits `process_id`), so a
 * process node's health is "unknown" rather than a guess.
 */

const LEGEND: Array<{ kind: LineageKind; label: string }> = [
  { kind: "connection", label: "Connection" },
  { kind: "collection", label: "Product" },
  { kind: "process", label: "Process" },
];

/** Alert kinds that indict a node, by the anchor the alert carries. */
function unhealthyNodeIds(
  alerts: {
    kind: string;
    connection_id?: string | null;
    collection_id?: string | null;
  }[],
): Set<string> {
  const ids = new Set<string>();
  for (const alert of alerts) {
    if (alert.connection_id) ids.add(`conn:${alert.connection_id}`);
    if (alert.collection_id) ids.add(`coll:${alert.collection_id}`);
  }
  return ids;
}

function nodeHref(node: GraphNode): string {
  if (node.type === "process") return `/processes/${node.id.slice("proc:".length)}`;
  if (node.type === "collection") {
    return `/collections/${encodeURIComponent(node.id.slice("coll:".length))}`;
  }
  return "/connections";
}

/** The mono second line: the id plus whatever the node's meta actually says. */
function nodeDetail(node: GraphNode, degree: number): string {
  const bits: string[] = [];
  if (node.type === "connection" && typeof node.meta.protocol === "string") {
    bits.push(node.meta.protocol);
  }
  if (node.meta.archived === true) bits.push("archived");
  if (node.meta.enabled === false) bits.push("disabled");
  if (node.meta.deployed === false) bits.push("no revision");
  // Degree is the structural fact a column layout loses: an isolated node is
  // wired to nothing, which is usually a mistake.
  bits.push(`${degree} ${degree === 1 ? "edge" : "edges"}`);
  return bits.join(" · ");
}

function toLineageNode(
  node: GraphNode,
  unhealthy: Set<string>,
  degree: number,
): LineageNode {
  const health: LineageHealth = unhealthy.has(node.id)
    ? "error"
    : node.meta.archived === true ||
        node.meta.enabled === false ||
        node.meta.deployed === false
      ? "warn"
      : node.type === "process"
        ? // No client-readable process anchor on alerts — see the file note.
          "unknown"
        : "ok";
  return {
    id: node.id,
    label: node.label,
    detail: nodeDetail(node, degree),
    health,
    href: nodeHref(node),
  };
}

function FlowList({ edges, nodes }: { edges: GraphEdge[]; nodes: GraphNode[] }) {
  const label = new Map(nodes.map((n) => [n.id, n.label]));
  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h2 className="text-sm font-bold">Flows</h2>
          <p className="text-xs text-muted-foreground">
            Every wiring the platform owns — the exact edges the columns above
            only imply. A loop through a connection is not drawn as a cycle:
            those are not decidable, and the run-rate ceiling is what bounds
            them.
          </p>
        </div>
        {edges.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is wired yet.</p>
        ) : (
          <div className="grid gap-1.5">
            {edges.map((edge) => (
              <div
                key={`${edge.kind}:${edge.id}`}
                className="flex flex-wrap items-center gap-2 text-[13px]"
              >
                <span className="truncate font-medium">
                  {label.get(edge.from) ?? edge.from}
                </span>
                <span className="text-muted-foreground">→</span>
                <span className="truncate font-medium">
                  {label.get(edge.to) ?? edge.to}
                </span>
                <Badge variant="outline" className="text-[10.5px]">
                  {edge.kind.replace("_", " ")}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GraphContent() {
  const { data: graph, isLoading, error, refetch } = usePipelineGraph();
  const { data: alerts } = useAlerts("open");

  const unhealthy = useMemo(() => unhealthyNodeIds(alerts ?? []), [alerts]);

  const { groups, orphans } = useMemo(() => {
    const nodes = graph?.nodes ?? [];
    const edges = graph?.edges ?? [];

    const degree = new Map<string, number>();
    for (const edge of edges) {
      degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
      degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
    }

    const ingestOut = new Set(
      edges.filter((e) => e.kind === "ingest").map((e) => e.from),
    );
    const deliverIn = new Set(
      edges.filter((e) => e.kind === "deliver").map((e) => e.to),
    );
    const derived = new Set(
      edges.filter((e) => e.kind === "process_output").map((e) => e.to),
    );

    const sourceConnections: GraphNode[] = [];
    const destinations: GraphNode[] = [];
    const sourceProducts: GraphNode[] = [];
    const derivedProducts: GraphNode[] = [];
    const processes: GraphNode[] = [];
    const unwired: GraphNode[] = [];

    for (const node of nodes) {
      if ((degree.get(node.id) ?? 0) === 0) {
        unwired.push(node);
        continue;
      }
      if (node.type === "process") {
        processes.push(node);
        continue;
      }
      if (node.type === "connection") {
        // A connection wired both ways legitimately appears in BOTH columns —
        // that is what "used as a source and a destination" looks like.
        if (ingestOut.has(node.id)) sourceConnections.push(node);
        if (deliverIn.has(node.id)) destinations.push(node);
        continue;
      }
      if (derived.has(node.id)) derivedProducts.push(node);
      else sourceProducts.push(node);
    }

    const byLabel = (a: GraphNode, b: GraphNode) =>
      a.label.localeCompare(b.label);
    const toGroup = (
      kind: LineageKind,
      label: string,
      list: GraphNode[],
    ): LineageGroup => ({
      kind,
      label,
      nodes: list
        .sort(byLabel)
        .map((n) => toLineageNode(n, unhealthy, degree.get(n.id) ?? 0)),
    });

    return {
      groups: [
        toGroup("connection", "Source connections", sourceConnections),
        toGroup("collection", "Source products", sourceProducts),
        toGroup("process", "Processes", processes),
        toGroup("collection", "Derived products", derivedProducts),
        toGroup("connection", "Destinations", destinations),
      ],
      orphans: unwired.sort(byLabel),
    };
  }, [graph, unhealthy]);

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
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {LEGEND.map(({ kind, label }) => (
          <span key={kind} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: KIND_COLOR_VAR[kind] }}
            />
            <span className="text-[12.5px] font-medium">{label}</span>
          </span>
        ))}
        <span className="text-[11.5px] text-muted-foreground">
          the dot on each node is health; the bar is type
        </span>
      </div>

      <Card>
        <CardContent className="px-5 py-4">
          <LineageStrip size="full" groups={groups} />
        </CardContent>
      </Card>

      {orphans.length > 0 && (
        <Card>
          <CardContent className="px-5 py-4">
            <div className="mb-2 flex flex-wrap items-baseline gap-x-2.5">
              <h2 className="text-sm font-bold">Not wired</h2>
              <p className="text-xs text-muted-foreground">
                These exist but nothing flows through them — usually a
                half-finished setup.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {orphans.map((node) => (
                <a
                  key={node.id}
                  href={nodeHref(node)}
                  className="rounded-sm border border-dashed border-border px-2.5 py-1.5 text-[12.5px] transition-colors hover:border-primary/50"
                >
                  {node.label}
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <FlowList edges={graph.edges} nodes={graph.nodes} />
    </div>
  );
}

export function PipelineGraphPage() {
  return (
    <AppShell>
      <main className="flex-1 space-y-5 p-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Pipeline graph</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Sources → products → processes → derived products → destinations.
          </p>
        </div>
        <GraphContent />
      </main>
    </AppShell>
  );
}

export type { Graph };
