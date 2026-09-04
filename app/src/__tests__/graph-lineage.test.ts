/**
 * `lineage()` (P-2, spec §4) — the subgraph an operator means by "this
 * product's pipeline". Pure: no React, no fetch, no database.
 */
import { describe, it, expect } from "vitest";
import { capLineage, FIXTURE_GRAPH, lineage } from "@stac-higher/shared";
import type { Graph } from "@stac-higher/shared";

const ids = (graph: { nodes: { id: string }[] }) =>
  graph.nodes.map((n) => n.id).sort();

describe("lineage", () => {
  it("walks a derived product's whole chain, both directions", () => {
    const result = lineage(FIXTURE_GRAPH, "coll:goes-geocolor");

    expect(result.focus).toBe("coll:goes-geocolor");
    expect(ids(result)).toEqual([
      "coll:goes-abi-mcmipc",
      "coll:goes-geocolor",
      "conn:goes-geocolor-dest", // downstream: the delivery tail
      "conn:goes-nodd", // upstream: back to the source connection
      "proc:goes-abi-metadata",
      "proc:goes-geocolor",
    ]);
    // Every demo-pipeline node stays out: the two pipelines share no node.
    expect(result.nodes.some((n) => n.id.includes("demo"))).toBe(false);
  });

  it("puts the focus node first", () => {
    const result = lineage(FIXTURE_GRAPH, "coll:goes-geocolor");
    expect(result.nodes[0].id).toBe("coll:goes-geocolor");
  });

  it("includes a chained product's neighbours on both sides", () => {
    // `demo-downscaled` is an output AND the next process's source — the case
    // the five-column layout flattens (spec §1.2).
    const result = lineage(FIXTURE_GRAPH, "coll:demo-downscaled");
    expect(ids(result)).toEqual([
      "coll:demo-downscaled",
      "coll:demo-scenes",
      "coll:demo-thumbnails",
      "conn:demo-store",
      "proc:demo-downscale",
      "proc:demo-thumbnails",
    ]);
  });

  it("returns the induced edges, not just the ones it walked", () => {
    const result = lineage(FIXTURE_GRAPH, "coll:goes-geocolor");
    expect(result.edges.map((e) => e.id).sort()).toEqual([
      "assoc-geocolor-deliver",
      "assoc-goes-ingest", // both the ingest edge and its extractor twin
      "assoc-goes-ingest",
      "out-geocolor",
      "src-geocolor",
    ]);
  });

  it("follows an extractor edge upstream — it is part of what makes the product", () => {
    const result = lineage(FIXTURE_GRAPH, "coll:goes-abi-mcmipc");
    expect(result.nodes.map((n) => n.id)).toContain("proc:goes-abi-metadata");
  });

  it("never follows an extractor edge downstream", () => {
    // Focusing the extractor itself must not drag in the collection it fixes
    // up (and through it, the whole GOES pipeline): the extractor produces no
    // derived product, so it has no downstream.
    const result = lineage(FIXTURE_GRAPH, "proc:goes-abi-metadata");
    expect(result.nodes.map((n) => n.id)).toEqual(["proc:goes-abi-metadata"]);
    expect(result.edges).toEqual([]);
  });

  it("does not join two collections that merely share an extractor", () => {
    const shared: Graph = {
      nodes: [
        ...FIXTURE_GRAPH.nodes,
        {
          id: "coll:goes-abi-mcmipf",
          type: "collection",
          label: "goes-abi-mcmipf",
          group_id: "earth-observation",
          meta: {},
        },
      ],
      edges: [
        ...FIXTURE_GRAPH.edges,
        {
          from: "proc:goes-abi-metadata",
          to: "coll:goes-abi-mcmipf",
          kind: "extractor",
          id: "assoc-goes-f-ingest",
        },
      ],
    };
    const result = lineage(shared, "coll:goes-abi-mcmipc");
    expect(result.nodes.map((n) => n.id)).not.toContain("coll:goes-abi-mcmipf");
  });

  it("terminates on a cycle through a connection", () => {
    // The write gate refuses only the cycles it can SEE; deliver→re-ingest
    // through one connection stays legal (`lib/graph/edges.ts`).
    const looped: Graph = {
      nodes: FIXTURE_GRAPH.nodes,
      edges: [
        ...FIXTURE_GRAPH.edges,
        {
          from: "conn:goes-geocolor-dest",
          to: "coll:goes-abi-mcmipc",
          kind: "ingest",
          id: "assoc-loop",
        },
      ],
    };
    const result = lineage(looped, "coll:goes-geocolor");
    expect(result.nodes).toHaveLength(6);
  });

  it("is empty for a node the graph does not contain", () => {
    const result = lineage(FIXTURE_GRAPH, "coll:nope");
    expect(result).toEqual({ nodes: [], edges: [], focus: "" });
  });

  it("returns a lone node for an unwired one", () => {
    const orphan: Graph = {
      nodes: [
        {
          id: "coll:orphan",
          type: "collection",
          label: "orphan",
          group_id: null,
          meta: {},
        },
      ],
      edges: [],
    };
    expect(lineage(orphan, "coll:orphan").nodes).toHaveLength(1);
  });
});

describe("capLineage", () => {
  /** One product feeding `n` consumers — spec §8's sprawling row. */
  const fanOut = (n: number): Graph => ({
    nodes: [
      {
        id: "coll:fan",
        type: "collection",
        label: "fan",
        group_id: null,
        meta: {},
      },
      ...Array.from({ length: n }, (_, i) => ({
        id: `proc:p${i}`,
        type: "process" as const,
        label: `p${i}`,
        group_id: null,
        meta: {},
      })),
    ],
    edges: Array.from({ length: n }, (_, i) => ({
      from: "coll:fan",
      to: `proc:p${i}`,
      kind: "process_source" as const,
      id: `s${i}`,
    })),
  });

  it("keeps every node when the row is already short", () => {
    const capped = capLineage(lineage(FIXTURE_GRAPH, "coll:goes-geocolor"), 8);
    expect(capped.hidden).toBe(0);
    expect(capped.nodes).toHaveLength(6);
  });

  it("trims downstream to the cap and reports what it dropped", () => {
    const capped = capLineage(lineage(fanOut(12), "coll:fan"), 4);
    expect(capped.hidden).toBe(8);
    expect(capped.nodes).toHaveLength(5); // the focus plus four consumers
    expect(capped.edges).toHaveLength(4);
  });

  it("never trims upstream — that is the half the row exists for", () => {
    // A long ingest → process → product chain, capped at zero downstream.
    const capped = capLineage(lineage(FIXTURE_GRAPH, "coll:goes-geocolor"), 0);
    expect(capped.nodes.map((n) => n.id)).toContain("conn:goes-nodd");
    expect(capped.nodes.map((n) => n.id)).toContain("proc:goes-abi-metadata");
    // Only the delivery destination is downstream of the focus.
    expect(capped.nodes.map((n) => n.id)).not.toContain("conn:goes-geocolor-dest");
    expect(capped.hidden).toBe(1);
  });

  it("does not spend budget on a node that is also upstream", () => {
    // deliver → re-ingest through one connection: the destination is both
    // downstream of the product and upstream of it.
    const looped: Graph = {
      nodes: FIXTURE_GRAPH.nodes,
      edges: [
        ...FIXTURE_GRAPH.edges,
        {
          from: "conn:goes-geocolor-dest",
          to: "coll:goes-abi-mcmipc",
          kind: "ingest",
          id: "assoc-loop",
        },
      ],
    };
    const capped = capLineage(lineage(looped, "coll:goes-geocolor"), 0);
    expect(capped.hidden).toBe(0);
    expect(capped.nodes).toHaveLength(6);
  });

  it("is a no-op for a node the graph does not contain", () => {
    expect(capLineage(lineage(FIXTURE_GRAPH, "coll:nope"), 3)).toEqual({
      nodes: [],
      edges: [],
      focus: "",
      hidden: 0,
    });
  });
});
