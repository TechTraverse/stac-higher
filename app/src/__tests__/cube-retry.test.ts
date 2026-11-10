import { describe, it, expect } from "vitest";
import { frameIndexFromSourceId, nextRetry } from "@/lib/serving/cube-retry";

describe("cube retry", () => {
  it("counts attempts per t and stops at the limit", () => {
    expect(nextRetry({}, "a")).toEqual({ a: 1 });
    expect(nextRetry({ a: 1, b: 2 }, "a")).toEqual({ a: 2, b: 2 });
    expect(nextRetry({ a: 3 }, "a")).toBeNull();
  });

  it("reads the frame index from a stack source id", () => {
    expect(frameIndexFromSourceId("cube", "cube-frame-12")).toBe(12);
    expect(frameIndexFromSourceId("cube", "other-frame-1")).toBeNull();
    expect(frameIndexFromSourceId("cube", undefined)).toBeNull();
    expect(frameIndexFromSourceId("cube", "cube-frame-x")).toBeNull();
  });
});
