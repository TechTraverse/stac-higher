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
import { iso } from "./storage";

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

function toApiBackfill(row: BackfillRow): ApiBackfill {
  return {
    id: row.id,
    association_id: row.association_id,
    requested_by: row.requested_by,
    status: row.status,
    items_enqueued: row.items_enqueued,
    error: row.error,
    created_at: iso(row.created_at),
    started_at: iso(row.started_at),
    finished_at: iso(row.finished_at),
  };
}

/**
 * Insert a 'queued' backfill, or return null when one is already open for
 * the association. Atomic: the partial unique index (migration 013) is the
 * arbiter, so concurrent requests cannot stack duplicate bulk work — the
 * route maps null to its 409.
 */
export async function insertBackfill(
  associationId: string,
  requestedBy: string,
): Promise<ApiBackfill | null> {
  await runMigrations();
  const result = await query<BackfillRow>(
    `INSERT INTO stac_higher.delivery_backfills (association_id, requested_by)
     VALUES ($1, $2)
     ON CONFLICT (association_id) WHERE status IN ('queued','running')
     DO NOTHING
     RETURNING ${BACKFILL_COLUMNS}`,
    [associationId, requestedBy],
  );
  return result.rows[0] ? toApiBackfill(result.rows[0]) : null;
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

