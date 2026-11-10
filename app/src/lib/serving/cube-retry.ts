/**
 * The 422 retry of a cube frame. The asset can be one server poll (<= 15 s)
 * ahead of cube-server, so a tile for a `t` it has not opened yet is a 422
 * (`no-store`). That is "not yet", never "failed": the frame is remounted
 * later. It is remounted, not re-URLed: xpublish-tiles reads an unknown query
 * parameter as a dimension selector, so a cache-buster is unsafe.
 */
export const CUBE_TILE_RETRY_MS = 5000;
export const CUBE_TILE_MAX_RETRIES = 3;

/** The attempts map with `t` bumped by one; null when `t` has used its retries. */
export function nextRetry(
  attempts: Readonly<Record<string, number>>,
  t: string,
): Record<string, number> | null {
  const used = attempts[t] ?? 0;
  if (used >= CUBE_TILE_MAX_RETRIES) return null;
  return { ...attempts, [t]: used + 1 };
}

/** The frame index of a RasterFrameStack source id `${stackId}-frame-${n}`, else null. */
export function frameIndexFromSourceId(stackId: string, sourceId: string | undefined): number | null {
  const prefix = `${stackId}-frame-`;
  if (!sourceId?.startsWith(prefix)) return null;
  const rest = sourceId.slice(prefix.length);
  return /^\d+$/.test(rest) ? Number(rest) : null;
}
