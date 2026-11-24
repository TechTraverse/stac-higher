import { describe, it, expect, vi, afterEach } from "vitest";
import { edrTimeToIso, fetchCubePosition, parseEdrPositionCsv } from "@/lib/serving/cube-edr";

afterEach(() => vi.unstubAllGlobals());

describe("edrTimeToIso", () => {
  it("converts the naive nanosecond form", () => {
    expect(edrTimeToIso("2026-10-03 17:02:36.714359936")).toBe("2026-10-03T17:02:36.714Z");
    expect(edrTimeToIso("2026-10-03 17:02:36")).toBe("2026-10-03T17:02:36.000Z");
    expect(edrTimeToIso("junk")).toBeNull();
  });
});

describe("parseEdrPositionCsv", () => {
  const csv =
    "t,CMI,longitude,latitude\n" +
    "2026-10-03 17:02:36.714359936,251.5,-97,38.5\n" +
    "2026-10-03 17:07:36.714359936,,-97,38.5\n";

  it("parses values and empty cells as null", () => {
    expect(parseEdrPositionCsv(csv, "CMI")).toEqual([
      { t: Date.parse("2026-10-03T17:02:36.714Z"), value: 251.5 },
      { t: Date.parse("2026-10-03T17:07:36.714Z"), value: null },
    ]);
  });

  it("finds the variable by header name", () => {
    const reordered =
      "longitude,CMI,latitude,t\n" +
      "-97,251.5,38.5,2026-10-03 17:02:36.714359936\n" +
      "-97,,38.5,2026-10-03 17:07:36.714359936\n";
    expect(parseEdrPositionCsv(reordered, "CMI")).toEqual(parseEdrPositionCsv(csv, "CMI"));
  });

  it("returns [] when the variable is missing, ignores blank and bad rows", () => {
    expect(parseEdrPositionCsv(csv, "DQF")).toEqual([]);
    expect(parseEdrPositionCsv("", "CMI")).toEqual([]);
    expect(parseEdrPositionCsv(csv + "\nnot-a-time,1,2,3\n\n", "CMI")).toHaveLength(2);
  });
});

describe("fetchCubePosition", () => {
  function stubFetch(res: Response) {
    const fn = vi.fn().mockResolvedValue(res);
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  it("returns points and the missing-steps count", async () => {
    const fn = stubFetch(
      new Response("t,CMI,longitude,latitude\n2026-10-03 17:02:36,1,0,0\n", {
        status: 200,
        headers: { "x-cube-missing-steps": "1" },
      }),
    );
    const out = await fetchCubePosition("c", -97, 38.5, "CMI");
    expect(out).toEqual({ points: [{ t: Date.parse("2026-10-03T17:02:36.000Z"), value: 1 }], missingSteps: 1 });
    expect(String(fn.mock.calls[0][0])).toContain("/datasets/c/edr/position?coords=POINT(-97%2038.5)");
  });

  it("treats a missing header as 0", async () => {
    stubFetch(new Response("t,CMI,longitude,latitude\n", { status: 200 }));
    expect((await fetchCubePosition("c", 0, 0, "CMI")).missingSteps).toBe(0);
  });

  it("throws with the status on a 404", async () => {
    stubFetch(new Response("no", { status: 404 }));
    await expect(fetchCubePosition("c", 0, 0, "CMI")).rejects.toThrow("404");
  });
});
