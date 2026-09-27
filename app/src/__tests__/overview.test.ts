/**
 * `components/layout/overview.ts` — the derivations home, the product page and
 * the graph all read (UI-3/UI-4/UI-5). Pure functions over the three platform
 * responses, so they test directly with no fetch mocking. Closes I-88.
 *
 * The point of these tests is the RULES the module documents: health defers to
 * the monitor's alerts, an unattributable alert is surfaced rather than
 * swallowed, and "no evidence" is never reported as "healthy".
 */
import { describe, it, expect } from "vitest";
import {
  buildConnectionChips,
  buildProductRows,
  buildStats,
  countImagesAtRisk,
  describeImagesAtRisk,
  successRate,
} from "@/components/layout/overview";
import { alertKindLabel } from "@/components/monitoring/shared";
import type { Alert } from "@/lib/monitoring/api";
import type { DailyStats, PipelineGraph } from "@/lib/monitoring/graph-api";
import type { Association } from "@/lib/associations/types";
import type { Image } from "@/lib/images/types";

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a1",
    source: "flow_monitor",
    kind: "ingest_inactivity",
    connection_id: null,
    association_id: null,
    channel_id: null,
    state: "firing",
    message: "",
    first_seen: "2026-09-01T00:00:00Z",
    last_seen: "2026-09-01T00:00:00Z",
    acknowledged_at: null,
    acknowledged_by: null,
    resolved_at: null,
    group_id: null,
    connection_name: null,
    collection_id: null,
    process_id: null,
    source_id: null,
    ...over,
  } as Alert;
}

function flow(over: Partial<Association> = {}): Association {
  return {
    id: "f1",
    collection_id: "prod-a",
    connection_id: "c1",
    direction: "ingest",
    enabled: true,
    config: {},
    expectation: null,
    flow_stats: {},
    created_by: "dev",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    connection: { name: "src", protocol: "s3", status: "ok" },
    ...over,
  } as Association;
}

const GRAPH: PipelineGraph = {
  nodes: [
    { id: "conn:c1", type: "connection", label: "src", group_id: "g", meta: { protocol: "s3", status: "ok" } },
    { id: "conn:c2", type: "connection", label: "dest", group_id: "g", meta: { protocol: "sftp", status: "error" } },
    { id: "coll:prod-a", type: "collection", label: "Product A", group_id: "g", meta: {} },
    { id: "proc:p1", type: "process", label: "masker", group_id: "g", meta: { deployed: true } },
  ],
  edges: [
    { from: "conn:c1", to: "coll:prod-a", kind: "ingest", id: "f1" },
    { from: "coll:prod-a", to: "conn:c2", kind: "deliver", id: "f2" },
    { from: "coll:prod-a", to: "proc:p1", kind: "process_source", id: "s1" },
    { from: "proc:p1", to: "coll:prod-a", kind: "process_output", id: "o1" },
  ],
};

describe("buildConnectionChips", () => {
  it("joins direction in from the flows — a connection has none of its own", () => {
    const chips = buildConnectionChips(
      GRAPH,
      [flow({ id: "f1", connection_id: "c1", direction: "ingest" }),
       flow({ id: "f2", connection_id: "c1", direction: "deliver" })],
      [],
    );
    const c1 = chips.find((c) => c.id === "c1");
    expect(c1?.directions).toEqual(["deliver", "ingest"]);
    expect(chips.find((c) => c.id === "c2")?.directions).toEqual([]);
  });

  it("ranks unhealthy connections first, then by name", () => {
    const chips = buildConnectionChips(GRAPH, [], []);
    expect(chips.map((c) => [c.id, c.health])).toEqual([
      ["c2", "error"],
      ["c1", "ok"],
    ]);
  });

  it("lets an open alert override a connection the graph calls ok", () => {
    const chips = buildConnectionChips(GRAPH, [], [alert({ connection_id: "c1" })]);
    expect(chips.find((c) => c.id === "c1")?.health).toBe("error");
  });

  it("degrades an acknowledged alert to a warning, not an error", () => {
    const chips = buildConnectionChips(
      GRAPH,
      [],
      [alert({ connection_id: "c1", state: "acknowledged" })],
    );
    expect(chips.find((c) => c.id === "c1")?.health).toBe("warn");
  });

  it("returns nothing without a graph", () => {
    expect(buildConnectionChips(undefined, [flow()], [alert()])).toEqual([]);
  });
});

describe("buildProductRows health", () => {
  const base = {
    collections: [{ id: "prod-a", title: "Product A" }],
    graph: GRAPH,
    flows: [flow()],
    alertsAreComplete: true,
  };

  it("is ok when the alert list is complete and says nothing", () => {
    const { rows } = buildProductRows({ ...base, openAlerts: [] });
    expect(rows[0]).toMatchObject({ health: "ok", reason: null, onPlatform: true });
  });

  it("refuses to claim health when the alert list is incomplete", () => {
    const { rows } = buildProductRows({
      ...base,
      openAlerts: [],
      alertsAreComplete: false,
    });
    expect(rows[0].health).toBe("unknown");
  });

  it("is unknown for a collection the platform does not know", () => {
    const { rows } = buildProductRows({
      ...base,
      collections: [{ id: "not-on-platform" }],
      openAlerts: [],
    });
    expect(rows[0]).toMatchObject({ health: "unknown", onPlatform: false });
  });

  it("says why a wired-up product with no flows is unknown", () => {
    const { rows } = buildProductRows({
      ...base,
      graph: { nodes: GRAPH.nodes, edges: [] },
      flows: [],
      openAlerts: [],
    });
    expect(rows[0]).toMatchObject({
      health: "unknown",
      reason: "no sources or destinations",
    });
  });

  it("a firing alert outranks an acknowledged one", () => {
    const { rows } = buildProductRows({
      ...base,
      openAlerts: [
        alert({ id: "ack", collection_id: "prod-a", kind: "push_rejected", state: "acknowledged" }),
        alert({ id: "fire", collection_id: "prod-a", kind: "push_rejected" }),
      ],
    });
    expect(rows[0].health).toBe("error");
    expect(rows[0].reason).not.toMatch(/acknowledged/);
  });

  it("warns on an expectation breach, from the monitor's alert not a local guess", () => {
    const { rows } = buildProductRows({
      ...base,
      openAlerts: [
        alert({ id: "late", association_id: "f1", kind: "ingest_inactivity", state: "acknowledged" }),
      ],
    });
    expect(rows[0].health).toBe("warn");
    // NOTE: this lands on the ACKNOWLEDGED branch, not the `lateFlow` one.
    // `isLate` matches an association-anchored alert that is not resolved —
    // which is exactly the set the `own` filter has already claimed as firing
    // or acknowledged, so `lateFlow` can never be the first branch to hit.
    // Same verdict either way, so the dead branch is harmless; recorded in
    // UI-TODO rather than deleted in a test-only slice.
    expect(rows[0].reason).toBe("ingest inactivity (acknowledged)");
  });
});

describe("buildProductRows attribution", () => {
  it("claims alerts anchored by collection, association, or wired connection", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow({ id: "f1", connection_id: "c1" })],
      alertsAreComplete: true,
      openAlerts: [
        alert({ id: "by-collection", collection_id: "prod-a" }),
        alert({ id: "by-association", association_id: "f1" }),
        alert({ id: "by-connection", connection_id: "c1" }),
      ],
    });
    expect(rows[0].health).toBe("error");
    expect(unattributed).toEqual([]);
  });

  it("claims a process alert through a wired process (I-84)", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "proc-alert", kind: "process_failed", process_id: "p1" })],
    });
    expect(rows[0].health).toBe("error");
    expect(rows[0].reason).toBe(alertKindLabel("process_failed"));
    expect(unattributed).toEqual([]);
  });

  it("a flagged image degrades its process and its product, never fails them (C-4)", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "img", kind: "process_image_flagged", process_id: "p1" })],
    });
    expect(rows[0].health).toBe("warn");
    expect(rows[0].reason).toBe(alertKindLabel("process_image_flagged"));
    const processes = rows[0].lineage.find((g) => g.kind === "process")!;
    expect(processes.nodes.every((n) => n.health === "warn")).toBe(true);
  });

  it("a firing process_failed still wins over an image-flagged alert listed first on the same process (C-4, lead ruling F1)", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [
        alert({ id: "img", kind: "process_image_flagged", process_id: "p1" }),
        alert({ id: "dead", kind: "process_failed", process_id: "p1" }),
      ],
    });
    const processes = rows[0].lineage.find((g) => g.kind === "process")!;
    expect(processes.nodes.every((n) => n.health === "error")).toBe(true);
  });

  it("claims a process alert through a process_output edge alone (I-84)", () => {
    const graph: PipelineGraph = {
      nodes: [
        { id: "coll:prod-a", type: "collection", label: "Product A", group_id: "g", meta: {} },
        { id: "proc:p1", type: "process", label: "masker", group_id: "g", meta: { deployed: true } },
      ],
      edges: [{ from: "proc:p1", to: "coll:prod-a", kind: "process_output", id: "o1" }],
    };
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph,
      flows: [],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "proc-alert", kind: "process_failed", process_id: "p1" })],
    });
    expect(rows[0].health).toBe("error");
    expect(unattributed).toEqual([]);
  });

  it("still surfaces an alert no product can claim — a channel alert, or a process wired to nothing", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [
        alert({ id: "webhook", kind: "webhook_failed", channel_id: "ch1" }),
        alert({ id: "elsewhere", kind: "process_stalled", process_id: "p-not-wired" }),
      ],
    });
    expect(rows[0].health).toBe("ok");
    expect(unattributed.map((a) => a.id)).toEqual(["webhook", "elsewhere"]);
  });

  it("does not attribute an alert through a connection the product is not wired to", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow({ id: "f1", connection_id: "c1" })],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "other", connection_id: "c2" })],
    });
    expect(rows[0].health).toBe("ok");
    expect(unattributed.map((a) => a.id)).toEqual(["other"]);
  });
});

describe("buildProductRows lineage and counts", () => {
  it("groups sources, processes and destinations with their labels", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      openAlerts: [],
      alertsAreComplete: true,
    });
    const [sources, processes, destinations] = rows[0].lineage;
    expect(sources.nodes.map((n) => n.label)).toEqual(["src"]);
    expect(destinations.nodes.map((n) => n.label)).toEqual(["dest"]);
    expect(processes.nodes.map((n) => n.label)).toEqual(["masker", "masker"]);
    expect(processes.nodes.map((n) => n.detail)).toEqual([
      "reads this product",
      "writes this product",
    ]);
    expect(processes.nodes[0].href).toBe("/processes/p1");
  });

  it("rolls a group's health up from its worst node", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      openAlerts: [],
      alertsAreComplete: true,
    });
    const [sources, , destinations] = rows[0].lineage;
    expect(sources.health).toBe("ok"); // conn c1 is ok
    expect(destinations.health).toBe("error"); // conn c2 is error
  });

  it("flags an undeployed process as a warning", () => {
    const graph: PipelineGraph = {
      nodes: GRAPH.nodes.map((n) =>
        n.id === "proc:p1" ? { ...n, meta: { deployed: false } } : n,
      ),
      edges: GRAPH.edges,
    };
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph,
      flows: [flow()],
      openAlerts: [],
      alertsAreComplete: true,
    });
    expect(rows[0].lineage[1].nodes[0]).toMatchObject({
      detail: "not deployed",
      health: "warn",
    });
  });

  it("colours a process lineage node from its open alert — firing is an error, acknowledged a warning", () => {
    const firing = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true,
      openAlerts: [alert({ id: "a", kind: "process_failed", process_id: "p1" })],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(firing.nodes.every((n) => n.health === "error")).toBe(true);
    expect(firing.health).toBe("error");

    const acked = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true,
      openAlerts: [alert({ id: "a", kind: "process_failed", process_id: "p1", state: "acknowledged" })],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(acked.nodes.every((n) => n.health === "warn")).toBe(true);
  });

  it("a deployed process with no alert is ok, not unknown", () => {
    const group = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true, openAlerts: [],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(group.nodes.every((n) => n.health === "ok")).toBe(true);
    expect(group.health).toBe("ok");
  });

  it("refuses to claim a deployed process is ok when the alert list is incomplete", () => {
    const group = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: false, openAlerts: [],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(group.nodes.every((n) => n.health === "unknown")).toBe(true);
    expect(group.health).toBe("unknown");
  });

  it("counts ingested items across ingest flows only, and null with none", () => {
    const withIngest = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [
        flow({ id: "f1", direction: "ingest", flow_stats: { items: 7 } }),
        flow({ id: "f2", direction: "deliver", flow_stats: { items: 99 } }),
      ],
      openAlerts: [],
      alertsAreComplete: true,
    });
    expect(withIngest.rows[0].itemsIngested).toBe(7);

    const deliverOnly = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow({ id: "f2", direction: "deliver", flow_stats: { items: 99 } })],
      openAlerts: [],
      alertsAreComplete: true,
    });
    expect(deliverOnly.rows[0].itemsIngested).toBeNull();
  });

  it("falls back to the id when a collection has no title", () => {
    const { rows } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [],
      openAlerts: [],
      alertsAreComplete: true,
    });
    expect(rows[0].title).toBe("prod-a");
  });
});

describe("buildStats", () => {
  it("counts connections from the chips and processes from the graph", () => {
    const chips = buildConnectionChips(GRAPH, [], []);
    const stats = buildStats(GRAPH, [flow({ flow_stats: { items: 4 } })], chips);
    expect(stats).toMatchObject({
      connections: 2,
      connectionsDegraded: 1,
      processes: 1,
      processesUndeployed: 0,
      itemsIngested: 4,
    });
  });

  it("sums ingest flows only", () => {
    const stats = buildStats(GRAPH, [
      flow({ id: "f1", direction: "ingest", flow_stats: { items: 4 } }),
      flow({ id: "f2", direction: "deliver", flow_stats: { items: 100 } }),
    ], []);
    expect(stats.itemsIngested).toBe(4);
  });
});

describe("successRate", () => {
  function day(over: Partial<DailyStats>): DailyStats {
    return {
      day: "2026-09-01",
      files: 0,
      items: 0,
      bytes: 0,
      delivered: 0,
      failed: 0,
      dead: 0,
      runs: 0,
      ...over,
    };
  }

  it("counts items against failures for ingest", () => {
    expect(successRate("ingest", [day({ items: 9, failed: 1 })])).toBe(90);
  });

  it("counts delivered against failed AND dead for deliver", () => {
    expect(
      successRate("deliver", [day({ delivered: 8, failed: 1, dead: 1 })]),
    ).toBe(80);
  });

  it("sums across the window", () => {
    expect(
      successRate("ingest", [
        day({ items: 5, failed: 0 }),
        day({ items: 5, failed: 10 }),
      ]),
    ).toBe(50);
  });

  it("is null with no window and null with no attempts — unmeasured, not perfect", () => {
    expect(successRate("ingest", undefined)).toBeNull();
    expect(successRate("ingest", [])).toBeNull();
    expect(successRate("ingest", [day({})])).toBeNull();
  });
});

describe("countImagesAtRisk (C-3, container-images spec §9.2)", () => {
  const row = (over: Partial<Image>) =>
    ({ status: "approved", stale: false, in_use_by: 1, ...over }) as Image;

  it("counts in-use images that are flagged, revoked or stale", () => {
    expect(
      countImagesAtRisk([
        row({ status: "flagged" }),
        row({ status: "revoked" }),
        row({ stale: true }),
        row({}),
      ]),
    ).toBe(3);
  });

  it("ignores images nobody uses and unknown staleness", () => {
    expect(countImagesAtRisk([row({ status: "flagged", in_use_by: 0 }), row({ stale: null })])).toBe(0);
    expect(countImagesAtRisk(undefined)).toBe(0);
  });
});

describe("describeImagesAtRisk (C-3, controller ruling F5 — reads as an image count)", () => {
  it("uses the singular for exactly one image", () => {
    expect(describeImagesAtRisk(1)).toBe("1 in-use image flagged, revoked or stale");
  });

  it("uses the plural for more than one image", () => {
    expect(describeImagesAtRisk(3)).toBe("3 in-use images flagged, revoked or stale");
  });

  it("shows nothing at zero — the zero case has no line", () => {
    expect(describeImagesAtRisk(0)).toBeNull();
  });
});
