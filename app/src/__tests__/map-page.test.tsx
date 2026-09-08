/**
 * /map island (V-2, spec §4). Query hooks and the map are mocked; what is
 * under test is the page's own logic — layer state, the layer panel, the
 * beforeId chain, and the hover/click plumbing.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useCollectionsMock, useItemsMock, mapProps } = vi.hoisted(() => ({
  useCollectionsMock: vi.fn(),
  useItemsMock: vi.fn(),
  mapProps: { current: null as Record<string, unknown> | null },
}));

// The shell is replaced by the bare QueryProvider it wraps: this test
// exercises page content, not the sidebar/top-bar chrome.
vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
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
vi.mock("@/lib/query/collections", () => ({
  useCollections: (...a: unknown[]) => useCollectionsMock(...a),
}));
vi.mock("@/lib/query/items", () => ({
  useItems: (...a: unknown[]) => useItemsMock(...a),
}));
// maplibre needs WebGL; inert stand-ins keep the tree — and the source/layer
// specs — intact. The DEFAULT export is the Map that StacMap renders, and it
// records its props so the test can drive the hover/click handlers.
vi.mock("react-map-gl/maplibre", () => ({
  default: (props: Record<string, unknown>) => {
    mapProps.current = props;
    return <div data-testid="map">{props.children as React.ReactNode}</div>;
  },
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

function collection(id: string, bbox?: number[]): StacCollection {
  return {
    type: "Collection",
    stac_version: "1.0.0",
    id,
    title: `Product ${id}`,
    description: "",
    license: "proprietary",
    extent: {
      spatial: { bbox: bbox ? [bbox] : [[-180, -90, 180, 90]] },
      temporal: { interval: [[null, null]] },
    },
    links: [],
  } as unknown as StacCollection;
}

function item(id: string, datetime: string | null): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "alpha",
    geometry: { type: "Point", coordinates: [0, 0] },
    properties: { datetime },
    links: [],
    assets: {},
  } as unknown as StacItem;
}

function sourceIds(): string[] {
  return screen
    .queryAllByTestId("source")
    .map((el) => JSON.parse(el.dataset.props as string).id as string);
}

function layerProps(): Record<string, unknown>[] {
  return screen
    .queryAllByTestId("layer")
    .map((el) => JSON.parse(el.dataset.props as string));
}

function rowIds(): string[] {
  return screen
    .queryAllByTestId("map-layer-row")
    .map((el) => el.dataset.layerId as string);
}

function addFootprints(collectionId: string) {
  fireEvent.click(screen.getByTestId("map-add-layer"));
  fireEvent.click(screen.getByTestId(`map-add-footprints-${collectionId}`));
}

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

beforeEach(() => {
  mapProps.current = null;
  useCollectionsMock.mockReset();
  useItemsMock.mockReset();
  useCollectionsMock.mockReturnValue({
    data: { collections: [collection("alpha"), collection("beta")] },
    isLoading: false,
  });
  useItemsMock.mockReturnValue({ data: { features: [] }, isLoading: false });
});

describe("MapPage", () => {
  it("opens on an empty map with an invitation to add the first layer", () => {
    render(<MapPage />);

    expect(screen.getByTestId("map")).toBeTruthy();
    expect(screen.getByText(/No layers yet/i)).toBeTruthy();
    expect(screen.queryAllByTestId("map-layer-row")).toHaveLength(0);
    // Nothing is drawn on the map, and nothing errored (spec §4.7).
    expect(screen.queryAllByTestId("source")).toHaveLength(0);
  });

  it("adds a footprints layer from the picker, as a row and a namespaced source", () => {
    useItemsMock.mockReturnValue({
      data: { features: [item("i1", "2026-09-07T00:00:00Z")] },
      isLoading: false,
    });
    render(<MapPage />);

    addFootprints("alpha");

    expect(rowIds()).toEqual(["layer-0"]);
    expect(screen.getByText("Product alpha")).toBeTruthy();
    // The source id IS the layer id, so several products can share the map.
    expect(sourceIds()).toEqual(["layer-0"]);
    expect(layerProps().map((l) => l.id)).toEqual(["layer-0-fill", "layer-0-line"]);
  });

  it("asks the catalog for the span's newest items, per layer", () => {
    render(<MapPage />);
    addFootprints("alpha");

    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "alpha",
      { limit: 50, sortby: "-datetime" },
    );
  });

  it("disables a product that is already on the map", () => {
    render(<MapPage />);
    addFootprints("alpha");

    fireEvent.click(screen.getByTestId("map-add-layer"));
    expect(screen.getByTestId("map-add-footprints-alpha")).toBeDisabled();
    expect(screen.getByTestId("map-add-footprints-beta")).not.toBeDisabled();
  });

  it("says so quietly when a product has no timestamped items", () => {
    // Never an error (spec §4.7): the layer is on the map, it just draws
    // nothing the time axis can use.
    useItemsMock.mockReturnValue({ data: { features: [item("i1", null)] }, isLoading: false });
    render(<MapPage />);

    addFootprints("alpha");

    const row = screen.getByTestId("map-layer-row");
    expect(within(row).getByText(/no items with a timestamp/i)).toBeTruthy();
  });

  it("chains each layer beneath the one above it, topmost rendered first", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    // Panel: topmost first. Draw order: bottom first.
    expect(rowIds()).toEqual(["layer-1", "layer-0"]);
    // Rendered topmost first so "layer-1-fill" exists by the time the layer
    // below it asks maplibre to insert beneath that id.
    expect(sourceIds()).toEqual(["layer-1", "layer-0"]);
    const beforeIds = layerProps().map((l) => l.beforeId);
    expect(beforeIds).toEqual([undefined, undefined, "layer-1-fill", "layer-1-fill"]);
  });

  it("hides a layer without dropping its source", () => {
    render(<MapPage />);
    addFootprints("alpha");

    fireEvent.click(screen.getByTestId("map-layer-visible"));

    expect(sourceIds()).toEqual(["layer-0"]);
    expect(layerProps().map((l) => l.layout)).toEqual([
      { visibility: "none" },
      { visibility: "none" },
    ]);
  });

  it("removes a layer from the list and the map", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    const first = screen.getAllByTestId("map-layer-row")[0];
    fireEvent.click(within(first).getByTestId("map-layer-remove"));

    expect(rowIds()).toEqual(["layer-0"]);
    expect(sourceIds()).toEqual(["layer-0"]);
    // The survivor is now topmost and chains to nothing.
    expect(layerProps().map((l) => l.beforeId)).toEqual([undefined, undefined]);
  });

  it("reorders layers and re-chains the beforeIds", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");
    expect(rowIds()).toEqual(["layer-1", "layer-0"]);

    // Move the bottom row (layer-0) up: it becomes topmost.
    const bottom = screen.getAllByTestId("map-layer-row")[1];
    fireEvent.click(within(bottom).getByTestId("map-layer-up"));

    expect(rowIds()).toEqual(["layer-0", "layer-1"]);
    expect(sourceIds()).toEqual(["layer-0", "layer-1"]);
    expect(layerProps().map((l) => l.beforeId)).toEqual([
      undefined,
      undefined,
      "layer-0-fill",
      "layer-0-fill",
    ]);
  });

  it("disables the move buttons at the ends of the list", () => {
    render(<MapPage />);
    addFootprints("alpha");
    addFootprints("beta");

    const [top, bottom] = screen.getAllByTestId("map-layer-row");
    expect(within(top).getByTestId("map-layer-up")).toBeDisabled();
    expect(within(top).getByTestId("map-layer-down")).not.toBeDisabled();
    expect(within(bottom).getByTestId("map-layer-down")).toBeDisabled();
  });

  it("feeds the opacity slider's value to the layer paint, clamped through opacityFromSlider", () => {
    // The clamp itself is unit-tested on opacityFromSlider (map-state.test.ts);
    // what matters here is that moving the row's control actually changes
    // what gets drawn. At the default opacity (1) the shared style omits
    // line-opacity and uses the plain hover expression for fill-opacity, so
    // driving the slider down one step is what proves the wiring — asserting
    // only the default would pass even with onValueChange stubbed out.
    render(<MapPage />);
    addFootprints("alpha");

    const thumb = within(screen.getByTestId("map-layer-opacity")).getByRole(
      "slider",
    );
    // Radix's Thumb takes keyboard focus and steps by the Slider's `step`
    // (1, i.e. 1%) per arrow press. From the default 100% this lands the
    // slider control's committed value at 99, i.e. opacity 0.99.
    fireEvent.keyDown(thumb, { key: "ArrowLeft" });

    const [fill, line] = layerProps();
    expect((fill.paint as Record<string, unknown>)["fill-opacity"]).toEqual([
      "*",
      0.99,
      ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0.1],
    ]);
    expect((line.paint as Record<string, unknown>)["line-opacity"]).toBe(0.99);
  });
});
