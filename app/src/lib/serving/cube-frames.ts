/**
 * The time axis of a cube's Preview: one frame per entry of the cube asset's
 * `stac_higher:time_values` (spec §3.3), the exact-nanosecond strings the
 * Z-5 writer publishes.
 *
 * The `t` a frame carries is the entry VERBATIM: cube-server matches it
 * exactly, so re-formatting it would blank the map. Only labels and sorting
 * read a parsed copy, and that copy is trimmed to milliseconds first because
 * `Date.parse` on a 9-digit fraction is engine-dependent.
 */
import type { StacCollection } from "@/lib/stac-api/types";
import { formatFrameLabel } from "./frames";

export const CUBE_MEDIA_TYPE = "application/vnd.zarr+icechunk";
/**
 * What the Preview renders when the collection names no data variable. The
 * pipeline will write `cube:variables` (follow-up #170); until then this is
 * the usual path.
 */
export const CUBE_DEFAULT_VARIABLE = "CMI";
/** Frames shown until the viewer asks for the full window. */
export const CUBE_DEFAULT_FRAME_COUNT = 24;

export interface CubeAssetRef {
  /** The asset key on the collection (`cube` by default). */
  key: string;
  /** `stac_higher:time_values`, string entries only; `[]` when absent. */
  timeValues: string[];
}

/** The collection's first Icechunk cube asset, or null for an ordinary collection. */
export function findCubeAsset(collection: StacCollection): CubeAssetRef | null {
  for (const [key, asset] of Object.entries(collection.assets ?? {})) {
    if (asset?.type !== CUBE_MEDIA_TYPE) continue;
    const raw = (asset as unknown as Record<string, unknown>)["stac_higher:time_values"];
    const timeValues = Array.isArray(raw)
      ? raw.filter((v): v is string => typeof v === "string")
      : [];
    return { key, timeValues };
  }
  return null;
}

/**
 * The variable to render: the first `cube:variables` entry of type `data`,
 * else {@link CUBE_DEFAULT_VARIABLE}. (The Z-5 writer emits only
 * `cube:dimensions` today, so the fallback is the usual path.)
 */
export function cubeVariable(collection: StacCollection): string {
  const vars = (collection as unknown as Record<string, unknown>)["cube:variables"];
  if (vars && typeof vars === "object") {
    for (const [name, def] of Object.entries(vars as Record<string, unknown>)) {
      if ((def as { type?: unknown } | null)?.type === "data") return name;
    }
  }
  return CUBE_DEFAULT_VARIABLE;
}

/** Epoch ms of a time value, fraction trimmed to 3 digits first; NaN when unparseable. */
export function cubeTimeMs(t: string): number {
  return Date.parse(t.replace(/(\.\d{3})\d+/, "$1"));
}

export interface CubeFrame {
  /** The `stac_higher:time_values` entry, verbatim. */
  t: string;
  label: string;
}

/**
 * Frames oldest first. Unparseable values are dropped (a frame the server
 * cannot be asked for is a blank map), duplicates collapse, and `last`
 * keeps only the newest N.
 */
export function buildCubeFrames(timeValues: readonly string[], last?: number): CubeFrame[] {
  const seen = new Map<string, { ms: number; frame: CubeFrame }>();
  for (const t of timeValues) {
    const ms = cubeTimeMs(t);
    if (Number.isNaN(ms) || seen.has(t)) continue;
    seen.set(t, { ms, frame: { t, label: formatFrameLabel(ms) } });
  }
  const frames = [...seen.values()].sort((a, b) => a.ms - b.ms).map((e) => e.frame);
  return last === undefined ? frames : frames.slice(-last);
}
