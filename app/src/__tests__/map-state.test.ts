import { describe, it, expect } from "vitest";
import {
  DEFAULT_FRAME_SPAN,
  INITIAL_MAP_STATE,
  beforeIdFor,
  layerAnchorId,
  mapReducer,
  opacityFromSlider,
  type MapLayer,
  type MapState,
} from "@/lib/map/state";

function layer(id: string, over: Partial<MapLayer> = {}): MapLayer {
  return {
    id,
    kind: "footprints",
    sourceId: `collection-${id}`,
    title: `Collection ${id}`,
    visible: true,
    opacity: 1,
    ...over,
  };
}

function stateWith(...layers: MapLayer[]): MapState {
  return { ...INITIAL_MAP_STATE, layers };
}

describe("mapReducer", () => {
  it("starts empty, at the default span, on the newest tick", () => {
    expect(INITIAL_MAP_STATE).toEqual({
      layers: [],
      frameSpan: DEFAULT_FRAME_SPAN,
      axisIndex: null,
    });
    expect(DEFAULT_FRAME_SPAN).toBe(50);
  });

  it("adds a layer on top of the draw order", () => {
    const one = mapReducer(INITIAL_MAP_STATE, { type: "add", layer: layer("a") });
    const two = mapReducer(one, { type: "add", layer: layer("b") });

    // Bottom first: the newest layer draws above the ones already there.
    expect(two.layers.map((l) => l.id)).toEqual(["a", "b"]);
  });

  it("refuses a duplicate kind + source, and allows the same source in another kind", () => {
    const one = mapReducer(INITIAL_MAP_STATE, { type: "add", layer: layer("a") });
    const dup = mapReducer(one, {
      type: "add",
      layer: layer("b", { sourceId: "collection-a" }),
    });
    expect(dup).toBe(one);

    const imagery = mapReducer(one, {
      type: "add",
      layer: layer("c", { kind: "imagery", sourceId: "collection-a" }),
    });
    expect(imagery.layers.map((l) => l.id)).toEqual(["a", "c"]);
  });

  it("removes by id and ignores an unknown id", () => {
    const state = stateWith(layer("a"), layer("b"));

    expect(mapReducer(state, { type: "remove", id: "a" }).layers.map((l) => l.id)).toEqual(["b"]);
    expect(mapReducer(state, { type: "remove", id: "zz" })).toBe(state);
  });

  it("moves a layer one step and stops at the ends", () => {
    const state = stateWith(layer("a"), layer("b"), layer("c"));

    // "up" in the panel = higher in the stack = later in the draw array.
    expect(
      mapReducer(state, { type: "move", id: "a", direction: "up" }).layers.map((l) => l.id),
    ).toEqual(["b", "a", "c"]);
    expect(
      mapReducer(state, { type: "move", id: "c", direction: "down" }).layers.map((l) => l.id),
    ).toEqual(["a", "c", "b"]);

    expect(mapReducer(state, { type: "move", id: "c", direction: "up" })).toBe(state);
    expect(mapReducer(state, { type: "move", id: "a", direction: "down" })).toBe(state);
    expect(mapReducer(state, { type: "move", id: "zz", direction: "up" })).toBe(state);
  });

  it("sets visibility, opacity and the imagery asset on one layer only", () => {
    const state = stateWith(layer("a"), layer("b", { kind: "imagery" }));

    const hidden = mapReducer(state, { type: "setVisible", id: "a", visible: false });
    expect(hidden.layers.map((l) => l.visible)).toEqual([false, true]);

    const dimmed = mapReducer(state, { type: "setOpacity", id: "b", opacity: 0.25 });
    expect(dimmed.layers.map((l) => l.opacity)).toEqual([1, 0.25]);

    const assigned = mapReducer(state, { type: "setAsset", id: "b", asset: "visual" });
    expect(assigned.layers.map((l) => l.asset)).toEqual([undefined, "visual"]);
  });

  it("returns to the newest tick when the span changes", () => {
    // Every layer's items query refetches on a span change, so an index into
    // the old axis means nothing (spec §4.4).
    const state = { ...stateWith(layer("a")), axisIndex: 7 };

    const next = mapReducer(state, { type: "setFrameSpan", frameSpan: 200 });
    expect(next.frameSpan).toBe(200);
    expect(next.axisIndex).toBeNull();
  });

  it("sets the axis index, including back to newest", () => {
    const state = stateWith(layer("a"));

    expect(mapReducer(state, { type: "setAxisIndex", axisIndex: 3 }).axisIndex).toBe(3);
    expect(mapReducer(state, { type: "setAxisIndex", axisIndex: null }).axisIndex).toBeNull();
  });
});

describe("layerAnchorId / beforeIdFor", () => {
  it("names the STABLE bottom layer of each kind", () => {
    // Never a raster frame id: those are mounted and unmounted every step and
    // maplibre no-ops an addLayer whose beforeId is missing.
    expect(layerAnchorId(layer("a"))).toBe("a-fill");
    expect(layerAnchorId(layer("a", { kind: "imagery" }))).toBe("a-anchor");
    expect(layerAnchorId(layer("a", { kind: "vector" }))).toBe("a-fill");
  });

  it("chains each layer beneath the anchor of the layer above it", () => {
    const layers = [layer("a"), layer("b", { kind: "imagery" }), layer("c")];

    expect(beforeIdFor(layers, 0)).toBe("b-anchor");
    expect(beforeIdFor(layers, 1)).toBe("c-fill");
    // The topmost layer chains to nothing and is simply appended.
    expect(beforeIdFor(layers, 2)).toBeUndefined();
  });
});

describe("opacityFromSlider", () => {
  it("converts percent to a clamped 0..1 opacity", () => {
    expect(opacityFromSlider([50])).toBe(0.5);
    expect(opacityFromSlider([0])).toBe(0);
    expect(opacityFromSlider([100])).toBe(1);
  });

  it("never lets an out-of-range value reach a layer", () => {
    expect(opacityFromSlider([150])).toBe(1);
    expect(opacityFromSlider([-20])).toBe(0);
    expect(opacityFromSlider([Number.NaN])).toBe(0);
    expect(opacityFromSlider([])).toBe(1);
  });
});
