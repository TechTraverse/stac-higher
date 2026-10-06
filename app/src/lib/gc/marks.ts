/**
 * asset_gc marking — the app's half of retention & GC (M2-F, ADR 0011).
 *
 * The pipeline marks retention/archive expiry; the APP marks the two
 * user-initiated paths at their source of truth, the BFF catalog route:
 * deleting an ITEM marks `assets/{collection}/{item}/`, deleting a
 * COLLECTION marks `assets/{collection}/` (which is what finally makes the
 * ADR 0009 collection-delete warning true — I-51's GC half). Marks are
 * key PREFIXES under the platform bucket; `pipeline.asset_collect` deletes
 * everything under them once the collection's grace has passed.
 *
 * Failure-tolerant by design: the catalog delete has already succeeded
 * upstream and cannot be rolled back, so a failed mark logs loudly instead
 * of failing the request — the bytes are orphaned no worse than before M2-F,
 * and re-deleting is idempotent.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { CANONICAL_PREFIX, assertSafeSegment } from "@/lib/storage/keys";
import { getCollectionSettings } from "@/lib/collections/settings";
import { isCubeItemId } from "@/lib/cubes/reserved";

export type GcReason = "item_delete" | "collection_delete";

export interface GcMarkInput {
  collectionId: string;
  /** null → whole-collection mark (collection delete). */
  itemId: string | null;
  reason: GcReason;
}

export function gcPrefix(collectionId: string, itemId: string | null): string {
  assertSafeSegment(collectionId, "collection");
  if (itemId !== null) assertSafeSegment(itemId, "item");
  return itemId === null
    ? `${CANONICAL_PREFIX}/${collectionId}/`
    : `${CANONICAL_PREFIX}/${collectionId}/${itemId}/`;
}

/** Insert one open mark (idempotent via the open-key partial unique index),
 * with `collect_after` from the collection's `gc_grace_days`. */
export async function markAssetGc(input: GcMarkInput): Promise<void> {
  // Z-2 (ADR 0022): assets/{c}/_cube/ is a cube repository, never an item's
  // bytes. It goes only with the whole-collection mark.
  if (input.itemId !== null && isCubeItemId(input.itemId)) return;
  await runMigrations();
  const settings = await getCollectionSettings(input.collectionId);
  await query(
    `INSERT INTO stac_higher.asset_gc
       (object_key, collection_id, item_id, reason, collect_after)
     VALUES ($1, $2, $3, $4, now() + make_interval(days => $5))
     ON CONFLICT (object_key) WHERE collected_at IS NULL DO NOTHING`,
    [
      gcPrefix(input.collectionId, input.itemId),
      input.collectionId,
      input.itemId,
      input.reason,
      settings.gcGraceDays,
    ],
  );
}

/** Best-effort wrapper for the BFF path — see the module doc. */
export async function markAssetGcTolerant(input: GcMarkInput): Promise<void> {
  try {
    await markAssetGc(input);
  } catch (err) {
    console.error(
      `[gc] failed to mark ${input.reason} for ${input.collectionId}/${input.itemId ?? "*"} — canonical bytes may be orphaned until re-marked:`,
      err instanceof Error ? err.message : err,
    );
  }
}
