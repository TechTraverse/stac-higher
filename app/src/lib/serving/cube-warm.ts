/**
 * Background warm-up of a cube Preview's frames. Cube tiles are slow virtual
 * reads, so playback that waits for each frame's tiles crawls on its first
 * pass. After the current frame shows, the other frames of the window are
 * loaded through hidden maplibre sources, a couple at a time; the tiles land
 * in the browser HTTP cache (cube-server marks t-pinned tiles immutable), so
 * the frame stack's own sources later read them from there.
 */

/** Hidden warm sources mounted at once (the visible frame must not be starved). */
export const CUBE_WARM_CONCURRENCY = 2;
/** A warm source still not loaded after this long is dropped, not marked loaded. */
export const CUBE_WARM_TIMEOUT_MS = 30_000;

/** The maplibre source id of the warm source for frame `n`. */
export function warmSourceId(n: number): string {
  return `cube-warm-${n}`;
}

/** The frame index of a warm source id, else null. */
export function frameIndexFromWarmId(sourceId: string | undefined): number | null {
  if (!sourceId?.startsWith("cube-warm-")) return null;
  const rest = sourceId.slice("cube-warm-".length);
  return /^\d+$/.test(rest) ? Number(rest) : null;
}

/**
 * The `t` values to start warming now. Candidates run in PLAYBACK order from
 * the frame after `current` (wrapping), so the frames playback reaches first
 * are ready first; frames already loaded, mounted by the stack or being
 * warmed (`active`) are skipped. Returns only as many as free slots remain
 * under `concurrency`.
 */
export function nextWarmFrames(
  frames: readonly string[],
  loaded: ReadonlySet<string>,
  mounted: ReadonlySet<string>,
  active: readonly string[],
  concurrency: number,
  current: number,
): string[] {
  const slots = concurrency - active.length;
  const n = frames.length;
  if (slots <= 0 || n === 0) return [];
  const out: string[] = [];
  for (let step = 1; step < n && out.length < slots; step++) {
    const t = frames[(((current + step) % n) + n) % n];
    if (loaded.has(t) || mounted.has(t) || active.includes(t)) continue;
    out.push(t);
  }
  return out;
}
