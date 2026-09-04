import { useRef } from "react";
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
  if (count === 0) return null;

  // Draw order, bottom to top: previous, current, then the lookahead frames
  // loading invisibly. Deduped — a series shorter than the window would
  // otherwise repeat itself.
  const mounted = [
    ...new Set([
      previousIndex.current % count,
      ...Array.from(
        { length: RASTER_FRAME_LOOKAHEAD + 1 },
        (_, offset) => (index + offset) % count,
      ),
    ]),
  ];

  return (
    <>
      {mounted.map((frameIndex) => (
        <RasterTileLayer
          key={frames[frameIndex].key}
          id={`${id}-frame-${frameIndex}`}
          tiles={frames[frameIndex].tiles}
          bounds={bounds}
          minzoom={minzoom}
          maxzoom={maxzoom}
          opacity={
            frameIndex === index || frameIndex === previousIndex.current % count
              ? opacity
              : 0
          }
          opacityTransitionMs={0}
          beforeId={beforeId}
        />
      ))}
    </>
  );
}
