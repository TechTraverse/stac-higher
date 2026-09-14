import { useRef } from "react";
import { Layer } from "react-map-gl/maplibre";
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
 * (`${id}-frame-${n}-layer`) are mounted and unmounted on every step, and
 * maplibre no-ops an `addLayer` whose `beforeId` names a layer that is not
 * in the style — so a layer drawn below the stack names THIS id as its
 * `beforeId`, never a frame.
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
  // during render, in tree order, and maplibre drops a layer whose `beforeId`
  // names a layer that does not exist yet — every target must already be on
  // the map. The anchor, the stack's stable bottom, is rendered last for the
  // same reason.
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
      beforeId={layerIds[position + 1] ?? beforeId}
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
        beforeId={layerIds[0] ?? beforeId}
        paint={{ "background-opacity": 0 }}
      />
    </>
  );
}
