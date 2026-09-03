/**
 * The platform's flow graph — nodes and edges (spec §8, I-64; M5-D).
 *
 * ONE edge model, shared by the M5-D cycle check and M5-E's
 * `/api/monitoring/graph`. They must agree: a cycle the graph endpoint draws
 * but the write gate permits (or vice versa) would be a UI that contradicts
 * the API, and the check is only trustworthy if it walks the same edges an
 * operator can see.
 *
 * **Our edges only, deliberately (I-64 decided).** The graph covers what the
 * platform owns: ingest and delivery associations, and process sources and
 * outputs. A loop that closes through a connection or an external system —
 * deliver to an FTP server that another association re-ingests — is not
 * statically decidable and is explicitly out of scope. The §7 run-rate
 * ceiling is the backstop for those; this check exists to refuse the cycles
 * we CAN see, at write time, with the path in the error.
 */

export type GraphNodeType = "collection" | "process" | "connection";

/** `coll:{id}` / `proc:{uuid}` / `conn:{uuid}` — stable across both consumers. */
export function collectionNode(id: string): string {
  return `coll:${id}`;
}
export function processNode(id: string): string {
  return `proc:${id}`;
}
export function connectionNode(id: string): string {
  return `conn:${id}`;
}

export type GraphEdgeKind =
  | "ingest"
  | "deliver"
  | "process_source"
  | "process_output"
  | "extractor";

export interface GraphEdge {
  from: string;
  to: string;
  kind: GraphEdgeKind;
  /** The row this edge came from — what the UI links to. */
  id: string;
}

/**
 * The edge kinds the CYCLE CHECK may walk.
 *
 * Ingest and deliver edges are in the graph — the `/graph` view draws them —
 * but they are deliberately NOT traversable here. Treating a connection as a
 * node you can path through would conflate "delivers to host X" with "ingests
 * from host X", which are usually different directories on that host. The
 * spec settles this explicitly: delivery→re-ingest through the same
 * connection stays POSSIBLE and documented, with the §7 run-rate ceiling as
 * the backstop. Refusing it here would block legitimate round-trip topologies
 * on a guess.
 *
 * An `extractor` edge (GOES spec §6.6 / §15) is display-only too — it
 * produces no `process_output`, so it cannot close a collection↔process loop.
 */
const TRAVERSABLE_KINDS: ReadonlySet<GraphEdgeKind> = new Set([
  "process_source",
  "process_output",
]);

/**
 * Depth-first search for a path from `start` back to `target`, over the
 * traversable (collection↔process) edges only.
 *
 * Returns the node path when one exists, `null` otherwise. Used to answer
 * "would adding an edge X→Y close a loop?" by asking whether Y already
 * reaches X.
 *
 * Iterative rather than recursive: the graph is operator-authored and could
 * be wide, and a stack overflow inside a write gate would fail the request
 * with something far less useful than a 409.
 */
export function findPath(
  edges: readonly GraphEdge[],
  start: string,
  target: string,
): string[] | null {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    if (!TRAVERSABLE_KINDS.has(edge.kind)) continue;
    const list = outgoing.get(edge.from);
    if (list) list.push(edge.to);
    else outgoing.set(edge.from, [edge.to]);
  }

  // Stack of paths rather than nodes: the path IS the answer, and rebuilding
  // it afterwards from a parent map is more code for the same result.
  const stack: string[][] = [[start]];
  const seen = new Set<string>();

  while (stack.length > 0) {
    const path = stack.pop() as string[];
    const node = path[path.length - 1];
    if (node === target && path.length > 1) return path;
    if (seen.has(node)) continue;
    seen.add(node);
    for (const next of outgoing.get(node) ?? []) {
      // A self-loop (start === target) is caught by the length check above;
      // here we just avoid revisiting.
      if (!seen.has(next) || next === target) stack.push([...path, next]);
    }
  }
  return null;
}

/**
 * Would adding `from → to` create a cycle? Returns the offending path
 * (including the new edge's endpoints) or null.
 */
export function wouldCycle(
  edges: readonly GraphEdge[],
  from: string,
  to: string,
): string[] | null {
  // A process whose source and output are the SAME collection is a one-hop
  // loop: it re-triggers on its own output forever. No search needed, and no
  // existing edge is required for it to be true.
  if (from === to) return [from, to];
  const back = findPath(edges, to, from);
  return back ? [from, ...back] : null;
}

/** Human-readable path for the 409 body: `coll:a → proc:b → coll:a`. */
export function formatPath(path: readonly string[]): string {
  return path.join(" → ");
}
