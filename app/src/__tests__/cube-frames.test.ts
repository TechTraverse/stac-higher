import { describe, it, expect } from "vitest";
import {
  buildCubeFrames,
  cubeTimeMs,
  cubeVariable,
  findCubeAsset,
  CUBE_MEDIA_TYPE,
} from "@/lib/serving/cube-frames";
import type { StacCollection } from "@/lib/stac-api/types";

function collection(extra: Record<string, unknown> = {}, assets?: Record<string, unknown>) {
  return { type: "Collection", id: "c", assets, ...extra } as unknown as StacCollection;
}

describe("buildCubeFrames", () => {
  it("sorts oldest first, drops junk and keeps the nanosecond t verbatim", () => {
    const frames = buildCubeFrames([
      "2026-10-03T17:07:36.714359936Z",
      "2026-10-03T17:02:36.714359936Z",
      "bogus",
    ]);
    expect(frames).toEqual([
      { t: "2026-10-03T17:02:36.714359936Z", label: "2026-10-03 17:02 UTC" },
      { t: "2026-10-03T17:07:36.714359936Z", label: "2026-10-03 17:07 UTC" },
    ]);
  });

  it("keeps the newest `last` frames, all when undefined", () => {
    const values = Array.from(
      { length: 30 },
      (_, i) => `2026-10-03T${String(Math.floor(i / 6)).padStart(2, "0")}:${String((i % 6) * 10).padStart(2, "0")}:00.000000000Z`,
    );
    const last = buildCubeFrames(values, 24);
    expect(last).toHaveLength(24);
    expect(last[23].t).toBe(values[29]);
    expect(last[0].t).toBe(values[6]);
    expect(buildCubeFrames(values)).toHaveLength(30);
  });

  it("collapses duplicates", () => {
    const t = "2026-10-03T17:02:36.714359936Z";
    expect(buildCubeFrames([t, t])).toHaveLength(1);
  });

  it("parses nanosecond fractions without relying on the engine", () => {
    expect(cubeTimeMs("2026-10-03T17:02:36.714359936Z")).toBe(Date.parse("2026-10-03T17:02:36.714Z"));
    expect(cubeTimeMs("nope")).toBeNaN();
  });
});

describe("findCubeAsset", () => {
  it("returns the cube asset's string time values", () => {
    const c = collection({}, {
      cube: { href: "s3://x", type: CUBE_MEDIA_TYPE, "stac_higher:time_values": ["a", 1, "b"] },
    });
    expect(findCubeAsset(c)).toEqual({ key: "cube", timeValues: ["a", "b"] });
  });

  it("is null for a collection without a cube asset", () => {
    expect(findCubeAsset(collection({}, { visual: { href: "x", type: "image/tiff" } }))).toBeNull();
    expect(findCubeAsset(collection())).toBeNull();
  });

  it("tolerates a cube asset with no time_values", () => {
    const c = collection({}, { cube: { href: "s3://x", type: CUBE_MEDIA_TYPE } });
    expect(findCubeAsset(c)).toEqual({ key: "cube", timeValues: [] });
  });
});

describe("cubeVariable", () => {
  it("takes the first data variable", () => {
    expect(
      cubeVariable(collection({ "cube:variables": { DQF: { type: "auxiliary" }, CMI: { type: "data" } } })),
    ).toBe("CMI");
    expect(cubeVariable(collection({ "cube:variables": { Rad: { type: "data" } } }))).toBe("Rad");
  });

  it("falls back to CMI", () => {
    expect(cubeVariable(collection())).toBe("CMI");
  });
});
