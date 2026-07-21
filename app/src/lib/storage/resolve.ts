/**
 * Asset-redirect resolution (ROADMAP Phase 3 §3 "Asset access"; reference
 * branch is Phase 4 Slice C).
 *
 * The asset route calls this to turn (collection, item, filename) into the URL
 * it 302-redirects to. For `storage_mode: reference` assets, the pipeline
 * records the stable source URL in `ingest_files.source_href`
 * (`lookupReferenceHref`) — when found, that href is the redirect target and
 * nothing is presigned. Otherwise (copy mode / manual upload / no match), this
 * presigns the canonical object as before. Callers depend only on `{ url }`,
 * so this branch stays entirely local to this module.
 */
import { canonicalAssetKey } from "./keys";
import { presignGetUrl } from "./presign";
import { lookupReferenceHref } from "./reference";

export interface AssetTarget {
  /** Where the asset route should redirect the caller. */
  url: string;
  /** How it was resolved — the source href (reference) or a presigned canonical object URL. */
  mode: "canonical" | "reference";
}

export async function resolveAssetTarget(
  collection: string,
  itemId: string,
  filename: string,
): Promise<AssetTarget> {
  const referenceHref = await lookupReferenceHref(collection, itemId, filename);
  if (referenceHref) {
    return { url: referenceHref, mode: "reference" };
  }
  const key = canonicalAssetKey(collection, itemId, filename);
  const url = await presignGetUrl(key);
  return { url, mode: "canonical" };
}
