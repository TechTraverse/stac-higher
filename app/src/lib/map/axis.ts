/**
 * The /map page's shared time axis (spec §4.4).
 *
 * Every time-aware layer keeps its own frames — products have their own
 * cadences and an axis taken from one "primary" layer breaks the moment a
 * second product is added. The page's axis is the UNION of their frame
 * instants; each layer then resolves its own frame for the chosen tick by
 * holding its last frame at or before it.
 *
 * Pure: no React, no fetching, no notion of layers beyond their frames.
 */
import { formatFrameLabel, type PreviewFrame } from "@/lib/serving/frames";

export interface AxisTick {
  /** Epoch ms — the identity of the tick. */
  instant: number;
  /** What the time bar reads out; formatted exactly as a frame's label is. */
  label: string;
}

/**
 * The instant a frame starts. A frame's `datetime` is the tiler's filter
 * string, which is either an instant or a `start/end` interval (STAC's ranged
 * form); the start is what it sorts and labels by, in both cases.
 */
export function frameInstant(frame: PreviewFrame): number {
  const slash = frame.datetime.indexOf("/");
  return Date.parse(slash === -1 ? frame.datetime : frame.datetime.slice(0, slash));
}

/**
 * One tick per distinct frame instant across all time-aware layers, oldest
 * first. Two layers whose items share an instant produce one tick.
 */
export function buildAxis(layerFrames: Map<string, PreviewFrame[]>): AxisTick[] {
  const byInstant = new Map<number, AxisTick>();

  for (const frames of layerFrames.values()) {
    for (const frame of frames) {
      const instant = frameInstant(frame);
      // buildPreviewFrames already drops unparseable times; belt and braces so
      // a NaN can never sort into the axis and poison every comparison.
      if (Number.isNaN(instant)) continue;
      if (!byInstant.has(instant)) {
        byInstant.set(instant, { instant, label: formatFrameLabel(instant) });
      }
    }
  }

  return [...byInstant.values()].sort((a, b) => a.instant - b.instant);
}

/**
 * The index of the layer's newest frame at or before `tickInstant` — "hold
 * last", so a slow product stays on screen through a fast one's ticks — or
 * `null` when the layer has nothing that early and must draw nothing.
 *
 * Binary search: frames are already sorted oldest-first by
 * `buildPreviewFrames`, and this runs once per layer per tick during playback.
 * The result is always a valid index into `frames` or `null`; a stack handed a
 * negative index throws and one handed an index past the end paints every
 * frame at zero opacity, which reads as a dead tile server.
 */
export function resolveLayerFrame(
  tickInstant: number,
  frames: PreviewFrame[],
): number | null {
  let low = 0;
  let high = frames.length - 1;
  let found: number | null = null;

  while (low <= high) {
    const mid = (low + high) >> 1;
    if (frameInstant(frames[mid]) <= tickInstant) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  return found;
}
