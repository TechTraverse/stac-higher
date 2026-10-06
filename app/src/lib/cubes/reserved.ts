/**
 * `_cube` is a reserved item id (ADR 0022): a cube repository lives at
 * assets/{collection}/_cube/, so an item of that id would own the
 * repository's prefix for GC and serving. It is reserved in EVERY collection,
 * not only cube collections: the repository outlives its sink row, and a
 * reservation keyed on the row would race the sink PUT. The id is not a valid
 * storage segment anyway (`SAFE_SEGMENT` forbids a leading `_`), so no real
 * item can carry platform-stored assets under it.
 */
export const CUBE_ITEM_ID = "_cube";

/** Whether `id` names the reserved item, raw or percent-encoded. */
export function isCubeItemId(id: string): boolean {
  if (id === CUBE_ITEM_ID) return true;
  try {
    return decodeURIComponent(id) === CUBE_ITEM_ID;
  } catch {
    return false; // malformed escape: the raw id is all there is
  }
}

/** Every item id a catalog write names: the path id plus the body's `id`, or
 * each feature's `id` for a FeatureCollection. */
export function writtenItemIds(pathItemId: string | null, doc: unknown): string[] {
  const ids: string[] = [];
  if (pathItemId) ids.push(pathItemId);
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
