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
  /** ADR 0009's archived state (declarative until M2-F's GC honors it). */
  archived: boolean;
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
    archived: false,
  };
}

interface CollectionSettingsRow {
  collection_id: string;
  group_id: string | null;
  externally_writable: boolean;
  retention_days: number | null;
  gc_grace_days: number;
  archived: boolean;
}

export async function getCollectionSettings(
  collectionId: string,
): Promise<CollectionSettings> {
  const result = await query<CollectionSettingsRow>(
    `SELECT collection_id, group_id, externally_writable, retention_days, gc_grace_days, archived
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
    archived: row.archived,
  };
}

export interface CollectionSettingsUpdate {
  groupId: string | null;
  externallyWritable: boolean;
  retentionDays: number | null;
  gcGraceDays: number;
  archived: boolean;
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
       (collection_id, group_id, externally_writable, retention_days, gc_grace_days, archived)
     VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT (collection_id) DO UPDATE SET
       group_id = EXCLUDED.group_id,
       externally_writable = EXCLUDED.externally_writable,
       retention_days = EXCLUDED.retention_days,
       gc_grace_days = EXCLUDED.gc_grace_days,
       archived = EXCLUDED.archived,
       updated_at = now()`,
    [
      collectionId,
      update.groupId,
      update.externallyWritable,
      update.retentionDays,
      update.gcGraceDays,
      update.archived,
    ],
  );
  return getCollectionSettings(collectionId);
}
