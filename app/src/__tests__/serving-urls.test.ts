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
  CUBE_DEFAULT_STYLE,
  cubeServerBaseUrl,
  cubeTileUrlTemplate,
  cubeEdrPositionUrl,
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

describe("cube-server urls", () => {
  it("defaults the base and strips trailing slashes", () => {
    expect(cubeServerBaseUrl()).toBe("http://localhost:8086");
    expect(cubeServerBaseUrl({ cubeServerUrl: "https://cube.example/" })).toBe(
      "https://cube.example",
    );
  });

  it("defaults the style to Greys (cold = white) over 190-310 K", () => {
    expect(CUBE_DEFAULT_STYLE).toEqual({ colormap: "Greys", range: [190, 310] });
  });

  it("builds the tile template row-first with the exact t and a colorscalerange", () => {
    expect(
      cubeTileUrlTemplate("goes19-c13-cube", "CMI", "2026-10-03T17:02:36.714359936Z", undefined, "http://c"),
    ).toBe(
      "http://c/datasets/goes19-c13-cube/tiles/WebMercatorQuad/{z}/{y}/{x}" +
        "?variables=CMI&t=2026-10-03T17%3A02%3A36.714359936Z&style=raster%2FGreys" +
        "&colorscalerange=190%2C310&width=256&height=256&f=png",
    );
  });

  it("encodes the collection id in the path", () => {
    expect(cubeTileUrlTemplate("a b/c", "CMI", "t", undefined, "http://c")).toContain(
      "/datasets/a%20b%2Fc/tiles/",
    );
    expect(cubeEdrPositionUrl("a b/c", 1, 2, "CMI", "http://c")).toContain("/datasets/a%20b%2Fc/edr/");
  });

  it("builds the EDR position url with the point rounded to 4 decimals", () => {
    expect(cubeEdrPositionUrl("goes19-c13-cube", -97.123456, 38.5, "CMI", "http://c")).toBe(
      "http://c/datasets/goes19-c13-cube/edr/position?coords=POINT(-97.1235%2038.5)&parameter-name=CMI&f=csv",
    );
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

describe("roundCoord", () => {
  it("rounds to 4 decimals and is idempotent", async () => {
    const { roundCoord } = await import("@/lib/serving/urls");
    expect(roundCoord(-97.1234561)).toBe(-97.1235);
    expect(roundCoord(roundCoord(38.5000004))).toBe(38.5);
  });
});
