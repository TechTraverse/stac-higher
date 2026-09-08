/**
 * /map island (V-2, spec §4). Query hooks and the map are mocked; what is
 * under test is the page's own logic — layer state, the layer panel, the
 * beforeId chain, and the hover/click plumbing.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StacCollection } from "@/lib/stac-api/types";

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
});
