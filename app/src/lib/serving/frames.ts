/**
 * The time axis of the collection preview: one frame per timestep the
 * collection actually has items for.
 *
 * A frame carries the `datetime` value handed straight to the tile server's
 * collection mosaic (`/collections/{c}/tiles/...?datetime=...`), which filters
 * pgstac to that instant and composites whatever items fall in it. That value
 * is the catalog's own string, UNCHANGED: the filter matches exactly, so
 * `2026-09-04T06:11:17.300000Z` renders and the same instant written
 * `2026-09-04T06:11:17Z` returns an empty tile. Normalising here would empty
 * the map in a way nothing else would explain.
 */
import type { StacItem } from "@/lib/stac-api/types";

export interface PreviewFrame {
  /** The tile server's `datetime` filter — an instant or a start/end interval. */
  datetime: string;
  /** Human label for the slider readout, always UTC. */
  label: string;
  /** The items composited into this frame, in the order the catalog returned them. */
  itemIds: string[];
}

/** The instant a frame sorts by, or NaN when the item carries no usable time. */
function frameStart(item: StacItem): number {
  const raw = item.properties.datetime ?? item.properties.start_datetime;
  return raw ? Date.parse(raw) : NaN;
}

/** The tile server's `datetime` value for one item. */
function frameDatetime(item: StacItem): string | null {
  const instant = item.properties.datetime;
  if (instant) return instant;

  // A null `datetime` is STAC's ranged-item form: the pair is required, and
  // an interval is exactly what the tiler's filter accepts.
  const { start_datetime: start, end_datetime: end } = item.properties;
  return start && end ? `${start}/${end}` : null;
}

/**
 * `2026-09-04 06:11 UTC` — minutes are the finest cadence worth reading.
 *
 * Exported because the /map page's shared axis (`lib/map/axis.ts`) labels its
 * ticks with it: a tick and the frame it came from must read identically.
 */
export function formatFrameLabel(ms: number): string {
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/**
 * Frames for the given items, oldest first, with items that share a timestep
 * collapsed into one frame (the mosaic composites them for free).
 *
 * Items with no parseable time are dropped rather than guessed at — a frame
 * the tile server cannot filter to is a blank map with no explanation.
 */
export function buildPreviewFrames(items: StacItem[]): PreviewFrame[] {
  const byDatetime = new Map<string, { start: number; frame: PreviewFrame }>();

  for (const item of items) {
    const datetime = frameDatetime(item);
    const start = frameStart(item);
    if (!datetime || Number.isNaN(start)) continue;

    const existing = byDatetime.get(datetime);
    if (existing) {
      existing.frame.itemIds.push(item.id);
      continue;
    }
    byDatetime.set(datetime, {
      start,
      frame: { datetime, label: formatFrameLabel(start), itemIds: [item.id] },
    });
  }

  return [...byDatetime.values()].sort((a, b) => a.start - b.start).map((e) => e.frame);
}
