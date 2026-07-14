/**
 * collection_settings accessor (ROADMAP §5 COLLECTION_SETTINGS).
 *
 * The table is SPARSE: most collections — including every collection that
 * existed before Phase 1 — have no row. Defaults are applied on read, and
 * `defaultCollectionSettings` is the single source of truth for them:
 *
 *   - group_id NULL       → UNOWNED / PUBLIC: visible to all users, mutable
 *                           by operators of any group and by admins
 *                           (ADR 0003, docs/decisions/0003-preexisting-collections.md)
 *   - externally_writable → false
 *   - retention_days NULL → keep forever
 *   - gc_grace_days       → 30
 */
import { query } from "@/lib/db/connection";

export const DEFAULT_GC_GRACE_DAYS = 30;

export interface CollectionSettings {
  collectionId: string;
  /** null = unowned/public (ADR 0003). */
  groupId: string | null;
  externallyWritable: boolean;
  /** null = keep forever. */
  retentionDays: number | null;
  gcGraceDays: number;
}

export function defaultCollectionSettings(
  collectionId: string,
): CollectionSettings {
  return {
    collectionId,
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    gcGraceDays: DEFAULT_GC_GRACE_DAYS,
  };
}

interface CollectionSettingsRow {
  collection_id: string;
  group_id: string | null;
  externally_writable: boolean;
  retention_days: number | null;
  gc_grace_days: number;
}

export async function getCollectionSettings(
  collectionId: string,
): Promise<CollectionSettings> {
  const result = await query<CollectionSettingsRow>(
    `SELECT collection_id, group_id, externally_writable, retention_days, gc_grace_days
       FROM stac_higher.collection_settings
      WHERE collection_id = $1`,
    [collectionId],
  );
  const row = result.rows[0];
  if (!row) return defaultCollectionSettings(collectionId);
  return {
    collectionId: row.collection_id,
    groupId: row.group_id,
    externallyWritable: row.externally_writable,
    retentionDays: row.retention_days,
    gcGraceDays: row.gc_grace_days,
  };
}
