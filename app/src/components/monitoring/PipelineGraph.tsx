import { useCallback, useMemo, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Card,
  CardContent,
  capLineage,
  EmptyState,
  ErrorState,
  Input,
  KIND_COLOR_VAR,
  lineage,
  LineageStrip,
  LoadingState,
  PipelineDag,
  type DagNodeDecoration,
  type LineageGroup,
  type LineageKind,
} from "@stac-higher/shared";
import { Search, Share2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAlerts } from "@/lib/monitoring/queries";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";
import {
  degreeMap,
  makeDecorator,
  nodeDetail,
  nodeHealth,
  nodeHref,
  unhealthyNodeIds,
} from "@/lib/monitoring/graph-decorate";
import type { GraphNode, PipelineGraph as Graph } from "@/lib/monitoring/graph-api";
import type { GraphEdge } from "@/lib/graph/edges";

/**
 * `/graph` — the pipeline, in two views (P-3, spec §5).
 *
 * **Pipelines** is one row per product: a searchable list where each row draws
 * that collection's WHOLE lineage — back to the source connection, forward to
 * the destinations — as a compact strip of real edges. It is the default
 * because "find the product I care about and read its chain" is the stated
 * common case (spec §9.1), and because a chained product (a derived product
 * that is the next process's source) appears in its own row and inside its
 * neighbours' rows, which a column layout cannot show at all.
 *
 * **Graph** is the whole platform at once. P-4 replaces it with the same
 * `PipelineDag` over the full graph; until then it keeps M5-F's five columns,
 * whose own design note explains what they do and do not claim: adjacency is
 * the direction of flow, NOT which node feeds which.
 *
 * The Flows list sits under both views as the text truth for edges.
 *
 * Health colouring joins the open-alert list client-side (see
 * `lib/monitoring/graph-decorate`), so one alert read serves the whole page.
 */

const LEGEND: Array<{ kind: LineageKind; label: string }> = [
  { kind: "connection", label: "Connection" },
  { kind: "collection", label: "Product" },
  { kind: "process", label: "Process" },
];

type View = "pipelines" | "graph";

/**
 * Spec §5: the view lives in `?view=` so an operator can link someone at the
 * picture they are looking at. `replaceState` rather than a navigation — this
 * is an island, and a reload would refetch the graph to show the same data.
 */
function initialView(): View {
  if (typeof window === "undefined") return "pipelines";
  return new URLSearchParams(window.location.search).get("view") === "graph"
    ? "graph"
    : "pipelines";
}

function useViewParam(): [View, (view: View) => void] {
  const [view, setView] = useState<View>(initialView);
  const update = useCallback((next: View) => {
    setView(next);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (next === "pipelines") url.searchParams.delete("view");
    else url.searchParams.set("view", next);
    window.history.replaceState(null, "", url);
  }, []);
  return [view, update];
}

/**
 * How many downstream nodes a row may draw before it starts to sprawl
 * (spec §8). Upstream is never capped — it is bounded by pipeline depth, and
 * it is the half the operator came for.
 */
const MAX_ROW_DOWNSTREAM = 8;

function LineageRow({
  graph,
  node,
  decorate,
}: {
  graph: Graph;
  node: GraphNode;
  decorate: (node: GraphNode) => DagNodeDecoration;
}) {
  const row = useMemo(
    () => capLineage(lineage(graph, node.id), MAX_ROW_DOWNSTREAM),
    [graph, node.id],
  );
  const wired = row.nodes.length > 1;

  return (
    <div className="border-t border-border py-3 first:border-t-0">
      <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <a
          href={nodeHref(node)}
          className="text-[13px] font-semibold hover:text-primary"
        >
          {node.label}
        </a>
        {row.hidden > 0 && (
          <span className="text-[11px] text-muted-foreground">
            +{row.hidden} more downstream — see the Graph view
          </span>
        )}
      </div>
      {wired ? (
        <PipelineDag
          graph={row}
          focus={node.id}
          decorate={decorate}
          label={`${node.label} lineage`}
        />
      ) : (
        <p className="text-xs text-muted-foreground/70">Not wired</p>
      )}
    </div>
  );
}

function PipelinesView({
  graph,
  decorate,
}: {
  graph: Graph;
  decorate: ReturnType<typeof makeDecorator>;
}) {
  const [search, setSearch] = useState("");

  const collections = useMemo(
    () =>
      graph.nodes
        .filter((node) => node.type === "collection")
        .sort((a, b) => a.label.localeCompare(b.label)),
    [graph.nodes],
  );

  const term = search.trim().toLowerCase();
  const matches = term
    ? collections.filter((node) => node.label.toLowerCase().includes(term))
    : collections;

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h2 className="text-sm font-bold">Pipelines</h2>
            <p className="text-xs text-muted-foreground">
              One row per product: everything upstream of it, and everything it
              feeds.
            </p>
          </div>
          <div className="relative w-full sm:w-64">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Find a product…"
              aria-label="Find a product"
              className="h-8 pl-8 text-[13px]"
            />
          </div>
        </div>

        {collections.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No products are wired to anything yet.
          </p>
        ) : matches.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No product matches “{search.trim()}”.
          </p>
        ) : (
          <div>
            {matches.map((node) => (
              <LineageRow
                key={node.id}
                graph={graph}
                node={node}
                decorate={decorate}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * M5-F's five columns, kept until P-4 replaces them with a full `PipelineDag`.
 *
 * Adjacency between columns is the direction of flow. It is NOT a claim about
 * which node feeds which — five columns cannot align arbitrary N:M wiring
 * without drawing edges that are wrong, which is exactly what the Pipelines
 * view and P-4 fix.
 */
function ColumnsView({
  graph,
  unhealthy,
  degree,
}: {
  graph: Graph;
  unhealthy: ReadonlySet<string>;
  degree: ReadonlyMap<string, number>;
}) {
  const groups = useMemo(() => {
    const edges = graph.edges;
    const ingestOut = new Set(
      edges.filter((e) => e.kind === "ingest").map((e) => e.from),
    );
    const deliverIn = new Set(
      edges.filter((e) => e.kind === "deliver").map((e) => e.to),
    );
    // An `extractor` edge does not make a collection derived: the extractor
    // fixes up items ingested INTO it.
    const derived = new Set(
      edges.filter((e) => e.kind === "process_output").map((e) => e.to),
    );

    const sourceConnections: GraphNode[] = [];
    const destinations: GraphNode[] = [];
    const sourceProducts: GraphNode[] = [];
    const derivedProducts: GraphNode[] = [];
    const processes: GraphNode[] = [];

    for (const node of graph.nodes) {
      if ((degree.get(node.id) ?? 0) === 0) continue;
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

    const byLabel = (a: GraphNode, b: GraphNode) => a.label.localeCompare(b.label);
    const toGroup = (
      kind: LineageKind,
      label: string,
      list: GraphNode[],
    ): LineageGroup => ({
      kind,
      label,
      nodes: list.sort(byLabel).map((node) => ({
        id: node.id,
        label: node.label,
        detail: nodeDetail(node, degree.get(node.id) ?? 0),
        health: nodeHealth(node, unhealthy),
        href: nodeHref(node),
      })),
    });

    return [
      toGroup("connection", "Source connections", sourceConnections),
      toGroup("collection", "Source products", sourceProducts),
      toGroup("process", "Processes", processes),
      toGroup("collection", "Derived products", derivedProducts),
      toGroup("connection", "Destinations", destinations),
    ];
  }, [graph, unhealthy, degree]);

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <LineageStrip size="full" groups={groups} />
      </CardContent>
    </Card>
  );
}

function FlowList({ edges, nodes }: { edges: GraphEdge[]; nodes: GraphNode[] }) {
  const label = new Map(nodes.map((n) => [n.id, n.label]));
  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h2 className="text-sm font-bold">Flows</h2>
          <p className="text-xs text-muted-foreground">
            Every wiring the platform owns, as text. A loop through a connection
            is not drawn as a cycle: those are not decidable, and the run-rate
            ceiling is what bounds them.
          </p>
        </div>
        {edges.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is wired yet.</p>
        ) : (
          <div className="grid gap-1.5">
            {edges.map((edge) => (
              <div
                key={`${edge.kind}:${edge.id}:${edge.from}`}
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
  const [view, setView] = useViewParam();

  const unhealthy = useMemo(() => unhealthyNodeIds(alerts ?? []), [alerts]);
  const degree = useMemo(() => degreeMap(graph?.edges ?? []), [graph?.edges]);
  const decorate = useMemo(
    () => makeDecorator(unhealthy, degree),
    [unhealthy, degree],
  );

  const orphans = useMemo(
    () =>
      (graph?.nodes ?? [])
        .filter((node) => (degree.get(node.id) ?? 0) === 0)
        .sort((a, b) => a.label.localeCompare(b.label)),
    [graph?.nodes, degree],
  );

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
        description="Connections, sources and destinations, and processes appear here once they exist."
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

      <Tabs
        value={view}
        onValueChange={(next) => setView(next as View)}
        className="space-y-5"
      >
        <TabsList>
          <TabsTrigger value="pipelines">Pipelines</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
        </TabsList>

        <TabsContent value="pipelines">
          <PipelinesView graph={graph} decorate={decorate} />
        </TabsContent>

        <TabsContent value="graph">
          <ColumnsView graph={graph} unhealthy={unhealthy} degree={degree} />
        </TabsContent>
      </Tabs>

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
            Find a product and read its whole chain, or look at the platform all
            at once.
          </p>
        </div>
        <GraphContent />
      </main>
    </AppShell>
  );
}

export type { Graph };
