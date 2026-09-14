import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionSettingsMock, useItemTileJsonMock } = vi.hoisted(() => ({
  useItemsMock: vi.fn(),
  useCollectionSettingsMock: vi.fn(),
  useItemTileJsonMock: vi.fn(),
}));

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));

import { useLayerData } from "@/components/map/useLayerData";

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime },
    links: [],
    assets: {
      visual: { href: `/api/assets/goes-geocolor/${id}/visual.tif`, roles: ["visual"] },
      cmi: { href: `/api/assets/goes-geocolor/${id}/cmi.tif`, type: "image/tiff" },
    },
  };
}

const ITEMS = [
  item("c", "2026-09-04T06:11:17.300000Z"),
  item("b", "2026-09-04T06:06:17.300000Z"),
  item("a", "2026-09-04T06:01:17.300000Z"),
];

function layer(overrides: Partial<MapLayer> = {}): MapLayer {
  return {
    id: "l1",
    kind: "imagery",
    sourceId: "goes-geocolor",
    title: "GeoColor",
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useItemsMock.mockReturnValue({ data: { features: ITEMS }, isLoading: false });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({ data: { tiles: ["t"], minzoom: 2 }, isFetched: true });
});

describe("useLayerData", () => {
  it("asks the catalog for the page's span, newest first", () => {
    renderHook(() => useLayerData(layer(), "http://localhost:8081", 100));

    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: 100, sortby: "-datetime" }),
    );
  });

  it("derives frames oldest-first and the tileable asset keys", () => {
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(result.current.frames.map((f) => f.itemIds[0])).toEqual(["a", "b", "c"]);
    expect(result.current.candidates).toEqual(["visual", "cmi"]);
    expect(result.current.asset).toBe("visual");
  });

  it("keeps the layer's chosen asset while the collection still offers it", () => {
    const { result } = renderHook(() =>
      useLayerData(layer({ asset: "cmi" }), "http://localhost:8081", 50),
    );

    expect(result.current.asset).toBe("cmi");
  });

  it("falls back to the best candidate when the chosen asset is gone", () => {
    const { result } = renderHook(() =>
      useLayerData(layer({ asset: "retired" }), "http://localhost:8081", 50),
    );

    expect(result.current.asset).toBe("visual");
  });

  it("asks the tiler for the zoom hint of the newest item and chosen asset", () => {
    renderHook(() => useLayerData(layer({ asset: "cmi" }), "http://localhost:8081", 50));

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", "cmi", true);
  });

  it("passes the tiler's hint and settled flag straight through", () => {
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(result.current.hint?.minzoom).toBe(2);
    expect(result.current.hintSettled).toBe(true);
  });

  it("never asks the tiler for a footprints layer", () => {
    // Footprints need no tiles; a hint request per footprints layer would be
    // one wasted round trip per layer on a page built to hold several.
    const { result } = renderHook(() =>
      useLayerData(layer({ kind: "footprints" }), "http://localhost:8081", 50),
    );

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", null, false);
    expect(result.current.frames).toHaveLength(3);
  });

  it("asks for no hint when serving is off", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "c", null, false);
    expect(result.current.servingEnabled).toBe(false);
  });

  it("holds frame identity stable across renders", () => {
    // The page stores each layer's frames in state; a fresh array every render
    // would loop the page through setState forever.
    const { result, rerender } = renderHook(() =>
      useLayerData(layer(), "http://localhost:8081", 50),
    );
    const first = result.current.frames;
    rerender();

    expect(result.current.frames).toBe(first);
  });

  it("survives a collection with no items", () => {
    useItemsMock.mockReturnValue({ data: undefined, isLoading: true });
    const { result } = renderHook(() => useLayerData(layer(), "http://localhost:8081", 50));

    expect(result.current.items).toEqual([]);
    expect(result.current.frames).toEqual([]);
    expect(result.current.asset).toBeUndefined();
    expect(useItemTileJsonMock).toHaveBeenCalledWith("goes-geocolor", "", null, true);
  });
});
