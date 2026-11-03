import { describe, it, expect, afterEach, vi } from "vitest";
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
  afterEach(() => {
    delete globalThis.__STAC_HIGHER_CONFIG__;
    vi.unstubAllEnvs();
  });

  it("defaults the bases and strips a trailing slash", () => {
    expect(titilerBaseUrl()).toBe("http://localhost:8084");
    expect(titilerBaseUrl({ titilerUrl: "https://tiles.example.org/" })).toBe("https://tiles.example.org");
    expect(tipgBaseUrl()).toBe("http://localhost:8085");
    expect(tipgBaseUrl({ tipgUrl: "https://features.example.org//" })).toBe("https://features.example.org");
  });

  it("reads the server-injected config, not the build-time env (no rebuild for a URL change)", () => {
    vi.stubEnv("PUBLIC_TITILER_URL", "https://inlined.example.org");
    expect(titilerBaseUrl()).toBe("http://localhost:8084");
    globalThis.__STAC_HIGHER_CONFIG__ = { titilerUrl: "https://runtime.example.org", tipgUrl: "https://vt.example.org" };
    expect(titilerBaseUrl()).toBe("https://runtime.example.org");
    expect(tipgLandingUrl()).toBe("https://vt.example.org/");
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
