import { describe, it, expect } from "vitest";
import { frameIndexFromWarmId, nextWarmFrames, warmSourceId } from "@/lib/serving/cube-warm";

const F = ["a", "b", "c", "d", "e"];
const none = new Set<string>();

describe("nextWarmFrames", () => {
  it("returns playback order after the current frame, wrapping", () => {
    expect(nextWarmFrames(F, none, none, [], 3, 4)).toEqual(["a", "b", "c"]);
    expect(nextWarmFrames(F, none, none, [], 3, 1)).toEqual(["c", "d", "e"]);
  });
  it("caps at the free slots", () => {
    expect(nextWarmFrames(F, none, none, ["x"], 2, 4)).toEqual(["a"]);
    expect(nextWarmFrames(F, none, none, ["x", "y"], 2, 4)).toEqual([]);
  });
  it("skips loaded, mounted and active frames, and the current one", () => {
    expect(nextWarmFrames(F, new Set(["a"]), new Set(["b"]), ["c"], 3, 4)).toEqual(["d"]);
  });
  it("returns nothing when all are done or the list is empty", () => {
    expect(nextWarmFrames(F, new Set(F), none, [], 2, 0)).toEqual([]);
    expect(nextWarmFrames([], none, none, [], 2, 0)).toEqual([]);
  });
});

describe("warm source ids", () => {
  it("round-trips and rejects others", () => {
    expect(frameIndexFromWarmId(warmSourceId(7))).toBe(7);
    expect(frameIndexFromWarmId("cube-frame-7")).toBeNull();
    expect(frameIndexFromWarmId("cube-warm-x")).toBeNull();
    expect(frameIndexFromWarmId(undefined)).toBeNull();
  });
});
