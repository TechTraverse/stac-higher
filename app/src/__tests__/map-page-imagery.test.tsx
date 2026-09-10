/**
 * `/map` (V-3, spec §4.4): the docked time bar exists only while a time-aware
 * layer does, and the tick it is parked on drives every layer on the map.
 *
 * Query hooks are mocked; what is under test is the page's wiring — frames up,
 * tick down — not the fetch.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionsMock, useCollectionSettingsMock, useItemTileJsonMock } =
  vi.hoisted(() => ({
    useItemsMock: vi.fn(),
    useCollectionsMock: vi.fn(),
    useCollectionSettingsMock: vi.fn(),
    useItemTileJsonMock: vi.fn(),
  }));

vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/query/collections", () => ({
  useCollections: (...a: unknown[]) => useCollectionsMock(...a),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: (...a: unknown[]) => useCollectionSettingsMock(...a),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));
vi.mock("@/stores/catalogStore", async () => {
  const { atom } = await import("nanostores");
  return {
    $builtInCatalog: atom({
      id: "built-in",
      name: "Built-in Catalog",
      url: "http://localhost:8081",
      isDefault: true,
      builtIn: true,
    }),
  };
});
// maplibre needs WebGL; inert stand-ins keep the tree — and the source/layer
// specs — intact.
vi.mock("react-map-gl/maplibre", () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="map">{children}</div>
  ),
  Source: ({ children, ...props }: Record<string, unknown> & { children?: React.ReactNode }) => (
    <div data-testid="source" data-props={JSON.stringify(props)}>
      {children}
    </div>
  ),
  Layer: (props: Record<string, unknown>) => (
    <div data-testid="layer" data-props={JSON.stringify(props)} />
  ),
  NavigationControl: () => <div />,
  ScaleControl: () => <div />,
}));

import { MapPage } from "@/components/map/MapPage";

const COLLECTION = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "goes-geocolor",
  title: "GOES GeoColor",
  description: "",
  license: "proprietary",
  extent: { spatial: { bbox: [[-137, 14, -52, 54]] }, temporal: { interval: [[null, null]] } },
  links: [],
} as unknown as StacCollection;

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: { type: "Polygon", coordinates: [[[-1, -1], [1, -1], [1, 1], [-1, -1]]] },
    properties: { datetime },
    links: [],
    assets: { visual: { href: `${id}.tif`, roles: ["visual"] } },
  } as unknown as StacItem;
}

const ITEMS = [
  item("c", "2026-09-04T06:10:00Z"),
  item("b", "2026-09-04T06:05:00Z"),
  item("a", "2026-09-04T06:00:00Z"),
];

beforeAll(() => {
  // The QueryProvider pulls in sonner, which reads matchMedia on mount.
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
  // Radix Select needs these in jsdom.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

beforeEach(() => {
  vi.clearAllMocks();
  // V-2's shape: useCollections(catalogUrl) → { data: { collections } }.
  useCollectionsMock.mockReturnValue({
    data: { collections: [COLLECTION] },
    isLoading: false,
  });
  useItemsMock.mockReturnValue({ data: { features: ITEMS }, isLoading: false });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({
    data: { tiles: ["ignored"], bounds: [-137, 14, -52, 54], minzoom: 2, maxzoom: 5 },
    isFetched: true,
  });
});

/** Open the Add-layer popover and add one kind, through V-2's test ids. */
async function addLayer(kind: "footprints" | "imagery") {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("map-add-layer"));
  await user.click(screen.getByTestId(`map-add-${kind}-goes-geocolor`));
  return user;
}

describe("MapPage — the shared time axis", () => {
  it("docks no time bar until a time-aware layer is added", async () => {
    render(<MapPage />);
    expect(screen.queryByTestId("map-time-bar")).toBeNull();

    await addLayer("footprints");

    expect(screen.getByTestId("map-time-bar")).toBeTruthy();
    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("undocks the bar when the last time-aware layer goes", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");
    expect(screen.getByTestId("map-time-bar")).toBeTruthy();

    await user.click(screen.getByTestId("map-layer-remove"));

    // The removed layer withdraws its frames, so the axis empties with it.
    expect(screen.queryByTestId("map-time-bar")).toBeNull();
  });

  it("draws only the current frame's footprints", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");

    const features = () =>
      JSON.parse(
        screen.getAllByTestId("source").map((el) => el.dataset.props as string)[0],
      ).data.features as { id: string }[];
    expect(features().map((f) => f.id)).toEqual(["c"]);

    await user.click(screen.getByLabelText("Previous frame"));

    expect(screen.getByText("2026-09-04 06:05 UTC")).toBeTruthy();
    expect(features().map((f) => f.id)).toEqual(["b"]);
  });

  it("pins an imagery layer's tiles to the tick, verbatim", async () => {
    render(<MapPage />);
    await addLayer("imagery");

    const tiles = screen
      .getAllByTestId("source")
      .map((el) => JSON.parse(el.dataset.props as string))
      .flatMap((p) => (p.tiles as string[] | undefined) ?? []);
    expect(tiles.some((t) => t.includes("datetime=2026-09-04T06%3A10%3A00Z"))).toBe(true);
    expect(tiles.every((t) => t.includes("assets=visual"))).toBe(true);
  });

  it("returns to the newest tick when the span changes", async () => {
    render(<MapPage />);
    const user = await addLayer("footprints");
    await user.click(screen.getByLabelText("Previous frame"));
    expect(screen.getByText("2026-09-04 06:05 UTC")).toBeTruthy();

    // fireEvent, not userEvent: the house pattern for driving a Radix Select
    // in jsdom (`ingest-form-window.test.tsx`).
    fireEvent.click(screen.getByRole("combobox", { name: "Frame span" }));
    fireEvent.click(screen.getByRole("option", { name: "100 frames" }));

    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: 100, sortby: "-datetime" }),
    );
  });
});
