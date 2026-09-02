import { describe, it, expect } from "vitest";
import {
  NETWORK_LEVEL_NOT_YET_AVAILABLE,
  processRuntimeReadSchema,
  processRuntimeSchema,
} from "@/lib/processes/schemas";
import { getNetworkMax, networkLevelWithinCap } from "@/lib/processes/network";

/**
 * The `runtime.network` block (GOES spec §4, ADR 0018) and the deployment cap.
 * Slice 1 ships the FIELD with `isolated` only: the read schema carries every
 * level so a revision written now keeps its meaning when the egress proxy
 * lands; the write gate refuses anything higher until then.
 */

describe("runtime.network", () => {
  it("defaults to isolated with no hosts", () => {
    const rt = processRuntimeSchema.parse({ kind: "inline_python" });
    expect(rt.network).toEqual({ level: "isolated", hosts: [] });
  });

  it("read schema accepts every level; write gate accepts only isolated this slice", () => {
    for (const level of ["inputs", "open"]) {
      expect(
        processRuntimeReadSchema.safeParse({ kind: "inline_python", network: { level } }).success,
      ).toBe(true);
      const write = processRuntimeSchema.safeParse({ kind: "inline_python", network: { level } });
      expect(write.success).toBe(false);
      expect(write.error?.issues.map((i) => i.message)).toContain(NETWORK_LEVEL_NOT_YET_AVAILABLE);
    }
  });

  it("hosts and the hosts level imply each other", () => {
    const ok = processRuntimeReadSchema.safeParse({
      kind: "inline_python",
      network: { level: "hosts", hosts: ["api.example.com"] },
    });
    expect(ok.success).toBe(true);
    for (const network of [
      { level: "hosts", hosts: [] },
      { level: "open", hosts: ["x.y"] },
      { level: "hosts", hosts: ["https://x.y"] },
      { level: "lan" },
    ]) {
      expect(processRuntimeReadSchema.safeParse({ kind: "inline_python", network }).success).toBe(
        false,
      );
    }
  });
});

describe("PROCESS_NETWORK_MAX", () => {
  it("defaults to isolated and validates", () => {
    expect(getNetworkMax({})).toBe("isolated");
    expect(getNetworkMax({ PROCESS_NETWORK_MAX: "hosts" })).toBe("hosts");
    expect(() => getNetworkMax({ PROCESS_NETWORK_MAX: "lan" })).toThrow(/PROCESS_NETWORK_MAX/);
  });

  it("orders levels", () => {
    expect(networkLevelWithinCap("isolated", "isolated")).toBe(true);
    expect(networkLevelWithinCap("open", "hosts")).toBe(false);
    expect(networkLevelWithinCap("inputs", "hosts")).toBe(true);
  });
});
