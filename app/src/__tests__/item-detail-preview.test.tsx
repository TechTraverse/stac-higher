import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { StacItem } from "@/lib/stac-api/types";

const { useItemMock, useCollectionSettingsMock, useItemTileJsonMock } = vi.hoisted(
  () => ({
    useItemMock: vi.fn(),
    useCollectionSettingsMock: vi.fn(),
    useItemTileJsonMock: vi.fn(),
  }),
);

vi.mock("@/lib/query/items", () => ({
  useItem: () => useItemMock(),
  useDeleteItem: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => useCollectionSettingsMock(),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...args: unknown[]) => useItemTileJsonMock(...args),
}));
vi.mock("@/components/layout/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
// maplibre needs WebGL, so the whole binding is replaced with inert elements
// that keep the component tree (and the layer props) intact. The DEFAULT
// export is the Map component StacMap renders — omitting it makes every
// import of the shared map barrel fail.
vi.mock("react-map-gl/maplibre", () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="map">{children}</div>
  ),
  Source: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Layer: (props: Record<string, unknown>) => (
    <div
      data-testid={props.type === "raster" ? "raster-layer" : "vector-layer"}
      data-layer-id={String(props.id)}
      data-props={JSON.stringify(props)}
    />
  ),
  NavigationControl: () => <div />,
  ScaleControl: () => <div />,
}));

import { ItemDetailPage } from "@/components/items/ItemDetail";

function makeItem(assets: StacItem["assets"]): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id: "t1",
    collection: "g4-tiletest",
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [-100, 30],
          [-90, 30],
          [-90, 40],
          [-100, 40],
          [-100, 30],
        ],
      ],
    },
    bbox: [-100, 30, -90, 40],
    properties: { datetime: "2026-09-01T00:00:00Z" },
    links: [],
    assets,
  };
}

const VISUAL_ITEM = makeItem({
  visual: { href: "/api/assets/g4-tiletest/t1/visual.tif", roles: ["visual"] },
});

beforeEach(() => {
  useItemMock.mockReset();
  useCollectionSettingsMock.mockReset();
  useItemTileJsonMock.mockReset();
  useItemMock.mockReturnValue({ data: VISUAL_ITEM, isLoading: false, error: null, refetch: vi.fn() });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({ data: undefined, error: null });
});

function renderPage() {
  const utils = render(<ItemDetailPage collectionId="g4-tiletest" itemId="t1" />);
  // The preview lives on the Geometry tab, and Radix mounts only the active
  // tab's content — so a test about the map has to open it.
  // Radix activates a tab on mousedown, and mounts only the active tab's
  // content — so a test about the map has to open it.
  fireEvent.mouseDown(screen.getByRole("tab", { name: /geometry/i }), {
    button: 0,
    ctrlKey: false,
  });
  return utils;
}

describe("item page raster preview", () => {
  it("draws the layer and the caption when serving is on and tiles came back", () => {
    useItemTileJsonMock.mockReturnValue({
      data: { tiles: ["http://t/{z}/{x}/{y}"], bounds: [-100, 30, -90, 40] },
      error: null,
    });

    renderPage();

    expect(useItemTileJsonMock).toHaveBeenCalledWith("g4-tiletest", "t1", "visual", true);
    const layer = JSON.parse(
      screen.getByTestId("raster-layer").dataset.props as string,
    );
    expect(layer).toMatchObject({
      id: "stac-raster-preview-layer",
      source: "stac-raster-preview",
      beforeId: "item-geometry-fill",
    });
    expect(screen.getByText(/rendered by the tile server/i)).toBeInTheDocument();
  });

  it("adds the raster layer after the layer its beforeId names", () => {
    // maplibre REFUSES `beforeId` naming a layer that does not exist yet
    // ("Cannot add layer ... before non-existing layer"), which mocked
    // primitives happily accept — so the order is pinned here. Found live on
    // 2026-09-02: the raster rendered first and the layer was never added.
    useItemTileJsonMock.mockReturnValue({
      data: { tiles: ["http://t/{z}/{x}/{y}"], bounds: [-100, 30, -90, 40] },
      error: null,
    });

    renderPage();

    const ids = screen
      .getAllByTestId(/^(raster|vector)-layer$/)
      .map((el) => el.dataset.layerId);
    const target = JSON.parse(
      screen.getByTestId("raster-layer").dataset.props as string,
    ).beforeId as string;

    expect(ids).toContain(target);
    expect(ids.indexOf(target)).toBeLessThan(ids.indexOf("stac-raster-preview-layer"));
  });

  it("asks for nothing and draws nothing when serving is off", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });

    renderPage();

    expect(useItemTileJsonMock).toHaveBeenCalledWith("g4-tiletest", "t1", "visual", false);
    expect(screen.queryByTestId("raster-layer")).toBeNull();
    expect(screen.queryByText(/rendered by the tile server/i)).toBeNull();
  });

  it("passes a null asset key when the item has nothing previewable", () => {
    useItemMock.mockReturnValue({
      data: makeItem({ meta: { href: "m.json", type: "application/json" } }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPage();

    expect(useItemTileJsonMock).toHaveBeenCalledWith("g4-tiletest", "t1", null, true);
    expect(screen.queryByTestId("raster-layer")).toBeNull();
  });

  it("draws nothing when the tile server request failed", () => {
    useItemTileJsonMock.mockReturnValue({ data: undefined, error: new Error("404") });

    renderPage();

    expect(screen.queryByTestId("raster-layer")).toBeNull();
    expect(screen.queryByText(/rendered by the tile server/i)).toBeNull();
  });
});
