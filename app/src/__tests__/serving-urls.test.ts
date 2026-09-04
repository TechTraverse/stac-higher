import { describe, it, expect } from "vitest";
import {
  titilerBaseUrl,
  itemTileJsonUrl,
  itemViewerUrl,
  collectionInfoUrl,
  tipgBaseUrl,
  tipgLandingUrl,
  tipgCollectionsUrl,
  tipgTileJsonUrl,
} from "@/lib/serving/urls";
import { pickPreviewAsset } from "@/lib/serving/preview";
import type { StacItem } from "@/lib/stac-api/types";

describe("serving urls", () => {
  it("defaults the base and strips a trailing slash", () => {
    expect(titilerBaseUrl({})).toBe("http://localhost:8084");
    expect(titilerBaseUrl({ PUBLIC_TITILER_URL: "https://tiles.example/" })).toBe(
      "https://tiles.example",
    );
  });

  it("builds the item tilejson url with encoded segments", () => {
    expect(itemTileJsonUrl("my coll", "it/em", "visual", "http://t")).toBe(
      "http://t/collections/my%20coll/items/it%2Fem/WebMercatorQuad/tilejson.json?assets=visual",
    );
  });

  it("builds the item viewer url with encoded segments", () => {
    expect(itemViewerUrl("c", "i", "visual", "http://t")).toBe(
      "http://t/collections/c/items/i/WebMercatorQuad/map?assets=visual",
    );
  });

  it("builds the collection info url", () => {
    expect(collectionInfoUrl("c", "http://t")).toBe("http://t/collections/c/info");
  });

  it("defaults the tipg base and strips a trailing slash", () => {
    expect(tipgBaseUrl({})).toBe("http://localhost:8085");
    expect(tipgBaseUrl({ PUBLIC_TIPG_URL: "https://features.example//" })).toBe(
      "https://features.example",
    );
  });

  it("builds the tipg landing, collections and tilejson urls", () => {
    expect(tipgLandingUrl("http://f")).toBe("http://f/");
    expect(tipgCollectionsUrl("http://f")).toBe("http://f/collections");
    expect(tipgTileJsonUrl("public.roads", "http://f")).toBe(
      "http://f/collections/public.roads/tiles/WebMercatorQuad/tilejson.json",
    );
    expect(tipgTileJsonUrl("a b", "http://f")).toContain("/collections/a%20b/");
  });
});

function item(assets: StacItem["assets"]): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id: "i",
    geometry: null,
    properties: { datetime: null },
    links: [],
    assets,
  };
}

describe("pickPreviewAsset", () => {
  it("prefers the visual role", () => {
    expect(
      pickPreviewAsset(
        item({
          data: { href: "a.tif", type: "image/tiff" },
          visual: { href: "v.tif", roles: ["visual"] },
        }),
      ),
    ).toBe("visual");
  });

  it("falls back to a GeoTIFF/COG typed asset", () => {
    expect(
      pickPreviewAsset(
        item({
          thumb: { href: "t.png", type: "image/png" },
          data: {
            href: "a.tif",
            type: "image/tiff; application=geotiff; profile=cloud-optimized",
          },
        }),
      ),
    ).toBe("data");
  });

  it("returns null when nothing is previewable", () => {
    expect(
      pickPreviewAsset(item({ meta: { href: "m.json", type: "application/json" } })),
    ).toBeNull();
  });

  it("returns null for an item with no assets at all", () => {
    expect(pickPreviewAsset(item({}))).toBeNull();
  });
});
