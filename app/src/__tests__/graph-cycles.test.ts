// @vitest-environment node
/**
 * Cycle refusal over the platform's own edges (I-64, M5-D).
 *
 * The pure half: `edges.ts` has no database, so the policy — which wirings
 * close a loop, and which are deliberately out of scope — is testable
 * directly.
 */
import { describe, it, expect } from "vitest";
import {
  collectionNode,
  connectionNode,
  findPath,
  formatPath,
  processNode,
  wouldCycle,
  type GraphEdge,
} from "@/lib/graph/edges";

const A = collectionNode("a");
const B = collectionNode("b");
const P = processNode("p1");
const Q = processNode("p2");
const CONN = connectionNode("c1");

function edge(from: string, to: string, kind: GraphEdge["kind"]): GraphEdge {
  return { from, to, kind, id: `${from}->${to}` };
}

describe("wouldCycle", () => {
  it("permits a straight line: a → p1 → b", () => {
    const edges = [edge(A, P, "process_source")];
    expect(wouldCycle(edges, P, B)).toBeNull();
  });

  it("refuses closing a two-hop loop", () => {
    // p1 already reads from a; adding p1 → a would make it consume its own
    // output forever.
    const edges = [edge(A, P, "process_source")];
    const cycle = wouldCycle(edges, P, A);
    expect(cycle).not.toBeNull();
    expect(formatPath(cycle!)).toContain("proc:p1");
    expect(formatPath(cycle!)).toContain("coll:a");
  });

  it("refuses a self-loop with no existing edges at all", () => {
    // The same collection as source AND output: nothing to search, still a
    // loop.
    expect(wouldCycle([], A, A)).toEqual([A, A]);
  });

  it("refuses a longer loop through a second process", () => {
    // a → p1 → b → p2, and now p2 → a would close it.
    const edges = [
      edge(A, P, "process_source"),
      edge(P, B, "process_output"),
      edge(B, Q, "process_source"),
    ];
    const cycle = wouldCycle(edges, Q, A);
    expect(cycle).not.toBeNull();
    expect(cycle!.length).toBeGreaterThan(3);
  });

  it("does NOT refuse a loop that closes through a connection", () => {
    // a is delivered to a connection which is re-ingested into b, and b feeds
    // p1. Adding p1 → a LOOKS like a loop if you treat the connection as a
    // traversable node — but "delivers to host X" and "ingests from host X"
    // are usually different directories, so the spec settles this as
    // permitted (delivery→re-ingest stays possible and documented, with the
    // §7 ceiling as the backstop). Refusing it would block a legitimate
    // round-trip topology on a guess.
    const edges = [
      edge(A, CONN, "deliver"),
      edge(CONN, B, "ingest"),
      edge(B, P, "process_source"),
    ];
    expect(wouldCycle(edges, P, A)).toBeNull();
  });

  it("still refuses the loop when the SAME path is all collection↔process", () => {
    // The mirror of the case above: no connection hop, so every edge is one
    // we own and the loop is decidable.
    const edges = [
      edge(A, Q, "process_source"),
      edge(Q, B, "process_output"),
      edge(B, P, "process_source"),
    ];
    expect(wouldCycle(edges, P, A)).not.toBeNull();
  });

  it("is not confused by a diamond that never closes", () => {
    const edges = [
      edge(A, P, "process_source"),
      edge(A, Q, "process_source"),
      edge(P, B, "process_output"),
      edge(Q, B, "process_output"),
    ];
    expect(wouldCycle(edges, B, processNode("p3"))).toBeNull();
  });

  it("terminates on a graph that already contains a cycle", () => {
    // A pre-existing loop (e.g. created before this check, or through a
    // re-enable) must not hang the write gate.
    const edges = [edge(A, P, "process_source"), edge(P, A, "process_output")];
    expect(wouldCycle(edges, B, Q)).toBeNull();
  });
});

describe("findPath", () => {
  it("returns null when the target is unreachable", () => {
    expect(findPath([edge(A, P, "process_source")], A, B)).toBeNull();
  });

  it("returns the path when reachable", () => {
    const edges = [edge(A, P, "process_source"), edge(P, B, "process_output")];
    expect(findPath(edges, A, B)).toEqual([A, P, B]);
  });
});

describe("extractor edges (G-6)", () => {
  it("are display-only: the cycle check never walks them", () => {
    const edges: GraphEdge[] = [
      { from: processNode("ex"), to: collectionNode("src"), kind: "extractor", id: "a1" },
      { from: collectionNode("src"), to: processNode("p"), kind: "process_source", id: "s1" },
    ];
    // p → ex would only close a loop THROUGH the extractor edge, which is
    // not traversable, so attaching `ex`'s "output" to src is not a cycle.
    expect(wouldCycle(edges, processNode("p"), processNode("ex"))).toBeNull();
    expect(findPath(edges, collectionNode("src"), processNode("ex"))).toBeNull();
  });
});
