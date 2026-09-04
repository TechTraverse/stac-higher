/**
 * `layeredLayout()` (P-2, spec §4) — the in-house Sugiyama behind both graph
 * views. Pure geometry: no React, no DOM.
 */
import { describe, it, expect } from "vitest";
import { FIXTURE_GRAPH, LAYOUT_DEFAULTS, layeredLayout, lineage } from "@stac-higher/shared";
import type { Graph, Layout } from "@stac-higher/shared";

const rankOf = (layout: Layout, id: string): number => {
  const placed = layout.nodes.find((n) => n.node.id === id);
  if (!placed) throw new Error(`${id} was not placed`);
  return placed.rank;
};

describe("layeredLayout — ranking", () => {
  it("puts a chain in strictly increasing ranks", () => {
    const layout = layeredLayout(FIXTURE_GRAPH);
    const chain = [
      "conn:demo-store",
      "coll:demo-scenes",
      "proc:demo-downscale",
      "coll:demo-downscaled",
      "proc:demo-thumbnails",
      "coll:demo-thumbnails",
    ].map((id) => rankOf(layout, id));

    for (let i = 1; i < chain.length; i++) {
      expect(chain[i]).toBeGreaterThan(chain[i - 1]);
    }
  });

  it("ranks an extractor one column left of the product it fixes up", () => {
    const layout = layeredLayout(FIXTURE_GRAPH);
    expect(rankOf(layout, "proc:goes-abi-metadata")).toBe(
      rankOf(layout, "coll:goes-abi-mcmipc") - 1,
    );
  });

  it("puts the extractor alongside the ingest connection, not after it", () => {
    // Spec §9.3: the extractor shares the source connection's column, which is
    // what says "it writes INTO the product" rather than "it derives from it".
    const layout = layeredLayout(FIXTURE_GRAPH);
    expect(rankOf(layout, "proc:goes-abi-metadata")).toBe(
      rankOf(layout, "conn:goes-nodd"),
    );
  });

  it("keeps every rank at or above zero when an extractor's product is a source", () => {
    // No ingest edge, so the collection would be rank 0 and its extractor -1.
    const graph: Graph = {
      nodes: [
        {
          id: "proc:x",
          type: "process",
          label: "x",
          group_id: null,
          meta: {},
        },
        { id: "coll:a", type: "collection", label: "a", group_id: null, meta: {} },
      ],
      edges: [{ from: "proc:x", to: "coll:a", kind: "extractor", id: "e1" }],
    };
    const layout = layeredLayout(graph);
    expect(layout.nodes.map((n) => n.rank).sort()).toEqual([0, 1]);
    expect(layout.nodes.every((n) => n.x >= 0 && n.y >= 0)).toBe(true);
  });

  it("uses the longest path, so a node waits for its deepest input", () => {
    // b feeds c directly AND through a longer arm; c belongs after the arm.
    const graph: Graph = {
      nodes: ["coll:b", "proc:mid", "coll:arm", "proc:c"].map((id) => ({
        id,
        type: id.startsWith("proc") ? ("process" as const) : ("collection" as const),
        label: id,
        group_id: null,
        meta: {},
      })),
      edges: [
        { from: "coll:b", to: "proc:mid", kind: "process_source", id: "1" },
        { from: "proc:mid", to: "coll:arm", kind: "process_output", id: "2" },
        { from: "coll:arm", to: "proc:c", kind: "process_source", id: "3" },
        { from: "coll:b", to: "proc:c", kind: "process_source", id: "4" },
      ],
    };
    const layout = layeredLayout(graph);
    expect(rankOf(layout, "proc:c")).toBe(3);
  });
});

describe("layeredLayout — cycles", () => {
  const looped: Graph = {
    nodes: FIXTURE_GRAPH.nodes,
    edges: [
      ...FIXTURE_GRAPH.edges,
      // deliver → re-ingest through the same connection: legal, and something
      // the M5-D write gate deliberately does not refuse.
      {
        from: "conn:goes-geocolor-dest",
        to: "coll:goes-abi-mcmipc",
        kind: "ingest",
        id: "assoc-loop",
      },
    ],
  };

  it("is total over a cycle — it ranks everything and returns", () => {
    const layout = layeredLayout(looped);
    expect(layout.nodes).toHaveLength(FIXTURE_GRAPH.nodes.length);
    expect(layout.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))).toBe(true);
  });

  it("still draws the back edge, flagged for dashing", () => {
    const layout = layeredLayout(looped);
    const loop = layout.edges.find((e) => e.edge.id === "assoc-loop");
    expect(loop?.back).toBe(true);
    expect(loop?.path).toMatch(/^M /);
    // Every other edge runs with the ranking.
    expect(layout.edges.filter((e) => e.back)).toHaveLength(1);
  });

  it("handles a graph that is nothing but a cycle", () => {
    const graph: Graph = {
      nodes: ["coll:a", "proc:b"].map((id) => ({
        id,
        type: id.startsWith("proc") ? ("process" as const) : ("collection" as const),
        label: id,
        group_id: null,
        meta: {},
      })),
      edges: [
        { from: "coll:a", to: "proc:b", kind: "process_source", id: "1" },
        { from: "proc:b", to: "coll:a", kind: "process_output", id: "2" },
      ],
    };
    const layout = layeredLayout(graph);
    expect(layout.ranks).toBe(2);
    expect(layout.edges.filter((e) => e.back)).toHaveLength(1);
  });

  it("survives a self-loop without ranking it", () => {
    const graph: Graph = {
      nodes: [{ id: "coll:a", type: "collection", label: "a", group_id: null, meta: {} }],
      edges: [{ from: "coll:a", to: "coll:a", kind: "process_source", id: "1" }],
    };
    expect(layeredLayout(graph).nodes[0].rank).toBe(0);
  });
});

describe("layeredLayout — geometry", () => {
  it("spaces ranks by node width plus the gap", () => {
    const layout = layeredLayout(FIXTURE_GRAPH);
    for (const placed of layout.nodes) {
      expect(placed.x).toBe(
        placed.rank * (LAYOUT_DEFAULTS.nodeWidth + LAYOUT_DEFAULTS.gapX),
      );
    }
    expect(layout.width).toBe(
      (layout.ranks - 1) * (LAYOUT_DEFAULTS.nodeWidth + LAYOUT_DEFAULTS.gapX) +
        LAYOUT_DEFAULTS.nodeWidth,
    );
  });

  it("draws a straight run for a level hop and an elbow otherwise", () => {
    const layout = layeredLayout(lineage(FIXTURE_GRAPH, "coll:demo-thumbnails"));
    // The demo chain is one node per rank, so every hop is level.
    expect(layout.edges.every((e) => /^M [\d.]+ [\d.]+ H [\d.]+$/.test(e.path))).toBe(true);

    const goes = layeredLayout(lineage(FIXTURE_GRAPH, "coll:goes-abi-mcmipc"));
    const extractor = goes.edges.find((e) => e.edge.kind === "extractor");
    expect(extractor?.path).toMatch(/H [\d.]+ V [\d.]+ H [\d.]+$/);
  });

  it("points the arrowhead at the target's entry port", () => {
    const layout = layeredLayout(FIXTURE_GRAPH);
    for (const placed of layout.edges) {
      const target = layout.nodes.find((n) => n.node.id === placed.edge.to);
      expect(placed.tipY).toBe((target as (typeof layout.nodes)[number]).y + LAYOUT_DEFAULTS.nodeHeight / 2);
      expect(placed.direction).toBe(1);
    }
  });

  it("honours custom sizing", () => {
    const layout = layeredLayout(FIXTURE_GRAPH, { nodeWidth: 10, nodeHeight: 4, gapX: 2, gapY: 1 });
    expect(layout.nodes.every((n) => n.width === 10 && n.height === 4)).toBe(true);
    expect(layout.nodes.find((n) => n.rank === 1)?.x).toBe(12);
  });

  it("returns an empty layout for an empty graph", () => {
    expect(layeredLayout({ nodes: [], edges: [] })).toEqual({
      nodes: [],
      edges: [],
      width: 0,
      height: 0,
      ranks: 0,
    });
  });

  it("drops half-edges rather than drawing to nowhere", () => {
    const graph: Graph = {
      nodes: [{ id: "coll:a", type: "collection", label: "a", group_id: null, meta: {} }],
      edges: [{ from: "coll:a", to: "proc:gone", kind: "process_source", id: "1" }],
    };
    expect(layeredLayout(graph).edges).toEqual([]);
  });
});

describe("layeredLayout — determinism", () => {
  it("gives identical output for identical input", () => {
    expect(layeredLayout(FIXTURE_GRAPH)).toEqual(layeredLayout(FIXTURE_GRAPH));
  });

  it("gives identical output across repeated runs of a tangled graph", () => {
    const tangled: Graph = {
      nodes: FIXTURE_GRAPH.nodes,
      edges: [
        ...FIXTURE_GRAPH.edges,
        {
          from: "coll:demo-downscaled",
          to: "proc:goes-geocolor",
          kind: "process_source",
          id: "cross",
        },
        {
          from: "conn:goes-geocolor-dest",
          to: "coll:demo-scenes",
          kind: "ingest",
          id: "cross-2",
        },
      ],
    };
    const first = JSON.stringify(layeredLayout(tangled));
    for (let i = 0; i < 5; i++) {
      expect(JSON.stringify(layeredLayout(tangled))).toBe(first);
    }
  });
});
