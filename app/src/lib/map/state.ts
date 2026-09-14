/**
 * The /map page's layer state (spec §4.2).
 *
 * A reducer in the island: nothing outside the page reads this, and a
 * nanostore would make it persistence by accident (spec §9). Pure and
 * exhaustively tested — the interesting behaviour is at the edges (a
 * duplicate add, a move at either end), and none of it is worth discovering
 * against a live map.
 *
 * Layer ids come from the CALLER so this module stays pure; the page hands
 * in a per-session counter.
 */
import {
  clamp01,
  footprintLayerIds,
  rasterFrameStackAnchorId,
  vectorTileLayerIds,
} from "@stac-higher/shared";

export type LayerKind = "footprints" | "imagery" | "vector";

export interface MapLayer {
  /** Client-generated, stable for the session; also the maplibre source id. */
  id: string;
  kind: LayerKind;
  /** footprints | imagery: a built-in-catalog collection id. vector: a tipg collection id. */
  sourceId: string;
  title: string;
  /** imagery only; a `previewAssetCandidates` key (V-3). */
  asset?: string;
  visible: boolean;
  /** 0..1, default 1. The reducer clamps every write (setOpacity); callers may pass anything. */
  opacity: number;
}

/** Newest items per STAC layer, page-wide (spec §4.2). */
export type FrameSpan = 25 | 50 | 100 | 200;
export const FRAME_SPANS: readonly FrameSpan[] = [25, 50, 100, 200];
export const DEFAULT_FRAME_SPAN: FrameSpan = 50;

export interface MapState {
  /** Draw order, BOTTOM first — the panel renders this reversed. */
  layers: MapLayer[];
  frameSpan: FrameSpan;
  /** null = the newest tick (the end of the axis), as the preview does. */
  axisIndex: number | null;
}

export const INITIAL_MAP_STATE: MapState = {
  layers: [],
  frameSpan: DEFAULT_FRAME_SPAN,
  axisIndex: null,
};

export type MapAction =
  | { type: "add"; layer: MapLayer }
  | { type: "remove"; id: string }
  | { type: "move"; id: string; direction: "up" | "down" }
  | { type: "setVisible"; id: string; visible: boolean }
  | { type: "setOpacity"; id: string; opacity: number }
  | { type: "setAsset"; id: string; asset: string }
  | { type: "setFrameSpan"; frameSpan: FrameSpan }
  | { type: "setAxisIndex"; axisIndex: number | null };

function patch(
  state: MapState,
  id: string,
  change: (layer: MapLayer) => MapLayer,
): MapState {
  if (!state.layers.some((l) => l.id === id)) return state;
  return {
    ...state,
    layers: state.layers.map((l) => (l.id === id ? change(l) : l)),
  };
}

export function mapReducer(state: MapState, action: MapAction): MapState {
  switch (action.type) {
    case "add": {
      // One layer per (kind, source): a second copy would collide on nothing
      // technically, but it is always a mis-click and it doubles the queries.
      const exists = state.layers.some(
        (l) => l.kind === action.layer.kind && l.sourceId === action.layer.sourceId,
      );
      if (exists) return state;
      return { ...state, layers: [...state.layers, action.layer] };
    }

    case "remove": {
      if (!state.layers.some((l) => l.id === action.id)) return state;
      return { ...state, layers: state.layers.filter((l) => l.id !== action.id) };
    }

    case "move": {
      const from = state.layers.findIndex((l) => l.id === action.id);
      if (from === -1) return state;
      // "up" in the panel means higher in the stack, which is LATER in the
      // draw array (bottom first).
      const to = action.direction === "up" ? from + 1 : from - 1;
      if (to < 0 || to >= state.layers.length) return state;
      const layers = [...state.layers];
      [layers[from], layers[to]] = [layers[to], layers[from]];
      return { ...state, layers };
    }

    case "setVisible":
      return patch(state, action.id, (l) => ({ ...l, visible: action.visible }));

    case "setOpacity":
      // The reducer is the actual boundary for the "clamped exactly once"
      // invariant (V-1): opacityFromSlider guards one path in, but any other
      // dispatcher (a URL restore, a saved preset, a keyboard step) must not
      // be able to write an out-of-range value the non-clamping layer
      // components would hand straight to maplibre.
      return patch(state, action.id, (l) => ({ ...l, opacity: clamp01(action.opacity) }));

    case "setAsset":
      return patch(state, action.id, (l) => ({ ...l, asset: action.asset }));

    case "setFrameSpan":
      // Every STAC layer's items query refetches, so the axis is rebuilt and
      // an index into the old one means nothing: back to the newest tick.
      return { ...state, frameSpan: action.frameSpan, axisIndex: null };

    case "setAxisIndex":
      // Lower-clamped here; the upper bound stays at render time in MapPage,
      // where the axis length is known.
      return {
        ...state,
        axisIndex: action.axisIndex === null ? null : Math.max(0, action.axisIndex),
      };
  }
}

/**
 * The id of the layer's own BOTTOM-most maplibre layer — the only id that is
 * stable for the life of the layer, and therefore the only legal `beforeId`
 * target. A raster stack's frame layers come and go on every step, which is
 * why imagery resolves to the stack's anchor (spec §11.2).
 */
export function layerAnchorId(layer: MapLayer): string {
  switch (layer.kind) {
    case "imagery":
      return rasterFrameStackAnchorId(layer.id);
    case "footprints":
      return footprintLayerIds(layer.id).fill;
    case "vector":
      return vectorTileLayerIds(layer.id).fill;
  }
}

/**
 * What the layer at `index` draws beneath: the anchor of the layer above it,
 * or nothing when it is the topmost layer.
 */
export function beforeIdFor(layers: MapLayer[], index: number): string | undefined {
  const above = layers[index + 1];
  return above ? layerAnchorId(above) : undefined;
}

/**
 * The single place a user-supplied opacity is clamped (V-1 final review):
 * the layer components take the caller's number unchanged and maplibre
 * rejects anything outside 0..1 as a style error.
 */
export function opacityFromSlider(values: number[]): number {
  const percent = values[0];
  if (percent === undefined) return 1;
  return clamp01(percent / 100);
}
