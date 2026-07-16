/**
 * Asset-redirect resolution (ROADMAP Phase 3, §3 "Asset access").
 *
 * The asset route calls this to turn (collection, item, filename) into the URL
 * it 302-redirects to. Today every asset lives in canonical storage, so this
 * always presigns the canonical object. It exists as a seam: Phase 4's
 * `storage_mode: reference` will branch here — look up the association's mode
 * and, for referenced assets, return the source href instead of a presigned
 * canonical URL. Callers depend only on `{ url }`, so that change stays local.
 */
import { canonicalAssetKey } from "./keys";
import { presignGetUrl } from "./presign";

export interface AssetTarget {
  /** Where the asset route should redirect the caller. */
  url: string;
  /** How it was resolved — canonical now; `reference` lands in Phase 4. */
  mode: "canonical";
}

export async function resolveAssetTarget(
  collection: string,
  itemId: string,
  filename: string,
): Promise<AssetTarget> {
  const key = canonicalAssetKey(collection, itemId, filename);
  const url = await presignGetUrl(key);
  return { url, mode: "canonical" };
}
