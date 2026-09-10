/**
 * Node decoration for the pipeline graph views (P-3): the link, the mono
 * detail line and the health verdict that `PipelineDag` and `LineagePanel`
 * both draw.
 *
 * Lives here rather than in either component because both surfaces render the
 * SAME row (spec §5.1) — one component, two surfaces, no drift — and because
 * the shared renderer is deliberately app-agnostic: health is decided on this
 * side, from the open-alert list, exactly like the M2-D flow hints.
 *
 * Health colouring joins the alert list client-side; the graph endpoint does
 * not embed alert state, so one alert read serves the whole page.
 * Process-anchored alerts carry their effective `process_id` (I-84), so a
 * process node is indicted the same way a connection or collection is.
 */
import type { DagNodeDecoration, LineageHealth } from "@stac-higher/shared";
import type { GraphNode } from "@/lib/monitoring/graph-api";

/** Alert kinds that indict a node, by the anchor the alert carries. */
export function unhealthyNodeIds(
  alerts: {
    kind: string;
    connection_id?: string | null;
    collection_id?: string | null;
    process_id?: string | null;
  }[],
): Set<string> {
  const ids = new Set<string>();
  for (const alert of alerts) {
    if (alert.connection_id) ids.add(`conn:${alert.connection_id}`);
    if (alert.collection_id) ids.add(`coll:${alert.collection_id}`);
    if (alert.process_id) ids.add(`proc:${alert.process_id}`);
  }
  return ids;
}

export function nodeHref(node: GraphNode): string {
  if (node.type === "process") return `/processes/${node.id.slice("proc:".length)}`;
  if (node.type === "collection") {
    return `/collections/${encodeURIComponent(node.id.slice("coll:".length))}`;
  }
  return "/connections";
}

/** The mono second line: whatever the node's meta actually says, plus degree. */
export function nodeDetail(node: GraphNode, degree: number): string {
  const bits: string[] = [];
  if (node.type === "connection" && typeof node.meta.protocol === "string") {
    bits.push(node.meta.protocol);
  }
  if (node.meta.archived === true) bits.push("archived");
  if (node.meta.enabled === false) bits.push("disabled");
  if (node.meta.deployed === false) bits.push("no revision");
  // Degree is the structural fact the old column layout lost: an isolated node
  // is wired to nothing, which is usually a mistake.
  bits.push(`${degree} ${degree === 1 ? "edge" : "edges"}`);
  return bits.join(" · ");
}

export function nodeHealth(
  node: GraphNode,
  unhealthy: ReadonlySet<string>,
): LineageHealth {
  if (unhealthy.has(node.id)) return "error";
  if (
    node.meta.archived === true ||
    node.meta.enabled === false ||
    node.meta.deployed === false
  ) {
    return "warn";
  }
  return "ok";
}

/** Every node's edge count, over the whole graph (not the lineage subgraph). */
export function degreeMap(
  edges: readonly { from: string; to: string }[],
): Map<string, number> {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
  }
  return degree;
}

/**
 * The `decorate` callback both views hand to `PipelineDag`. Degree comes from
 * the FULL graph deliberately: "2 edges" should mean what the platform knows,
 * not what this particular row happens to show.
 */
export function makeDecorator(
  unhealthy: ReadonlySet<string>,
  degree: ReadonlyMap<string, number>,
): (node: GraphNode) => DagNodeDecoration {
  return (node) => ({
    health: nodeHealth(node, unhealthy),
    href: nodeHref(node),
    detail: nodeDetail(node, degree.get(node.id) ?? 0),
  });
}
