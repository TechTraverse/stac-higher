import { useEffect, useRef, useState } from "react";
import { Layer, useMap } from "react-map-gl/maplibre";
import { RasterTileLayer } from "./RasterTileLayer";

export interface RasterFrame {
  /** Stable identity for React keys — a collection preview uses the frame's datetime. */
  key: string;
  /** XYZ tile templates for this frame. */
  tiles: string[];
}

export interface RasterFrameStackProps {
  /** Namespace: frame n mounts as maplibre source `${id}-frame-${n}`. */
  id: string;
  /** The series, oldest first. */
  frames: RasterFrame[];
  /** The frame to show. The caller clamps it to `frames`. */
  index: number;
  /** [w, s, e, n] so maplibre asks only for covered tiles. */
  bounds?: [number, number, number, number];
  minzoom?: number;
  maxzoom?: number;
  /** The layer's own opacity (0..1), applied to the visible frames. */
  opacity?: number;
  /** Hides every mounted frame (the anchor is unaffected — it paints nothing). */
  visible?: boolean;
  /** Draw beneath this layer id. */
  beforeId?: string;
}

/**
 * Frames kept mounted ahead of the current one, warming while it plays. ONE:
 * every mounted frame competes for the same handful of connections to the
 * tile server, so a deeper window starves the frame the viewer is actually
 * looking at.
 */
export const RASTER_FRAME_LOOKAHEAD = 1;

/**
 * The stack's stable `beforeId` target (spec §11.2). Frame layer ids
 * (`${id}-frame-${n}-layer`) are mounted and unmounted on every step, and an
 * `addLayer` whose `beforeId` names a layer that is not in the style yet
 * fires an error event and skips the add — so a layer drawn below the stack
 * names THIS id as its `beforeId`, never a frame. On a fresh mount the
 * anchor's OWN target, the bottom-most frame, is not on the map yet either;
 * it falls back to the caller's own target until a later `styledata` event
 * (or step) lets it chain down to the frame instead.
 */
export function rasterFrameStackAnchorId(id: string): string {
  return `${id}-anchor`;
}

/**
 * A time series of raster tile layers shown one frame at a time.
 *
 * Three frames are mounted: the frame shown BEFORE this one, kept painted
 * underneath because tiles take far longer to render than a playback tick
 * and a step to a cold frame would otherwise flash an empty map; the
 * current frame; and one lookahead frame at zero opacity, loading its tiles
 * invisibly. Frames are swapped by opacity with no transition — a fade
 * would smear one timestep into the next.
 *
 * Pure presentation: no fetching, no notion of time. Deciding which frame
 * is current (and when playback may advance) belongs to the caller.
 */
export function RasterFrameStack({
  id,
  frames,
  index,
  bounds,
  minzoom,
  maxzoom,
  opacity = 1,
  visible = true,
  beforeId,
}: RasterFrameStackProps) {
  // The frame shown before this one. Tracked in refs rather than state so a
  // step re-renders once, with "previous" already pointing at the frame that
  // was on screen.
  const previousIndex = useRef(index);
  const lastIndex = useRef(index);
  if (lastIndex.current !== index) {
    previousIndex.current = lastIndex.current;
    lastIndex.current = index;
  }

  // A chain target is only a target if maplibre already has it: a frame whose
  // Source is not registered yet has no layer, and an addLayer naming it as
  // `beforeId` fires an error event and skips the add. Fall back to the
  // caller's target — a fallen-back layer sits just below the caller's own
  // target rather than where it belongs in the chain — until the chain is
  // re-evaluated, which happens on every step AND on every `styledata` event
  // (below): the moment a frame's own `addLayer` retry lands, re-running
  // `existing()` picks it up as a target. When `useMap` gives no map (unit
  // tests with a mocked module that omits it, or Storybook without a map),
  // treat "no map" as "assume it exists" so the pure chain behaviour stays
  // testable.
  const { current: mapRef } = useMap();
  const map = mapRef?.getMap?.();
  const existing = (layerId: string | undefined): string | undefined => {
    if (layerId === undefined) return undefined;
    return map && map.style ? (map.getLayer(layerId) ? layerId : undefined) : layerId;
  };

  // Nothing else re-renders the stack once the frames' Sources finish
  // registering, so without this a fresh mount's fallback would never
  // resolve: every target is missing on the FIRST render, everything falls
  // back to the caller's target, and the anchor can sit above the frames
  // until some unrelated prop change happens to re-render. react-map-gl's own
  // `<Layer>` retries a skipped `addLayer` on this same event, so it is the
  // moment a fallen-back target is guaranteed to exist.
  const [, bump] = useState(0);
  useEffect(() => {
    if (!map) return;
    const rerender = () => bump((n) => n + 1);
    map.on("styledata", rerender);
    return () => {
      map.off("styledata", rerender);
    };
  }, [map]);

  const count = frames.length;

  // A non-finite index shows the first frame rather than nothing.
  const safeIndex = Number.isFinite(index) ? index : 0;

  // Normalised into the series so an index from a caller's own arithmetic
  // (a shared time axis, a series that shrank under a stale ref) can never
  // reach past the array: previous and current are both taken modulo the
  // series, which also keeps the previous frame painted when the series
  // shrinks below the index it remembers.
  const current = count === 0 ? 0 : ((safeIndex % count) + count) % count;
  const previous = count === 0 ? 0 : ((previousIndex.current % count) + count) % count;

  // Draw order, bottom to top: previous, current, then the lookahead frames
  // loading invisibly. Deduped — a series shorter than the window would
  // otherwise repeat itself.
  const mounted =
    count === 0
      ? []
      : [
          ...new Set([
            previous,
            ...Array.from(
              { length: RASTER_FRAME_LOOKAHEAD + 1 },
              (_, offset) => (current + offset) % count,
            ),
          ]),
        ];

  // The layer ids RasterTileLayer will mint for that window, bottom first —
  // the links of the chain.
  const layerIds = mounted.map((frameIndex) => `${id}-frame-${frameIndex}-layer`);

  // Draw order is STATED, not inherited from React child order (I-112).
  // maplibre fixes a layer's position at addLayer time and react-map-gl calls
  // moveLayer only when `beforeId` changes, so a step that reorders the window
  // — every backward step — has to change some layer's `beforeId` or the frame
  // left over from the way up keeps painting over the current one.
  //
  // Each frame draws beneath the frame above it; the top frame beneath the
  // caller's target. Rendered TOP FIRST because react-map-gl creates layers
  // during render, in tree order, and an `addLayer` whose `beforeId` names a
  // layer that does not exist yet fires an error event and skips the add —
  // every target must already be on the map. The anchor, the stack's stable
  // bottom, is rendered last for the same reason. Render order alone is not
  // enough on a fresh mount or when a new frame joins the window, though:
  // react-map-gl's `<Layer>` calls `addLayer` during render, but a frame's
  // OWN layer is added only once its `<Source>` is registered, so a frame
  // chaining to the one above (still mid-mount) would still name a target
  // that is not in the style yet. `existing()` chains only to a layer
  // maplibre already has and falls back to the caller's target otherwise —
  // re-evaluated on every step and on every `styledata` event above, which is
  // when a fallen-back target is guaranteed to have appeared.
  const frameLayers = mounted.map((frameIndex, position) => (
    <RasterTileLayer
      key={frames[frameIndex].key}
      id={`${id}-frame-${frameIndex}`}
      tiles={frames[frameIndex].tiles}
      bounds={bounds}
      minzoom={minzoom}
      maxzoom={maxzoom}
      visible={visible}
      opacity={frameIndex === current || frameIndex === previous ? opacity : 0}
      opacityTransitionMs={0}
      beforeId={existing(layerIds[position + 1]) ?? beforeId}
    />
  ));
  frameLayers.reverse();

  // Always mounted, even for an empty series: a chaining target that appears
  // only once data arrives is not a chaining target. A `background` layer
  // needs no source and fetches nothing. It carries no `layout`: it paints
  // nothing in either state, and hiding it would only risk maplibre dropping
  // the target the layer below chains to.
  //
  // Rendered LAST because its own target is the bottom-most frame, which must
  // exist by the time it mounts. With no frames it falls back to the caller's
  // target, exactly as V-2 had it.
  return (
    <>
      {frameLayers}
      <Layer
        id={rasterFrameStackAnchorId(id)}
        type="background"
        beforeId={existing(layerIds[0]) ?? beforeId}
        paint={{ "background-opacity": 0 }}
      />
    </>
  );
}
