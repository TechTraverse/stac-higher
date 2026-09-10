import { describe, it, expect } from "vitest";
import { nodeHealth, unhealthyNodeIds } from "@/lib/monitoring/graph-decorate";
import type { GraphNode } from "@/lib/monitoring/graph-api";

const proc: GraphNode = { id: "proc:p1", type: "process", label: "masker", group_id: null, meta: { deployed: true } };

describe("unhealthyNodeIds", () => {
  it("indicts connection, collection and process nodes by their anchors", () => {
    const ids = unhealthyNodeIds([
      { kind: "connection_error", connection_id: "c1" },
      { kind: "push_rejected", collection_id: "prod-a" },
      { kind: "process_failed", process_id: "p1" },
    ]);
    expect([...ids].sort()).toEqual(["coll:prod-a", "conn:c1", "proc:p1"]);
  });
});

describe("nodeHealth", () => {
  it("a deployed process with no alert is ok (I-84 — was unknown)", () => {
    expect(nodeHealth(proc, new Set())).toBe("ok");
  });
  it("a process named by an alert is an error", () => {
    expect(nodeHealth(proc, new Set(["proc:p1"]))).toBe("error");
  });
  it("an undeployed process is still a warning", () => {
    expect(nodeHealth({ ...proc, meta: { deployed: false } }, new Set())).toBe("warn");
  });

  it("a process node is unknown on an incomplete alert list (I-84 fix-round-1)", () => {
    expect(nodeHealth(proc, new Set(), false)).toBe("unknown");
  });
  it("an alert still wins over an incomplete list", () => {
    expect(nodeHealth(proc, new Set(["proc:p1"]), false)).toBe("error");
  });
  it("an undeployed process still wins over an incomplete list", () => {
    expect(
      nodeHealth({ ...proc, meta: { deployed: false } }, new Set(), false),
    ).toBe("warn");
  });
});
