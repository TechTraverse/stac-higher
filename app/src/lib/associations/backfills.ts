/**
 * delivery_backfills persistence — the app half of the backfill bridge
 * (Slice C, ROADMAP §6.4; bridge pattern per ADR 0004).
 *
 * The app only INSERTs 'queued' rows and polls them; the pipeline's sweep
 * claims open rows (FOR UPDATE SKIP LOCKED), enqueues chunked bulk delivery
 * jobs for the collection's existing items, records progress
 * (items_enqueued / cursor_item_id), and stamps the terminal status.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

interface BackfillRow {
  id: string;
  association_id: string;
  requested_by: string;
  status: "queued" | "running" | "completed" | "failed";
  items_enqueued: number;
  error: string | null;
  created_at: Date | string;
  started_at: Date | string | null;
  finished_at: Date | string | null;
}

export interface ApiBackfill {
  id: string;
  association_id: string;
  requested_by: string;
  status: "queued" | "running" | "completed" | "failed";
  items_enqueued: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

const BACKFILL_COLUMNS = `id, association_id, requested_by, status,
  items_enqueued, error, created_at, started_at, finished_at`;

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

function toApiBackfill(row: BackfillRow): ApiBackfill {
  return {
    id: row.id,
    association_id: row.association_id,
    requested_by: row.requested_by,
    status: row.status,
    items_enqueued: row.items_enqueued,
    error: row.error,
    created_at: iso(row.created_at) as string,
    started_at: iso(row.started_at),
    finished_at: iso(row.finished_at),
  };
}

export async function insertBackfill(
  associationId: string,
  requestedBy: string,
): Promise<ApiBackfill> {
  await runMigrations();
  const result = await query<BackfillRow>(
    `INSERT INTO stac_higher.delivery_backfills (association_id, requested_by)
     VALUES ($1, $2)
     RETURNING ${BACKFILL_COLUMNS}`,
    [associationId, requestedBy],
  );
  return toApiBackfill(result.rows[0]);
}

export async function getBackfill(
  associationId: string,
  backfillId: string,
): Promise<ApiBackfill | null> {
  await runMigrations();
  const result = await query<BackfillRow>(
    `SELECT ${BACKFILL_COLUMNS}
       FROM stac_higher.delivery_backfills
      WHERE id = $1 AND association_id = $2`,
    [backfillId, associationId],
  );
  return result.rows[0] ? toApiBackfill(result.rows[0]) : null;
}

/**
 * True when the association already has a queued/running backfill — the POST
 * route 409s instead of stacking duplicate bulk work.
 */
export async function hasOpenBackfill(associationId: string): Promise<boolean> {
  await runMigrations();
  const result = await query<{ id: string }>(
    `SELECT id FROM stac_higher.delivery_backfills
      WHERE association_id = $1 AND status IN ('queued','running')
      LIMIT 1`,
    [associationId],
  );
  return result.rows.length > 0;
}
