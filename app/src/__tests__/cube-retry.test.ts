import { describe, it, expect } from "vitest";
import {
  frameIndexFromSourceId,
  isNotYetTileBody,
  isNotYetTileError,
  nextRetry,
} from "@/lib/serving/cube-retry";

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

  // Bodies captured from cube-server (xpublish-tiles 0.9.2) on 2026-11-10.
  const NOT_YET = JSON.stringify({
    detail:
      "Invalid indexer: {'t': '1999-01-01T00:00:00.000000000Z'}. '1999-01-01T00:00:00.000000000Z' is not a valid value for 't'.",
  });
  const BAD_STYLE = JSON.stringify({
    detail: [
      {
        type: "value_error",
        loc: ["query", "style"],
        msg: "Value error, variant 'gray_r' is not supported for style 'raster'.",
      },
    ],
  });

  it("retries only a 422 about the t selector", () => {
    expect(isNotYetTileError(NOT_YET)).toBe(true);
    expect(isNotYetTileError(BAD_STYLE)).toBe(false);
    expect(isNotYetTileError(JSON.stringify({ detail: "Invalid colormap: nope" }))).toBe(false);
    expect(isNotYetTileError("<html>proxy error</html>")).toBe(true);
  });

  it("reads maplibre's Blob body", async () => {
    expect(await isNotYetTileBody(new Blob([NOT_YET]))).toBe(true);
    expect(await isNotYetTileBody(new Blob([BAD_STYLE]))).toBe(false);
  });
});
