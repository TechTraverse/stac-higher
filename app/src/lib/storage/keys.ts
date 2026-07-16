/**
 * Object-storage key layout (ROADMAP §5.3) + input hardening.
 *
 *   {bucket}/assets/{collection}/{item_id}/{filename}   canonical
 *   {bucket}/staging/{upload_id}/{filename}             push-ingest, TTL-cleaned
 *
 * Every segment that reaches a storage key is derived from untrusted input
 * (URL params, upload request bodies), so it is validated here BEFORE it can
 * be interpolated into a key — a `../` or an absolute path would otherwise let
 * a caller escape the item's prefix and read/overwrite a sibling's assets.
 */

export const CANONICAL_PREFIX = "assets";
export const STAGING_PREFIX = "staging";

export class StorageKeyError extends Error {}

// Path components that carry identity (collection id, item id, upload id). We
// keep them verbatim (they must match the catalog / the generated id) but
// reject anything that isn't a single safe path segment.
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** Validate an identity segment; throws `StorageKeyError` if unsafe. */
export function assertSafeSegment(value: string, label: string): string {
  if (!value || value === "." || value === "..") {
    throw new StorageKeyError(`${label} is empty or a relative path segment`);
  }
  if (!SAFE_SEGMENT.test(value)) {
    throw new StorageKeyError(
      `${label} contains characters not allowed in a storage key`,
    );
  }
  return value;
}

/**
 * Sanitize a user-supplied filename into a single safe path segment. Unlike
 * identity segments, a filename is *rewritten* (not just rejected): the display
 * name is cosmetic, so we take the basename and replace anything outside
 * `[A-Za-z0-9._-]` with `_`. Throws only if nothing usable remains.
 */
export function sanitizeFilename(name: string): string {
  // strip any directory portion a client may have sent (foo/bar.tif, C:\x.tif)
  const base = name.split(/[/\\]/).pop() ?? "";
  // stripping all leading dots means the result can never be "." or ".."
  const cleaned = base.replace(/[^A-Za-z0-9._-]/g, "_").replace(/^\.+/, "");
  if (!cleaned) {
    throw new StorageKeyError(`filename ${JSON.stringify(name)} is not usable`);
  }
  return cleaned;
}

export function canonicalAssetKey(
  collection: string,
  itemId: string,
  filename: string,
): string {
  assertSafeSegment(collection, "collection");
  assertSafeSegment(itemId, "item id");
  const file = sanitizeFilename(filename);
  return `${CANONICAL_PREFIX}/${collection}/${itemId}/${file}`;
}

export function stagingKey(uploadId: string, filename: string): string {
  assertSafeSegment(uploadId, "upload id");
  const file = sanitizeFilename(filename);
  return `${STAGING_PREFIX}/${uploadId}/${file}`;
}

/** The `/api/assets/...` href stored in an item's asset (what resolves later). */
export function assetHref(
  collection: string,
  itemId: string,
  filename: string,
): string {
  const file = sanitizeFilename(filename);
  return `/api/assets/${encodeURIComponent(collection)}/${encodeURIComponent(
    itemId,
  )}/${encodeURIComponent(file)}`;
}
