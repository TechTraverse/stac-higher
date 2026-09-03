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
 *   - retention_max_items NULL → no count cap (W-2)
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
  /** null = no count cap; otherwise keep the newest N by item datetime (W-2).
   * Unions with `retentionDays`; `archived` overrides both. */
  retentionMaxItems: number | null;
  gcGraceDays: number;
  /** ADR 0009's archived state (declarative until M2-F's GC honors it). */
  archived: boolean;
  /**
   * Advertise the local OGC serving endpoints (titiler-pgstac / tipg) on the
   * collection page. LINK-LEVEL only — nothing gates the services themselves
   * until per-collection read visibility (I-1) lands.
   */
  servingEnabled: boolean;
}

export function defaultCollectionSettings(
  collectionId: string,
): CollectionSettings {
  return {
    collectionId,
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    retentionMaxItems: null,
    gcGraceDays: DEFAULT_GC_GRACE_DAYS,
    archived: false,
    servingEnabled: false,
  };
}

interface CollectionSettingsRow {
  collection_id: string;
  group_id: string | null;
  externally_writable: boolean;
  retention_days: number | null;
  retention_max_items: number | null;
  gc_grace_days: number;
  archived: boolean;
  serving_enabled: boolean;
}

export async function getCollectionSettings(
  collectionId: string,
): Promise<CollectionSettings> {
  const result = await query<CollectionSettingsRow>(
    `SELECT collection_id, group_id, externally_writable, retention_days, retention_max_items,
            gc_grace_days, archived, serving_enabled
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
    retentionMaxItems: row.retention_max_items,
    gcGraceDays: row.gc_grace_days,
    archived: row.archived,
    servingEnabled: row.serving_enabled,
  };
}

export interface CollectionSettingsUpdate {
  groupId: string | null;
  externallyWritable: boolean;
  retentionDays: number | null;
  retentionMaxItems: number | null;
  gcGraceDays: number;
  archived: boolean;
  servingEnabled: boolean;
}

/**
 * Full-document upsert (the Settings form submits every field; the table is
 * sparse, so the first save creates the row). Returns the stored settings.
 */
export async function upsertCollectionSettings(
  collectionId: string,
  update: CollectionSettingsUpdate,
): Promise<CollectionSettings> {
  await query(
    `INSERT INTO stac_higher.collection_settings
       (collection_id, group_id, externally_writable, retention_days, retention_max_items,
        gc_grace_days, archived, serving_enabled)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     ON CONFLICT (collection_id) DO UPDATE SET
       group_id = EXCLUDED.group_id,
       externally_writable = EXCLUDED.externally_writable,
       retention_days = EXCLUDED.retention_days,
       retention_max_items = EXCLUDED.retention_max_items,
       gc_grace_days = EXCLUDED.gc_grace_days,
       archived = EXCLUDED.archived,
       serving_enabled = EXCLUDED.serving_enabled,
       updated_at = now()`,
    [
      collectionId,
      update.groupId,
      update.externallyWritable,
      update.retentionDays,
      update.retentionMaxItems,
      update.gcGraceDays,
      update.archived,
      update.servingEnabled,
    ],
  );
  return getCollectionSettings(collectionId);
}
