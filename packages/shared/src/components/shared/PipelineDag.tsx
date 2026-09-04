/**
 * `PipelineDag` — the renderer for a laid-out pipeline graph (P-3, spec §6).
 *
 * Takes the output of `layeredLayout` and draws the REAL edges: every kind the
 * graph API returns, between the specific nodes they join. That is the whole
 * point of the P queue — `LineageStrip size="full"` showed COLUMN adjacency and
 * said so in its own design note, which could not make an extractor legible or
 * tell two pipelines apart (spec §1).
 *
 * **Edges are SVG, nodes are HTML.** The `<svg>` layer draws one `<path>` per
 * edge, absolutely positioned under absolutely-positioned node chips. Nodes in
 * SVG would mean `<text>` with hand-rolled truncation at a guessed character
 * width, and would lose links, hover styling and keyboard focus; this way the
 * chips keep the ADR 0017 lineage visuals (type bar, health dot) exactly as
 * `LineageStrip` renders them, and the geometry still comes from one pure,
 * tested function.
 *
 * Presentational and app-agnostic, like `LineageStrip`: health, links and the
 * detail line are decided by the caller through `decorate`, so this component
 * can never disagree with the monitoring surfaces about what "healthy" means.
 */
import { useId, useMemo } from "react";
import { cn } from "@shared/lib/utils";
import {
  layeredLayout,
  type LayoutOptions,
  type PlacedEdge,
  type PlacedNode,
} from "@shared/lib/graph/layout";
import type { Graph, GraphEdgeKind, GraphNode } from "@shared/lib/graph/types";
import {
  healthDotClass,
  KIND_COLOR_VAR,
  type LineageHealth,
  type LineageKind,
} from "./LineageStrip";

/** What the app knows about a node that the graph payload does not. */
export interface DagNodeDecoration {
  health?: LineageHealth;
  href?: string;
  /** Mono second line. Only drawn at `size="full"`. */
  detail?: string;
}

export type PipelineDagSize = "compact" | "full";

export interface PipelineDagProps {
  graph: Graph;
  /**
   * `compact` is the one-row lineage strip (the Pipelines list, the collection
   * page); `full` is the whole-platform picture. Presets rather than raw
   * numbers so the layout memo has a stable dependency.
   */
  size?: PipelineDagSize;
  layout?: LayoutOptions;
  decorate?: (node: GraphNode) => DagNodeDecoration;
  /** Drawn outlined — the product whose lineage this is. */
  focus?: string;
  /** When set, ids outside it fade. `null` = nothing dimmed. */
  highlight?: ReadonlySet<string> | null;
  onNodeClick?: (node: GraphNode) => void;
  onBackgroundClick?: () => void;
  label?: string;
  className?: string;
}

// `compact` is wide enough that a badged node still shows a usable prefix of
// its label: an extractor chip spends ~50px on the badge, and at 148px
// `goes-abi-metadata` rendered as "goes-…".
const SIZE_PRESETS: Record<PipelineDagSize, LayoutOptions> = {
  compact: { nodeWidth: 184, nodeHeight: 34, gapX: 34, gapY: 8, elbow: 12 },
  full: { nodeWidth: 190, nodeHeight: 50, gapX: 60, gapY: 16, elbow: 16 },
};

const EDGE_LABEL: Record<GraphEdgeKind, string> = {
  ingest: "ingest",
  deliver: "deliver",
  process_source: "process source",
  process_output: "process output",
  extractor: "extractor",
};

/**
 * An `extractor` edge is dashed on purpose: it runs process → collection like
 * a `process_output` but means the opposite thing — the process fixes up items
 * an ingest association is already writing INTO that collection, so it points
 * at the product rather than out of it (GOES spec §15).
 */
const EDGE_DASH: Partial<Record<GraphEdgeKind, string>> = {
  extractor: "5 3",
};

function edgeStroke(placed: PlacedEdge): string {
  if (placed.back) return "var(--color-warning)";
  if (placed.edge.kind === "extractor") return KIND_COLOR_VAR.process;
  return "var(--color-muted-foreground)";
}

function Chip({
  placed,
  size,
  decoration,
  badge,
  focused,
  dimmed,
  onClick,
}: {
  placed: PlacedNode;
  size: PipelineDagSize;
  decoration: DagNodeDecoration;
  badge: string | null;
  focused: boolean;
  dimmed: boolean;
  onClick?: (node: GraphNode) => void;
}) {
  const kind = placed.node.type as LineageKind;
  const classes = cn(
    "absolute flex items-center gap-1.5 overflow-hidden rounded-md border border-l-2 border-border bg-card px-2 text-left transition-opacity",
    size === "full" ? "py-1.5" : "py-1",
    focused && "border-primary ring-1 ring-primary/40",
    dimmed && "opacity-25",
    (decoration.href || onClick) && "hover:border-primary/60 hover:bg-accent",
  );
  const style = {
    left: placed.x,
    top: placed.y,
    width: placed.width,
    height: placed.height,
    borderLeftColor: KIND_COLOR_VAR[kind],
  };
  const inner = (
    <>
      <span
        aria-hidden="true"
        className={cn(
          "inline-block h-2 w-2 shrink-0 rounded-full",
          healthDotClass(decoration.health),
        )}
      />
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            "block truncate font-medium",
            size === "full" ? "text-[12.5px]" : "text-[11.5px]",
          )}
        >
          {placed.node.label}
        </span>
        {size === "full" && decoration.detail && (
          <span className="tech block truncate text-[10px] text-muted-foreground">
            {decoration.detail}
          </span>
        )}
      </span>
      {badge && (
        <span
          data-node-badge={badge}
          className="shrink-0 rounded-sm border border-border px-1 text-[8.5px] font-bold uppercase tracking-tight text-muted-foreground"
        >
          {badge}
        </span>
      )}
    </>
  );

  // A node with a click handler is a button (the Graph view's highlight); one
  // with only an href is a link. Never both — a link that also swallows the
  // click cannot be opened in a new tab.
  const title = decoration.detail
    ? `${placed.node.label} — ${decoration.detail}`
    : placed.node.label;
  if (onClick) {
    return (
      <button
        type="button"
        title={title}
        style={style}
        className={classes}
        onClick={(event) => {
          event.stopPropagation();
          onClick(placed.node);
        }}
      >
        {inner}
      </button>
    );
  }
  return decoration.href ? (
    <a href={decoration.href} title={title} style={style} className={classes}>
      {inner}
    </a>
  ) : (
    <div title={title} style={style} className={classes}>
      {inner}
    </div>
  );
}

export function PipelineDag({
  graph,
  size = "compact",
  layout: overrides,
  decorate,
  focus,
  highlight = null,
  onNodeClick,
  onBackgroundClick,
  label,
  className,
}: PipelineDagProps) {
  // Marker ids must be unique per instance: the Pipelines view renders one dag
  // per collection, and duplicate ids would point every arrowhead at the first.
  const markerPrefix = useId().replace(/[^\w-]/g, "");
  const placed = useMemo(
    () => layeredLayout(graph, { ...SIZE_PRESETS[size], ...overrides }),
    [graph, size, overrides],
  );

  /** Badge the source of an extractor edge, so its role reads without hover. */
  const badges = useMemo(() => {
    const map = new Map<string, string>();
    for (const edge of graph.edges) {
      if (edge.kind === "extractor") map.set(edge.from, "extractor");
    }
    return map;
  }, [graph.edges]);

  if (placed.nodes.length === 0) return null;

  const dimmed = (id: string) => highlight !== null && !highlight.has(id);
  // Arrowheads are one marker per stroke colour, not per edge: markers cannot
  // inherit `stroke` from the path that uses them.
  const markerKinds = ["default", "extractor", "back"] as const;
  const markerColor = {
    default: "var(--color-muted-foreground)",
    extractor: KIND_COLOR_VAR.process,
    back: "var(--color-warning)",
  } as const;
  const markerFor = (edge: PlacedEdge) =>
    edge.back ? "back" : edge.edge.kind === "extractor" ? "extractor" : "default";

  return (
    <div
      className={cn("overflow-x-auto", className)}
      onClick={onBackgroundClick}
      role={onBackgroundClick ? "presentation" : undefined}
    >
      <div
        className="relative"
        style={{ width: placed.width, height: placed.height, minWidth: placed.width }}
      >
        <svg
          className="absolute inset-0 overflow-visible"
          width={placed.width}
          height={placed.height}
          aria-label={label ?? "Pipeline graph"}
          role="img"
        >
          <defs>
            {markerKinds.map((kind) => (
              <marker
                key={kind}
                id={`${markerPrefix}-arrow-${kind}`}
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 8 4 L 0 8 z" fill={markerColor[kind]} />
              </marker>
            ))}
          </defs>
          {placed.edges.map((edge) => (
            <path
              key={`${edge.edge.kind}:${edge.edge.id}:${edge.edge.from}`}
              d={edge.path}
              fill="none"
              stroke={edgeStroke(edge)}
              strokeWidth={1.25}
              strokeDasharray={edge.back ? "2 4" : EDGE_DASH[edge.edge.kind]}
              markerEnd={`url(#${markerPrefix}-arrow-${markerFor(edge)})`}
              opacity={
                dimmed(edge.edge.from) || dimmed(edge.edge.to) ? 0.15 : 0.75
              }
              data-edge-kind={edge.edge.kind}
            >
              <title>
                {EDGE_LABEL[edge.edge.kind]}
                {edge.back ? " (closes a loop)" : ""}
              </title>
            </path>
          ))}
        </svg>
        {placed.nodes.map((node) => (
          <Chip
            key={node.node.id}
            placed={node}
            size={size}
            decoration={decorate?.(node.node) ?? {}}
            badge={badges.get(node.node.id) ?? null}
            focused={node.node.id === focus}
            dimmed={dimmed(node.node.id)}
            onClick={onNodeClick}
          />
        ))}
      </div>
    </div>
  );
}
