/**
 * The wire shape of `GET /api/monitoring/graph` (M5-E), restated here so the
 * pure lineage/layout functions can live in the shared package without
 * reaching into the app's server-only `lib/graph/edges`.
 *
 * These are deliberately STRUCTURAL copies, not a new source of truth: the
 * app's `PipelineGraph` (`app/src/lib/monitoring/graph-api.ts`) assigns to
 * `Graph` without a cast, and would stop compiling the moment the two drift.
 * The platform's edge model still has exactly one owner — `lib/graph/edges` —
 * which is also what the M5-D write gate walks (spec §4: the picture and the
 * write gate cannot disagree).
 */

export type GraphNodeType = "collection" | "process" | "connection";

export type GraphEdgeKind =
  | "ingest"
  | "deliver"
  | "process_source"
  | "process_output"
  | "extractor";

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  group_id: string | null;
  meta: Record<string, unknown>;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: GraphEdgeKind;
  /** The row this edge came from — what the UI links to. */
  id: string;
}

export interface Graph {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
}
