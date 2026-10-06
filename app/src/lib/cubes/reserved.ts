/**
 * `_cube` is a reserved item id in a cube collection (ADR 0022): the
 * repository lives at assets/{cube}/_cube/, so an item of that id would own
 * the repository's prefix for GC and serving.
 */
export const CUBE_ITEM_ID = "_cube";

/** Every item id a catalog write names: the path id plus the body's `id`, or
 * each feature's `id` for a FeatureCollection. */
export function writtenItemIds(pathItemId: string | null, doc: unknown): string[] {
  const ids: string[] = [];
  if (pathItemId) {
    ids.push(pathItemId);
    try {
      ids.push(decodeURIComponent(pathItemId));
    } catch {
      // malformed escape: the raw id is all there is
    }
  }
  const idOf = (value: unknown) => {
    const id = (value as { id?: unknown } | null)?.id;
    if (typeof id === "string") ids.push(id);
  };
  if (doc && typeof doc === "object") {
    const features = (doc as { features?: unknown }).features;
    if (Array.isArray(features)) features.forEach(idOf);
    else idOf(doc);
  }
  return ids;
}
