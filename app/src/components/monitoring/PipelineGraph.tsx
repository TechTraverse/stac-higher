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
  LoadingState,
  PipelineDag,
  type DagNodeDecoration,
  type LineageKind,
} from "@stac-higher/shared";
import { ExternalLink, Search, Share2, X } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAlerts } from "@/lib/monitoring/queries";
import { ALERTS_PAGE_LIMIT } from "@/lib/monitoring/api";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";
import {
  degreeMap,
  makeDecorator,
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
 * **Graph** is the whole platform at once, the same `PipelineDag` over the
 * whole group-scoped graph (P-4). It replaced M5-F's five columns, whose own
 * design note said what they could not do: five columns cannot align arbitrary
 * N:M wiring without drawing edges that are wrong, so adjacency there was the
 * direction of flow and never a claim about which node feeds which. Clicking a
 * node fades everything outside its lineage — a whole-platform picture is
 * legible at a glance but not readable node by node without one.
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
 * The whole platform in one picture (P-4, spec §5.2).
 *
 * Degree-0 nodes are deliberately NOT in the SVG: a floating chip attached to
 * nothing reads as a layout bug rather than as a fact about the platform, so
 * they get their own "Not wired" row beneath (spec §9.4).
 *
 * Clicking a node fades everything outside `lineage(node)` and offers an Open
 * link — the chip itself becomes a button while a selection is possible, so
 * the link lives in the selection bar rather than in the chip, where it would
 * swallow a middle-click.
 */
function GraphView({
  graph,
  decorate,
}: {
  graph: Graph;
  decorate: (node: GraphNode) => DagNodeDecoration;
}) {
  const [selected, setSelected] = useState<GraphNode | null>(null);

  const wired = useMemo(() => {
    const degree = degreeMap(graph.edges);
    return {
      nodes: graph.nodes.filter((node) => (degree.get(node.id) ?? 0) > 0),
      edges: graph.edges,
    };
  }, [graph]);

  const highlight = useMemo(() => {
    if (!selected) return null;
    return new Set(lineage(wired, selected.id).nodes.map((node) => node.id));
  }, [wired, selected]);

  if (wired.nodes.length === 0) {
    return (
      <Card>
        <CardContent className="px-5 py-4">
          <p className="text-sm text-muted-foreground">
            Nothing is wired yet — every node below is an island.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h2 className="text-sm font-bold">Graph</h2>
            <p className="text-xs text-muted-foreground">
              Every wiring at once. Click a node to follow its chain.
            </p>
          </div>
          {selected && (
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="font-medium">{selected.label}</span>
              <a
                href={nodeHref(selected)}
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                Open <ExternalLink aria-hidden="true" className="h-3 w-3" />
              </a>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              >
                Clear <X aria-hidden="true" className="h-3 w-3" />
              </button>
            </div>
          )}
        </div>
        <PipelineDag
          graph={wired}
          size="full"
          decorate={decorate}
          focus={selected?.id}
          highlight={highlight}
          onNodeClick={setSelected}
          onBackgroundClick={() => setSelected(null)}
          label="The whole pipeline graph"
        />
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
  const {
    data: alerts,
    isLoading: alertsLoading,
    isError: alertsError,
  } = useAlerts("open");
  const [view, setView] = useViewParam();

  // "Healthy" is only claimed on a loaded, untruncated alert list — the same
  // evidence rule `overview.ts` / the dashboard use (I-84 fix-round-1).
  const alertsAreComplete =
    !alertsLoading && !alertsError && (alerts?.length ?? 0) < ALERTS_PAGE_LIMIT;
  const unhealthy = useMemo(() => unhealthyNodeIds(alerts ?? []), [alerts]);
  const degree = useMemo(() => degreeMap(graph?.edges ?? []), [graph?.edges]);
  const decorate = useMemo(
    () => makeDecorator(unhealthy, degree, alertsAreComplete),
    [unhealthy, degree, alertsAreComplete],
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
          <GraphView graph={graph} decorate={decorate} />
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
