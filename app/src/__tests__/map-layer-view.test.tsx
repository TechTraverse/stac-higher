import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MapLayer } from "@/lib/map/state";
import type { StacItem } from "@/lib/stac-api/types";
import { buildPreviewFrames } from "@/lib/serving/frames";

const { useLayerDataMock } = vi.hoisted(() => ({ useLayerDataMock: vi.fn() }));
vi.mock("@/components/map/useLayerData", () => ({
  useLayerData: (...a: unknown[]) => useLayerDataMock(...a),
}));

// The shared map components are stand-ins here: what is under test is which
// items and which frame index this view hands them, not how they draw.
vi.mock("@stac-higher/shared", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@stac-higher/shared");
  return {
    ...actual,
    FootprintLayer: (props: Record<string, unknown>) => (
      <div
        data-testid="footprints"
        data-ids={JSON.stringify((props.items as StacItem[]).map((i) => i.id))}
        data-before={String(props.beforeId)}
      />
    ),
    RasterFrameStack: (props: Record<string, unknown>) => (
      <div
        data-testid="stack"
        data-props={JSON.stringify({
          id: props.id,
          index: props.index,
          beforeId: props.beforeId,
          keys: (props.frames as { key: string }[]).map((f) => f.key),
        })}
      />
    ),
  };
});

import { MapLayerView } from "@/components/map/MapLayerView";

function item(id: string, datetime: string): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection: "goes-geocolor",
    geometry: null,
    properties: { datetime },
    links: [],
    assets: { visual: { href: `${id}.tif`, roles: ["visual"] } },
  };
}

const ITEMS = [
  item("c", "2026-09-04T06:10:00Z"),
  item("b", "2026-09-04T06:05:00Z"),
  item("a", "2026-09-04T06:00:00Z"),
];
const FRAMES = buildPreviewFrames(ITEMS);
const T = (iso: string) => Date.parse(iso);

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

function renderView(props: Partial<Record<string, unknown>> = {}, l: MapLayer = layer()) {
  const onFramesChange = vi.fn();
  const onFramesRemove = vi.fn();
  const result = render(
    <MapLayerView
      layer={l}
      catalogUrl="http://localhost:8081"
      frameSpan={50}
      tickInstant={T("2026-09-04T06:10:00Z")}
      beforeId="above-fill"
      onFramesChange={onFramesChange}
      onFramesRemove={onFramesRemove}
      {...props}
    />,
  );
  return { ...result, onFramesChange, onFramesRemove };
}

function stackProps() {
  return JSON.parse(screen.getByTestId("stack").dataset.props as string);
}

beforeEach(() => {
  vi.clearAllMocks();
  useLayerDataMock.mockReturnValue({
    items: ITEMS,
    frames: FRAMES,
    candidates: ["visual"],
    asset: "visual",
    hint: { tiles: ["ignored"], bounds: [-137, 14, -52, 54], minzoom: 2, maxzoom: 5 },
    hintSettled: true,
    servingEnabled: true,
  });
});

describe("MapLayerView — imagery", () => {
  it("mounts one tile template per frame, datetime pinned verbatim", () => {
    renderView();

    expect(stackProps().keys).toEqual(FRAMES.map((f) => f.datetime));
    expect(stackProps().id).toBe("l1");
    expect(stackProps().beforeId).toBe("above-fill");
  });

  it("shows the frame the axis tick resolves to", () => {
    renderView({ tickInstant: T("2026-09-04T06:07:00Z") });

    // Hold-last: 06:07 is between this layer's 06:05 and 06:10 frames.
    expect(stackProps().index).toBe(1);
  });

  it("draws no frames — but keeps its anchor — before its first frame", () => {
    renderView({ tickInstant: T("2026-09-04T05:00:00Z") });

    expect(stackProps().keys).toEqual([]);
    expect(stackProps().index).toBe(0);
  });

  it("holds the frames back until the zoom hint settles", () => {
    useLayerDataMock.mockReturnValue({
      items: ITEMS,
      frames: FRAMES,
      candidates: ["visual"],
      asset: "visual",
      hint: undefined,
      hintSettled: false,
      servingEnabled: true,
    });
    renderView();

    // The stack itself stays mounted so the page's beforeId chain keeps its
    // target; only the frames wait (a source's zoom range is fixed at
    // creation, so mounting before the hint pins the wrong range).
    expect(stackProps().keys).toEqual([]);
  });

  it("shows the newest frame when there is no axis yet", () => {
    renderView({ tickInstant: null });

    expect(stackProps().index).toBe(FRAMES.length - 1);
  });

  it("draws nothing when the product publishes no tileable asset", () => {
    useLayerDataMock.mockReturnValue({
      items: ITEMS,
      frames: FRAMES,
      candidates: [],
      asset: undefined,
      hint: undefined,
      hintSettled: true,
      servingEnabled: true,
    });
    renderView();

    expect(stackProps().keys).toEqual([]);
  });
});

describe("MapLayerView — footprints", () => {
  it("draws the items of the frame the tick resolves to, not the whole span", () => {
    renderView({ tickInstant: T("2026-09-04T06:05:00Z") }, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.ids).toBe(JSON.stringify(["b"]));
  });

  it("draws nothing before its first frame", () => {
    renderView({ tickInstant: T("2026-09-04T05:00:00Z") }, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.ids).toBe(JSON.stringify([]));
  });

  it("passes the page's beforeId through", () => {
    renderView({}, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.before).toBe("above-fill");
  });

  it("still draws when the (unrequested) zoom hint hasn't settled", () => {
    // Footprints never ask the tiler (I-… useLayerData never issues the hint
    // query for them), so hintSettled stays false forever — drawing must not
    // wait on it.
    useLayerDataMock.mockReturnValue({
      items: ITEMS,
      frames: FRAMES,
      candidates: [],
      asset: undefined,
      hint: undefined,
      hintSettled: false,
      servingEnabled: true,
    });
    renderView({ tickInstant: T("2026-09-04T06:05:00Z") }, layer({ kind: "footprints" }));

    expect(screen.getByTestId("footprints").dataset.ids).toBe(JSON.stringify(["b"]));
  });
});

describe("MapLayerView — the axis", () => {
  it("reports its frames up and withdraws them when it unmounts", () => {
    const { onFramesChange, onFramesRemove, unmount } = renderView();

    expect(onFramesChange).toHaveBeenCalledWith("l1", FRAMES);
    unmount();
    expect(onFramesRemove).toHaveBeenCalledWith("l1");
  });
});
