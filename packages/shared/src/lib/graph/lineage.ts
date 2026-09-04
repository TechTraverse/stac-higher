/**
 * `lineage(graph, nodeId)` — the subgraph an operator means by "this
 * product's pipeline" (spec §4).
 *
 * Upstream is the transitive closure over INCOMING edges, downstream over
 * OUTGOING ones. Pure and React-free so both surfaces that render a lineage
 * row — the `/graph` Pipelines view and the collection page's `LineagePanel`
 * — derive it the same way, from the same payload, with no second query.
 */
import type { Graph, GraphEdge, GraphNode } from "./types";

export interface Lineage {
  /** Focus first, then the remaining nodes in the input graph's order. */
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** The node the lineage was taken around; `""` when it was not in the graph. */
  focus: string;
}

/**
 * An `extractor` edge (GOES spec §6.6 / §15) runs process → collection like a
 * `process_output`, but it means something different: the extractor FIXES UP
 * the items an ingest association is already writing into that collection
 * rather than deriving a new product from it. So it is part of what produces
 * the collection — followed when walking upstream — and never a way to leave
 * the extractor process when walking downstream. Without this rule, focusing
 * a collection that happens to share an extractor with another collection
 * would drag that unrelated collection into the row.
 */
function traversable(edge: GraphEdge, direction: "up" | "down"): boolean {
  return edge.kind !== "extractor" || direction === "up";
}

/** Reachable nodes in breadth-first order — nearest to `start` first. */
function reachable(
  start: string,
  edges: readonly GraphEdge[],
  direction: "up" | "down",
): string[] {
  // Upstream walks edges backwards (`to` → `from`), downstream forwards.
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    if (!traversable(edge, direction)) continue;
    const [key, next] =
      direction === "up" ? [edge.to, edge.from] : [edge.from, edge.to];
    const list = adjacency.get(key);
    if (list) list.push(next);
    else adjacency.set(key, [next]);
  }

  // Iterative BFS: operator-authored graphs are small but the write gate only
  // refuses the cycles it can SEE (`edges.ts`), so a loop through a connection
  // is possible and recursion here would be a stack overflow in the UI.
  const seen = new Set<string>();
  const order: string[] = [];
  const queue = [start];
  while (queue.length > 0) {
    const node = queue.shift() as string;
    for (const next of adjacency.get(node) ?? []) {
      if (seen.has(next)) continue;
      seen.add(next);
      order.push(next);
      queue.push(next);
    }
  }
  return order;
}

/**
 * Every node reachable from `nodeId` in either direction, plus `nodeId`, and
 * the edges INDUCED on that node set.
 *
 * Induced rather than "edges traversed" on purpose: if two of the lineage's
 * own nodes are wired to each other by an edge the walk happened not to need
 * — a process that takes both the focus product and one of its ancestors as
 * sources — drawing the node without its edge would misstate that process's
 * inputs. Every edge shown joins two nodes that are already in the picture.
 */
export function lineage(graph: Graph, nodeId: string): Lineage {
  const present = graph.nodes.some((node) => node.id === nodeId);
  if (!present) return { nodes: [], edges: [], focus: "" };

  const included = new Set<string>([nodeId]);
  for (const id of reachable(nodeId, graph.edges, "up")) included.add(id);
  for (const id of reachable(nodeId, graph.edges, "down")) included.add(id);

  const nodes = graph.nodes.filter((node) => included.has(node.id));
  // Focus first so a consumer can label the row without a second lookup; the
  // rest keep the graph's own order, which makes the result deterministic.
  nodes.sort((a, b) => Number(b.id === nodeId) - Number(a.id === nodeId));

  return {
    nodes,
    edges: graph.edges.filter(
      (edge) => included.has(edge.from) && included.has(edge.to),
    ),
    focus: nodeId,
  };
}

export interface CappedLineage extends Lineage {
  /** Downstream nodes trimmed to keep the row short. */
  hidden: number;
}

/**
 * Trim a lineage's DOWNSTREAM half to at most `maxDownstream` nodes.
 *
 * Spec §8's risk: a product with many downstreams grows its row in the
 * Pipelines list until the list stops being scannable. Upstream is never
 * trimmed — "where did this come from" is the whole reason the row exists, and
 * it is bounded by the pipeline's depth anyway; downstream fans out. Nodes are
 * kept in breadth-first order from the focus, so the ones dropped are the
 * furthest away, and the Graph view carries the rest.
 */
export function capLineage(
  result: Lineage,
  maxDownstream: number,
): CappedLineage {
  if (result.focus === "") return { ...result, hidden: 0 };

  const keep = new Set<string>([result.focus]);
  for (const id of reachable(result.focus, result.edges, "up")) keep.add(id);

  let hidden = 0;
  let kept = 0;
  for (const id of reachable(result.focus, result.edges, "down")) {
    // A node that is ALSO upstream (a loop through a connection) is already in
    // and does not spend budget.
    if (keep.has(id)) continue;
    if (kept < maxDownstream) {
      keep.add(id);
      kept++;
    } else {
      hidden++;
    }
  }
  if (hidden === 0) return { ...result, hidden: 0 };

  return {
    focus: result.focus,
    nodes: result.nodes.filter((node) => keep.has(node.id)),
    edges: result.edges.filter(
      (edge) => keep.has(edge.from) && keep.has(edge.to),
    ),
    hidden,
  };
}
