import { describe, it, expect } from "vitest";
import type { StacItem } from "@/lib/stac-api/types";
import { buildPreviewFrames } from "@/lib/serving/frames";
import { previewAssetCandidates } from "@/lib/serving/preview";
import { collectionTileUrlTemplate, collectionViewerUrl } from "@/lib/serving/urls";

function item(
  id: string,
  properties: Partial<StacItem["properties"]> = {},
  assets: StacItem["assets"] = {},
): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime: null, ...properties },
    links: [],
    assets,
  };
}

describe("buildPreviewFrames", () => {
  it("orders frames chronologically, oldest first", () => {
    const frames = buildPreviewFrames([
      item("c", { datetime: "2026-09-04T06:11:17.300000Z" }),
      item("a", { datetime: "2026-09-04T06:01:17.300000Z" }),
      item("b", { datetime: "2026-09-04T06:06:17.300000Z" }),
    ]);

    expect(frames.map((f) => f.itemIds[0])).toEqual(["a", "b", "c"]);
  });

  it("passes the catalog's datetime through VERBATIM", () => {
    // The tile server matches the instant exactly: `…17.3Z` renders and
    // `…17Z` returns 204. Any normalisation here silently empties the map.
    const [frame] = buildPreviewFrames([
      item("a", { datetime: "2026-09-04T06:11:17.300000Z" }),
    ]);

    expect(frame.datetime).toBe("2026-09-04T06:11:17.300000Z");
  });

  it("collapses items sharing a timestep into one frame", () => {
    const frames = buildPreviewFrames([
      item("west", { datetime: "2026-09-04T06:11:17.300000Z" }),
      item("east", { datetime: "2026-09-04T06:11:17.300000Z" }),
    ]);

    expect(frames).toHaveLength(1);
    expect(frames[0].itemIds).toEqual(["west", "east"]);
  });

  it("falls back to the start/end interval when datetime is null", () => {
    const [frame] = buildPreviewFrames([
      item("a", {
        datetime: null,
        start_datetime: "2026-09-04T06:00:00Z",
        end_datetime: "2026-09-04T06:05:00Z",
      }),
    ]);

    expect(frame.datetime).toBe("2026-09-04T06:00:00Z/2026-09-04T06:05:00Z");
  });

  it("skips items with no usable time at all", () => {
    expect(buildPreviewFrames([item("a"), item("b", { datetime: "nonsense" })])).toEqual(
      [],
    );
  });

  it("labels frames in UTC", () => {
    const [frame] = buildPreviewFrames([
      item("a", { datetime: "2026-09-04T06:11:17.300000Z" }),
    ]);

    expect(frame.label).toBe("2026-09-04 06:11 UTC");
  });
});

describe("previewAssetCandidates", () => {
  it("puts a visual-role key first and dedupes across items", () => {
    const assets = {
      data: { href: "d.tif", type: "image/tiff; application=geotiff" },
      visual: { href: "v.tif", roles: ["visual"] },
    };

    expect(previewAssetCandidates([item("a", {}, assets), item("b", {}, assets)])).toEqual(
      ["visual", "data"],
    );
  });

  it("returns nothing when no asset is tileable", () => {
    const assets = { thumb: { href: "t.png", type: "image/png" } };
    expect(previewAssetCandidates([item("a", {}, assets)])).toEqual([]);
  });
});

describe("collection tile URLs", () => {
  const BASE = "http://localhost:8084";

  it("keeps the maplibre placeholders literal and encodes the query", () => {
    const url = collectionTileUrlTemplate(
      "goes-geocolor",
      "visual",
      "2026-09-04T06:11:17.300000Z",
      BASE,
    );

    expect(url).toBe(
      `${BASE}/collections/goes-geocolor/tiles/WebMercatorQuad/{z}/{x}/{y}@1x` +
        "?assets=visual&datetime=2026-09-04T06%3A11%3A17.300000Z",
    );
  });

  it("encodes an interval frame's slash", () => {
    const url = collectionTileUrlTemplate(
      "c",
      "visual",
      "2026-09-04T06:00:00Z/2026-09-04T06:05:00Z",
      BASE,
    );

    expect(url).toContain("datetime=2026-09-04T06%3A00%3A00Z%2F2026-09-04T06%3A05%3A00Z");
  });

  it("builds the tile server's own viewer URL, pinned to the frame", () => {
    expect(
      collectionViewerUrl("goes-geocolor", "visual", "2026-09-04T06:11:17.300000Z", BASE),
    ).toBe(
      `${BASE}/collections/goes-geocolor/WebMercatorQuad/map` +
        "?assets=visual&datetime=2026-09-04T06%3A11%3A17.300000Z",
    );
  });

  it("omits datetime from the viewer URL when no frame is given", () => {
    expect(collectionViewerUrl("goes-geocolor", "visual", undefined, BASE)).toBe(
      `${BASE}/collections/goes-geocolor/WebMercatorQuad/map?assets=visual`,
    );
  });
});
