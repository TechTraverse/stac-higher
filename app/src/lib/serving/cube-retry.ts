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

/**
 * Whether a tile 422's body is the "not yet" kind: xpublish-tiles answers a
 * `t` its open snapshot lacks with `{"detail": "Invalid indexer: …"}` (a
 * string). Any other 422 (an unsupported style variant, a bad range or
 * variable: a pydantic `detail` list, or "Invalid colormap: …") is a request
 * error that no retry fixes. A body that is not JSON is treated as "not yet"
 * (the retry is capped anyway).
 */
export function isNotYetTileError(body: string): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return true;
  }
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.startsWith("Invalid indexer");
}

/** {@link isNotYetTileError} on maplibre's AJAXError body (a Blob); unreadable → "not yet". */
export async function isNotYetTileBody(body: Blob): Promise<boolean> {
  try {
    return isNotYetTileError(await body.text());
  } catch {
    return true;
  }
}

/** The frame index of a RasterFrameStack source id `${stackId}-frame-${n}`, else null. */
export function frameIndexFromSourceId(stackId: string, sourceId: string | undefined): number | null {
  const prefix = `${stackId}-frame-`;
  if (!sourceId?.startsWith(prefix)) return null;
  const rest = sourceId.slice(prefix.length);
  return /^\d+$/.test(rest) ? Number(rest) : null;
}
