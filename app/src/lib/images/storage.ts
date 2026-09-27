/**
 * `container_images` reads for the deploy gate (C-1). C-3 grows this module
 * with the registry CRUD. The app owns the DDL (migration 030); the
 * pipeline writes statuses, verdicts and scan times.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ImageStatus } from "./status";

export interface ImageGateRow {
  id: string;
  reference: string;
  digest: string | null;
  status: ImageStatus;
  last_scanned_at: Date | null;
  registry_connection_id: string | null;
  /** NULL when the row carries no credential OR its connection is soft-deleted. */
  registry_connection_group_id: string | null;
}

interface GateQueryRow extends Omit<ImageGateRow, "last_scanned_at"> {
  last_scanned_at: Date | string | null;
}

export async function getImageForGate(id: string): Promise<ImageGateRow | null> {
  await runMigrations();
  const result = await query<GateQueryRow>(
    `SELECT i.id, i.reference, i.digest, i.status, i.last_scanned_at,
            i.registry_connection_id, c.group_id AS registry_connection_group_id
       FROM stac_higher.container_images i
       LEFT JOIN stac_higher.connections c
         ON c.id = i.registry_connection_id AND c.deleted_at IS NULL
      WHERE i.id = $1`,
    [id],
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    ...row,
    last_scanned_at: row.last_scanned_at === null ? null : new Date(row.last_scanned_at),
  };
}
