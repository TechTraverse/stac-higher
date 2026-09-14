import { describe, it, expect } from "vitest";
import { buildAxis, frameInstant, resolveLayerFrame } from "@/lib/map/axis";
import { buildPreviewFrames, type PreviewFrame } from "@/lib/serving/frames";
import type { StacItem } from "@/lib/stac-api/types";

/** A frame as `buildPreviewFrames` would emit it, without going through items. */
function frame(datetime: string, itemIds: string[] = ["i"]): PreviewFrame {
  const start = datetime.includes("/") ? datetime.split("/")[0] : datetime;
  const iso = new Date(Date.parse(start)).toISOString();
  return { datetime, label: `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`, itemIds };
}

const GEOCOLOR = [
  frame("2026-09-04T06:00:00Z"),
  frame("2026-09-04T06:05:00Z"),
  frame("2026-09-04T06:10:00Z"),
];
// A slower product, offset from the fast one, sharing exactly one instant.
const MCMIPC = [frame("2026-09-04T06:05:00Z"), frame("2026-09-04T06:20:00Z")];

describe("buildAxis", () => {
  it("is the sorted union of every layer's frame instants", () => {
    const axis = buildAxis(
      new Map([
        ["b", MCMIPC],
        ["a", GEOCOLOR],
      ]),
    );

    expect(axis.map((t) => t.label)).toEqual([
      "2026-09-04 06:00 UTC",
      "2026-09-04 06:05 UTC",
      "2026-09-04 06:10 UTC",
      "2026-09-04 06:20 UTC",
    ]);
    expect(axis.map((t) => t.instant)).toEqual([...axis].sort((x, y) => x.instant - y.instant).map((t) => t.instant));
  });

  it("collapses an instant two layers share into one tick", () => {
    const axis = buildAxis(new Map([["a", GEOCOLOR], ["b", MCMIPC]]));
    const shared = axis.filter((t) => t.instant === Date.parse("2026-09-04T06:05:00Z"));

    expect(shared).toHaveLength(1);
  });

  it("labels a tick exactly as buildPreviewFrames labels its frame", () => {
    // The bar reads one label; two spellings of the same instant would look
    // like two products disagreeing about the time.
    const items: StacItem[] = [
      {
        type: "Feature",
        stac_version: "1.0.0",
        id: "a",
        collection: "goes-geocolor",
        geometry: null,
        properties: { datetime: "2026-09-04T06:11:17.300000Z" },
        links: [],
        assets: {},
      },
    ];
    const frames = buildPreviewFrames(items);
    const [tick] = buildAxis(new Map([["a", frames]]));

    expect(tick.label).toBe(frames[0].label);
  });

  it("takes the START of an interval-form datetime", () => {
    const [tick] = buildAxis(
      new Map([["a", [frame("2026-09-04T06:00:00Z/2026-09-04T06:05:00Z")]]]),
    );

    expect(tick.instant).toBe(Date.parse("2026-09-04T06:00:00Z"));
    expect(tick.label).toBe("2026-09-04 06:00 UTC");
  });

  it("is empty for no layers and for layers with no frames", () => {
    expect(buildAxis(new Map())).toEqual([]);
    expect(buildAxis(new Map([["a", []]]))).toEqual([]);
  });
});

describe("frameInstant", () => {
  it("parses a plain instant and an interval start alike", () => {
    expect(frameInstant(frame("2026-09-04T06:00:00Z"))).toBe(Date.parse("2026-09-04T06:00:00Z"));
    expect(frameInstant(frame("2026-09-04T06:00:00Z/2026-09-04T06:05:00Z"))).toBe(
      Date.parse("2026-09-04T06:00:00Z"),
    );
  });
});

describe("resolveLayerFrame", () => {
  it("reproduces a single layer's own indices when the axis is its own frames", () => {
    const axis = buildAxis(new Map([["a", GEOCOLOR]]));

    expect(axis.map((t) => resolveLayerFrame(t.instant, GEOCOLOR))).toEqual([0, 1, 2]);
  });

  it("holds the last frame at or before the tick", () => {
    // The union's 06:00 and 06:10 ticks are not this layer's; the slow product
    // keeps showing 06:05 until its own next frame arrives.
    const axis = buildAxis(new Map([["a", GEOCOLOR], ["b", MCMIPC]]));

    expect(axis.map((t) => resolveLayerFrame(t.instant, MCMIPC))).toEqual([null, 0, 0, 1]);
  });

  it("returns null when the layer has nothing that early", () => {
    expect(resolveLayerFrame(Date.parse("2026-09-04T05:59:00Z"), GEOCOLOR)).toBeNull();
  });

  it("returns null for an empty series", () => {
    expect(resolveLayerFrame(Date.parse("2026-09-04T06:00:00Z"), [])).toBeNull();
  });

  it("never returns an index outside the series", () => {
    // A stack handed frames[-1] throws and frames[count] paints everything at
    // zero opacity, which reads as "the tiler is down".
    for (const instant of [0, Date.parse("2999-01-01T00:00:00Z")]) {
      const resolved = resolveLayerFrame(instant, GEOCOLOR);
      expect(resolved === null || (resolved >= 0 && resolved < GEOCOLOR.length)).toBe(true);
    }
    expect(resolveLayerFrame(Date.parse("2999-01-01T00:00:00Z"), GEOCOLOR)).toBe(2);
  });
});
