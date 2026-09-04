/**
 * `layeredLayout(graph)` — coordinates for an SVG rendering of any graph
 * `/api/monitoring/graph` can return (spec §4).
 *
 * A small Sugiyama, written in-house because the lead declined `elkjs`,
 * `dagre` and React Flow (spec §2): (1) rank by longest path from a source,
 * (2) order within a rank by the barycenter of neighbours in the adjacent
 * rank, two sweeps, (3) x from rank, y from order, edges as orthogonal paths
 * with a fixed elbow.
 *
 * **Total, never throwing.** The M5-D write gate guarantees the edges the
 * platform OWNS are acyclic, but it deliberately does not walk connection or
 * extractor edges (`lib/graph/edges.ts`), so a loop that closes through a
 * connection is possible and legal. A back-edge found by DFS is dropped from
 * ranking — otherwise longest-path would not terminate — and still drawn,
 * flagged `back` so the renderer can dash it.
 *
 * Pure and React-free: the renderer takes placed nodes, so if the platform's
 * graphs ever outgrow barycenter ordering (spec §8) this function is the only
 * thing that gets swapped.
 */
import type { Graph, GraphEdge, GraphNode } from "./types";

export interface LayoutOptions {
  nodeWidth?: number;
  nodeHeight?: number;
  /** Horizontal gap between ranks. */
  gapX?: number;
  /** Vertical gap between nodes within a rank. */
  gapY?: number;
  /** How far an edge runs straight out of a node before it turns. */
  elbow?: number;
}

export const LAYOUT_DEFAULTS: Required<LayoutOptions> = {
  nodeWidth: 168,
  nodeHeight: 44,
  gapX: 56,
  gapY: 14,
  elbow: 16,
};

export interface PlacedNode {
  node: GraphNode;
  /** Top-left corner. */
  x: number;
  y: number;
  width: number;
  height: number;
  rank: number;
  /** Position within the rank, top to bottom. */
  order: number;
}

export interface PlacedEdge {
  edge: GraphEdge;
  /** SVG `d` for an orthogonal path from source port to target port. */
  path: string;
  /** Where the arrowhead sits, and which way it points (+1 right, -1 left). */
  tipX: number;
  tipY: number;
  direction: 1 | -1;
  /** True when the edge runs against the ranking — draw it dashed. */
  back: boolean;
}

export interface Layout {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  width: number;
  height: number;
  /** Number of columns, i.e. `maxRank + 1`. */
  ranks: number;
}

/** Ranking ignores extractor edges — they are placed relatively, in step 2. */
function ranksEdge(edge: GraphEdge): boolean {
  return edge.kind !== "extractor" && edge.from !== edge.to;
}

/**
 * Edges that close a cycle, by iterative DFS with the classic three colours.
 *
 * An edge into a node still on the current DFS stack is a back edge. The walk
 * starts from every node in input order so a graph with no source (a pure
 * cycle) is still fully visited, which makes the answer independent of where
 * the traversal happens to begin.
 */
function findBackEdges(
  nodeIds: readonly string[],
  edges: readonly GraphEdge[],
): Set<GraphEdge> {
  const outgoing = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    const list = outgoing.get(edge.from);
    if (list) list.push(edge);
    else outgoing.set(edge.from, [edge]);
  }

  const back = new Set<GraphEdge>();
  const done = new Set<string>();
  const onStack = new Set<string>();

  for (const root of nodeIds) {
    if (done.has(root)) continue;
    // Each frame is a node plus how far through its edge list we are, so the
    // "leaving the node" moment (popping it off the stack) is explicit.
    const stack: { id: string; i: number }[] = [{ id: root, i: 0 }];
    onStack.add(root);
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const outs = outgoing.get(frame.id) ?? [];
      if (frame.i >= outs.length) {
        onStack.delete(frame.id);
        done.add(frame.id);
        stack.pop();
        continue;
      }
      const edge = outs[frame.i++];
      if (onStack.has(edge.to)) back.add(edge);
      else if (!done.has(edge.to)) {
        onStack.add(edge.to);
        stack.push({ id: edge.to, i: 0 });
      }
    }
  }
  return back;
}

/** Longest path from any source over an acyclic edge set. */
function longestPathRanks(
  nodeIds: readonly string[],
  edges: readonly GraphEdge[],
): Map<string, number> {
  const indegree = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  const outgoing = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
    const list = outgoing.get(edge.from);
    if (list) list.push(edge);
    else outgoing.set(edge.from, [edge]);
  }

  const rank = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  // Kahn in input order: ties in the topological order resolve the same way
  // on every run, which is what makes the whole layout deterministic.
  const queue = nodeIds.filter((id) => (indegree.get(id) ?? 0) === 0);
  for (let head = 0; head < queue.length; head++) {
    const id = queue[head];
    for (const edge of outgoing.get(id) ?? []) {
      rank.set(edge.to, Math.max(rank.get(edge.to) ?? 0, (rank.get(id) ?? 0) + 1));
      const left = (indegree.get(edge.to) ?? 0) - 1;
      indegree.set(edge.to, left);
      if (left === 0) queue.push(edge.to);
    }
  }
  return rank;
}

export function layeredLayout(graph: Graph, options: LayoutOptions = {}): Layout {
  const { nodeWidth, nodeHeight, gapX, gapY, elbow } = {
    ...LAYOUT_DEFAULTS,
    ...options,
  };

  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const nodeIds = graph.nodes.map((node) => node.id);
  // A half-edge would be drawn to nowhere. `loadGraph` already drops them for
  // visibility reasons; doing it again here keeps the function total when a
  // caller hands us a hand-built subgraph.
  const edges = graph.edges.filter((e) => byId.has(e.from) && byId.has(e.to));

  if (graph.nodes.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0, ranks: 0 };
  }

  // --- 1. ranks --------------------------------------------------------
  const rankingCandidates = edges.filter(ranksEdge);
  const back = findBackEdges(nodeIds, rankingCandidates);
  const rank = longestPathRanks(
    nodeIds,
    rankingCandidates.filter((edge) => !back.has(edge)),
  );

  // --- 2. extractors sit one column LEFT of the product they fix up ----
  //
  // Spec §9.3: beside the ingest connection, drawn pointing INTO the product,
  // which is what makes "this process writes into that collection rather than
  // deriving from it" legible. Only a node with no ranking edges of its own is
  // moved — an extractor has neither sources nor outputs by construction
  // (G-6), and if that ever changes its real edges should win.
  const ranked = new Set<string>();
  for (const edge of rankingCandidates) {
    ranked.add(edge.from);
    ranked.add(edge.to);
  }
  for (const edge of edges) {
    if (edge.kind !== "extractor" || ranked.has(edge.from)) continue;
    const target = rank.get(edge.to) ?? 0;
    rank.set(edge.from, Math.min(rank.get(edge.from) ?? target - 1, target - 1));
  }

  // An extractor of a rank-0 product lands at -1; shift the whole graph back
  // to a zero origin rather than emitting negative coordinates.
  const minRank = Math.min(...nodeIds.map((id) => rank.get(id) ?? 0));
  if (minRank !== 0) {
    for (const id of nodeIds) rank.set(id, (rank.get(id) ?? 0) - minRank);
  }
  const maxRank = Math.max(...nodeIds.map((id) => rank.get(id) ?? 0));

  // --- 3. ordering within a rank ---------------------------------------
  const columns: string[][] = Array.from({ length: maxRank + 1 }, () => []);
  for (const id of nodeIds) columns[rank.get(id) ?? 0].push(id);

  // Barycenter uses EVERY edge, extractor ones included: pulling an extractor
  // towards the row of the product it feeds is the whole point of its column.
  const neighbours = new Map<string, string[]>();
  const link = (a: string, b: string) => {
    const list = neighbours.get(a);
    if (list) list.push(b);
    else neighbours.set(a, [b]);
  };
  for (const edge of edges) {
    link(edge.from, edge.to);
    link(edge.to, edge.from);
  }

  const positionOf = new Map<string, number>();
  const record = (column: readonly string[]) =>
    column.forEach((id, i) => positionOf.set(id, i));
  for (const column of columns) record(column);

  const sweep = (column: string[], reference: number) => {
    const target = columns[reference];
    if (!target || target.length === 0) return;
    const inReference = new Set(target);
    const barycenter = new Map<string, number>();
    for (const id of column) {
      const positions = (neighbours.get(id) ?? [])
        .filter((other) => inReference.has(other))
        .map((other) => positionOf.get(other) as number);
      if (positions.length > 0) {
        barycenter.set(
          id,
          positions.reduce((sum, p) => sum + p, 0) / positions.length,
        );
      }
    }
    // Nodes with no neighbour in the reference column keep their current
    // position — `sort` is stable, so they do not jump around between sweeps.
    column.sort(
      (a, b) =>
        (barycenter.get(a) ?? (positionOf.get(a) as number)) -
        (barycenter.get(b) ?? (positionOf.get(b) as number)),
    );
    // Record IMMEDIATELY: the next column's barycenters must be taken against
    // this column's new order, not the order it had before the sweep. Batching
    // the update to the end of a sweep makes the two passes disagree, and a
    // crossing appears where neither pass wanted one.
    record(column);
  };

  for (let r = 1; r <= maxRank; r++) sweep(columns[r], r - 1);
  for (let r = maxRank - 1; r >= 0; r--) sweep(columns[r], r + 1);

  // --- 4. coordinates ---------------------------------------------------
  const columnHeight = (count: number) =>
    count === 0 ? 0 : count * nodeHeight + (count - 1) * gapY;
  const height = Math.max(...columns.map((column) => columnHeight(column.length)));

  const placed: PlacedNode[] = [];
  const boxes = new Map<string, PlacedNode>();
  columns.forEach((column, r) => {
    // Columns are centred against the tallest one so a straight chain reads as
    // a single horizontal line — the compact row the Pipelines view wants.
    const top = (height - columnHeight(column.length)) / 2;
    column.forEach((id, i) => {
      const box: PlacedNode = {
        node: byId.get(id) as GraphNode,
        x: r * (nodeWidth + gapX),
        y: top + i * (nodeHeight + gapY),
        width: nodeWidth,
        height: nodeHeight,
        rank: r,
        order: i,
      };
      boxes.set(id, box);
      placed.push(box);
    });
  });
  // Emit nodes in the caller's order rather than column order: a consumer that
  // zips the result against `graph.nodes` should not have to re-sort.
  const inputOrder = new Map(nodeIds.map((id, i) => [id, i]));
  placed.sort(
    (a, b) =>
      (inputOrder.get(a.node.id) as number) -
      (inputOrder.get(b.node.id) as number),
  );

  // --- 5. edges ---------------------------------------------------------
  const placedEdges: PlacedEdge[] = edges.map((edge) => {
    const a = boxes.get(edge.from) as PlacedNode;
    const b = boxes.get(edge.to) as PlacedNode;
    const forward = b.x >= a.x;
    const sx = forward ? a.x + a.width : a.x;
    const sy = a.y + a.height / 2;
    const tx = forward ? b.x : b.x + b.width;
    const ty = b.y + b.height / 2;
    const ex = forward ? sx + elbow : sx - elbow;
    return {
      edge,
      path:
        sy === ty
          ? `M ${sx} ${sy} H ${tx}`
          : `M ${sx} ${sy} H ${ex} V ${ty} H ${tx}`,
      tipX: tx,
      tipY: ty,
      direction: forward ? 1 : -1,
      back: back.has(edge),
    };
  });

  return {
    nodes: placed,
    edges: placedEdges,
    width: maxRank * (nodeWidth + gapX) + nodeWidth,
    height,
    ranks: maxRank + 1,
  };
}
