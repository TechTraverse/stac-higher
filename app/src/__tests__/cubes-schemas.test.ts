// @vitest-environment node
/** Z-2: the cube sink write gate beyond the fixture cases. */
import { describe, it, expect } from "vitest";
import {
  cubeSinkConfigSchema,
  cubeSinkPatchSchema,
  cubeSinkPutSchema,
  durationSeconds,
  layoutChanged,
} from "@/lib/cubes/schemas";

const base = { append_dim: "t", variables: ["CMI"], loadable_variables: ["t"] };

describe("durationSeconds", () => {
  it("parses minutes, hours and days", () => {
    expect(durationSeconds("90m")).toBe(5400);
    expect(durationSeconds("24h")).toBe(86400);
    expect(durationSeconds("30d")).toBe(2_592_000);
  });
  it("returns null for anything outside ^\\d+[mhd]$", () => {
    for (const bad of ["", "24", "1w", "1.5h", " 1h", "-1h", "24 hours"]) {
      expect(durationSeconds(bad)).toBeNull();
    }
  });
});

describe("cubeSinkConfigSchema", () => {
  it("accepts exactly 64 variables and rejects 65", () => {
    const names = (n: number) => Array.from({ length: n }, (_, i) => `v${i}`);
    expect(cubeSinkConfigSchema.safeParse({ ...base, variables: names(64) }).success).toBe(true);
    expect(cubeSinkConfigSchema.safeParse({ ...base, variables: names(65) }).success).toBe(false);
  });
  it("accepts 30d and rejects 31d", () => {
    expect(cubeSinkConfigSchema.safeParse({ ...base, window: { max_age: "30d" } }).success).toBe(true);
    expect(cubeSinkConfigSchema.safeParse({ ...base, window: { max_age: "31d" } }).success).toBe(false);
  });
});

describe("cubeSinkPutSchema / cubeSinkPatchSchema", () => {
  it("defaults enabled to true and trims the source id", () => {
    const parsed = cubeSinkPutSchema.parse({ source_collection_id: " goes19-cmipc ", config: base });
    expect(parsed.enabled).toBe(true);
    expect(parsed.source_collection_id).toBe("goes19-cmipc");
  });
  it("rejects unknown keys and a missing source", () => {
    expect(cubeSinkPutSchema.safeParse({ config: base }).success).toBe(false);
    expect(cubeSinkPutSchema.safeParse({ source_collection_id: "s", config: base, x: 1 }).success).toBe(false);
  });
  it("PATCH takes only enabled", () => {
    expect(cubeSinkPatchSchema.safeParse({ enabled: false }).success).toBe(true);
    expect(cubeSinkPatchSchema.safeParse({ enabled: "no" }).success).toBe(false);
    expect(cubeSinkPatchSchema.safeParse({ enabled: true, config: base }).success).toBe(false);
  });
});

describe("layoutChanged", () => {
  const stored = cubeSinkConfigSchema.parse({ ...base, window: { max_steps: 72 } });
  it("ignores window, asset_key and on_late", () => {
    const next = cubeSinkConfigSchema.parse({ ...base, window: { max_age: "6h" }, asset_key: "c13" });
    expect(layoutChanged(stored, next)).toBe(false);
  });
  it("flags parser, append_dim, variables and loadable_variables", () => {
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ ...base, variables: ["CMI", "DQF"] }))).toBe(true);
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ ...base, loadable_variables: ["t", "x"] }))).toBe(true);
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ append_dim: "time", variables: ["CMI"], loadable_variables: ["time"] }))).toBe(true);
  });
  it("treats a reordered variable list as unchanged", () => {
    const a = cubeSinkConfigSchema.parse({ ...base, variables: ["CMI", "DQF"] });
    const b = cubeSinkConfigSchema.parse({ ...base, variables: ["DQF", "CMI"] });
    expect(layoutChanged(a, b)).toBe(false);
  });
});
