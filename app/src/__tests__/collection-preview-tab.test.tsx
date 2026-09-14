import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";

const { useItemsMock, useCollectionSettingsMock, useItemTileJsonMock } = vi.hoisted(
  () => ({
    useItemsMock: vi.fn(),
    useCollectionSettingsMock: vi.fn(),
    useItemTileJsonMock: vi.fn(),
  }),
);

vi.mock("@/lib/query/items", () => ({ useItems: (...a: unknown[]) => useItemsMock(...a) }));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => useCollectionSettingsMock(),
}));
vi.mock("@/lib/serving/queries", () => ({
  useItemTileJson: (...a: unknown[]) => useItemTileJsonMock(...a),
}));
// maplibre needs WebGL; inert stand-ins keep the tree — and the source/layer
// specs — intact. The DEFAULT export is the Map that StacMap renders.
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
  useMap: () => ({ current: undefined }),
}));

import { CollectionPreviewTab } from "@/components/collections/CollectionPreviewTab";

const COLLECTION = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "goes-geocolor",
  description: "",
  license: "proprietary",
  extent: {
    spatial: { bbox: [[-137, 14, -52, 54]] },
    temporal: { interval: [[null, null]] },
  },
  links: [],
} as unknown as StacCollection;

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
    },
  };
}

// Newest first — the order the catalog returns under `sortby=-datetime`.
const ITEMS = [
  item("c", "2026-09-04T06:11:17.300000Z"),
  item("b", "2026-09-04T06:06:17.300000Z"),
  item("a", "2026-09-04T06:01:17.300000Z"),
];

interface RenderedLayer {
  source: string;
  opacity: number;
}

/**
 * The stack's raster layers, BOTTOM first. Tree order is now top-first — the
 * frames chain `beforeId` on each other and react-map-gl creates layers during
 * render, so each target must exist before its dependant mounts — and the
 * always-mounted anchor is a background layer, not a frame.
 */
function frameProps(): Record<string, unknown>[] {
  return screen
    .queryAllByTestId("layer")
    .map((el) => JSON.parse(el.dataset.props as string) as Record<string, unknown>)
    .filter((p) => p.type === "raster")
    .reverse();
}

function layers(): RenderedLayer[] {
  return frameProps().map((props) => ({
    source: props.source as string,
    opacity: (props.paint as Record<string, number>)["raster-opacity"],
  }));
}

/**
 * The tile template of the frame on top. Frames are drawn in array order, so
 * the LAST opaque layer is the current one — the previous frame sits beneath
 * it at full opacity until the current one's tiles arrive.
 */
function visibleTiles(): string | undefined {
  const visible = layers().findLast((l) => l.opacity === 1);
  if (!visible) return undefined;
  const source = screen
    .queryAllByTestId("source")
    .map((el) => JSON.parse(el.dataset.props as string))
    .find((p) => p.id === visible.source);
  return source?.tiles?.[0];
}

function renderTab() {
  return render(
    <CollectionPreviewTab
      collection={COLLECTION}
      collectionId="goes-geocolor"
      endpointUrl="http://localhost:8081"
    />,
  );
}

beforeEach(() => {
  useItemsMock.mockReset();
  useCollectionSettingsMock.mockReset();
  useItemTileJsonMock.mockReset();
  useItemsMock.mockReturnValue({ data: { features: ITEMS }, isLoading: false });
  useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: true } });
  useItemTileJsonMock.mockReturnValue({ data: undefined, isFetched: true });
});

describe("CollectionPreviewTab", () => {
  it("opens on the newest frame", () => {
    renderTab();

    expect(screen.getByText("2026-09-04 06:11 UTC")).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("pins the visible frame's tiles to that timestep, verbatim", () => {
    renderTab();

    expect(visibleTiles()).toBe(
      "http://localhost:8084/collections/goes-geocolor/tiles/WebMercatorQuad" +
        "/{z}/{x}/{y}@1x?assets=visual&datetime=2026-09-04T06%3A11%3A17.300000Z",
    );
  });

  it("steps to an older frame", () => {
    renderTab();

    fireEvent.click(screen.getByLabelText("Previous frame"));

    expect(screen.getByText("2026-09-04 06:06 UTC")).toBeTruthy();
    expect(visibleTiles()).toContain("datetime=2026-09-04T06%3A06%3A17.300000Z");
  });

  it("warms the next frame at zero opacity while the current one plays", () => {
    // The lookahead is forward-only, the direction playback runs; stepping
    // back lands on a frame whose successor is the one just left.
    renderTab();
    fireEvent.click(screen.getByLabelText("Previous frame"));
    fireEvent.click(screen.getByLabelText("Previous frame"));
    fireEvent.click(screen.getByLabelText("Next frame"));

    const warming = layers().filter((l) => l.opacity === 0);
    expect(warming.map((l) => l.source)).toEqual(["preview-frame-2"]);
  });

  it("keeps the previous frame painted beneath the current one", () => {
    // Tiles render far slower than a step, so the outgoing frame stays up
    // until the incoming one covers it — otherwise every step flashes empty.
    renderTab();
    fireEvent.click(screen.getByLabelText("Previous frame"));

    const opaque = layers().filter((l) => l.opacity === 1);
    expect(opaque).toHaveLength(2);
    expect(opaque[0].source).toBe("preview-frame-2");
    expect(opaque[1].source).toBe("preview-frame-1");

    // "Beneath" is now stated by the chain rather than implied by child order
    // (I-112): the previous frame draws below the current frame's layer.
    expect(
      frameProps().map((p) => [p.id, p.beforeId]),
    ).toEqual([
      ["preview-frame-2-layer", "preview-frame-1-layer"],
      ["preview-frame-1-layer", undefined],
    ]);
  });

  it("asks the catalog for as many items as the frame count, newest first", () => {
    renderTab();

    expect(useItemsMock).toHaveBeenCalledWith(
      "http://localhost:8081",
      "goes-geocolor",
      expect.objectContaining({ limit: 50, sortby: "-datetime" }),
    );
  });

  it("takes the source's zoom limits from the newest item's TileJSON", () => {
    useItemTileJsonMock.mockReturnValue({
      data: { tiles: ["ignored"], minzoom: 2, maxzoom: 5, bounds: [-137, 14, -52, 54] },
      isFetched: true,
    });
    renderTab();

    const source = JSON.parse(
      screen.queryAllByTestId("source")[0].dataset.props as string,
    );
    expect(source).toMatchObject({ minzoom: 2, maxzoom: 5 });
  });

  it("holds the frames back until the zoom hint settles", () => {
    // A maplibre source's zoom range is fixed at creation, so mounting before
    // the hint lands would pin the wrong range (and warn on the update).
    useItemTileJsonMock.mockReturnValue({ data: undefined, isFetched: false });
    renderTab();

    expect(screen.queryAllByTestId("layer")).toHaveLength(0);
    expect(screen.getByTestId("map")).toBeTruthy();
    expect(screen.getByText("2026-09-04 06:11 UTC")).toBeTruthy();
  });

  it("says so, quietly, when nothing is previewable", () => {
    useItemsMock.mockReturnValue({
      data: {
        features: [
          {
            ...item("x", "2026-09-04T06:11:17.300000Z"),
            assets: { thumb: { href: "t.png", type: "image/png" } },
          },
        ],
      },
      isLoading: false,
    });
    renderTab();

    expect(screen.queryAllByTestId("layer")).toHaveLength(0);
    expect(screen.getByText(/no previewable/i)).toBeTruthy();
  });

  it("renders nothing when the product does not advertise serving", () => {
    useCollectionSettingsMock.mockReturnValue({ data: { servingEnabled: false } });
    const { container } = renderTab();

    expect(container.textContent).toBe("");
  });
});
