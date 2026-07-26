/**
 * delivery_log reads + the redeliver action (Slice D, ROADMAP §6.4/§8).
 *
 * The pipeline owns every delivery_log WRITE in the normal lifecycle
 * (pending → delivering → delivered|failed → dead); the app only reads the
 * table for the Data-flow tab's status panel — and performs the one
 * user-initiated write: `redeliverDeadRow`, which flips a dead-lettered row
 * back into the retry path (ADR 0004 bridge pattern — the pipeline's retry
 * sweep picks up `status='failed' AND next_attempt_at <= now()`, so no direct
 * enqueue happens here). `attempts` resets to 0 because a redeliver starts a
 * fresh attempt cycle (I-44 semantics: attempts count per cycle, not lifetime).
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "./storage";

export type DeliveryStatus =
  | "pending"
  | "delivering"
  | "delivered"
  | "failed"
  | "dead";

interface DeliveryRow {
  id: string;
  association_id: string;
  item_id: string;
  status: DeliveryStatus;
  attempts: number;
  bytes: string | number | null;
  error: string | null;
  next_attempt_at: Date | string | null;
  delivered_at: Date | string | null;
  created_at: Date | string;
  updated_at: Date | string;
}

export interface ApiDelivery {
  id: string;
  association_id: string;
  item_id: string;
  status: DeliveryStatus;
  /** Attempts in the CURRENT cycle — a new item event or a redeliver resets
   * the count (I-44), so this is not a lifetime total. */
  attempts: number;
  bytes: number | null;
  error: string | null;
  next_attempt_at: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Per-status row counts for the association (zero-filled). */
export type DeliveryCounts = Record<DeliveryStatus, number>;

const DELIVERY_COLUMNS = `id, association_id, item_id, status, attempts,
  bytes, error, next_attempt_at, delivered_at, created_at, updated_at`;

function toApiDelivery(row: DeliveryRow): ApiDelivery {
  return {
    id: row.id,
    association_id: row.association_id,
    item_id: row.item_id,
    status: row.status,
    attempts: row.attempts,
    bytes: row.bytes === null ? null : Number(row.bytes),
    error: row.error,
    next_attempt_at: iso(row.next_attempt_at),
    delivered_at: iso(row.delivered_at),
    created_at: iso(row.created_at),
    updated_at: iso(row.updated_at),
  };
}

export interface DeliveryListing {
  deliveries: ApiDelivery[];
  counts: DeliveryCounts;
}

/**
 * Recent delivery rows (most recently touched first) plus per-status counts
 * over the association's WHOLE log, so the summary stays truthful even when
 * the list is truncated.
 */
export async function listDeliveries(
  associationId: string,
  limit = 20,
): Promise<DeliveryListing> {
  await runMigrations();
  const [rows, totals] = await Promise.all([
    query<DeliveryRow>(
      `SELECT ${DELIVERY_COLUMNS}
         FROM stac_higher.delivery_log
        WHERE association_id = $1
        ORDER BY updated_at DESC
        LIMIT $2`,
      [associationId, limit],
    ),
    query<{ status: DeliveryStatus; count: string }>(
      `SELECT status, count(*)::text AS count
         FROM stac_higher.delivery_log
        WHERE association_id = $1
        GROUP BY status`,
      [associationId],
    ),
  ]);
  const counts: DeliveryCounts = {
    pending: 0,
    delivering: 0,
    delivered: 0,
    failed: 0,
    dead: 0,
  };
  for (const row of totals.rows) counts[row.status] = Number(row.count);
  return { deliveries: rows.rows.map(toApiDelivery), counts };
}

export async function getDelivery(
  associationId: string,
  deliveryId: string,
): Promise<ApiDelivery | null> {
  await runMigrations();
  const result = await query<DeliveryRow>(
    `SELECT ${DELIVERY_COLUMNS}
       FROM stac_higher.delivery_log
      WHERE id = $1 AND association_id = $2`,
    [deliveryId, associationId],
  );
  return result.rows[0] ? toApiDelivery(result.rows[0]) : null;
}

/**
 * Flip a dead-lettered row back into the retry path: `failed` with a due
 * `next_attempt_at` and a fresh attempt cycle. The `status = 'dead'` guard
 * makes the flip concurrency-safe — returns null when the row is not (or no
 * longer) dead, and the route maps that to its 409.
 */
export async function redeliverDeadRow(
  associationId: string,
  deliveryId: string,
): Promise<ApiDelivery | null> {
  await runMigrations();
  const result = await query<DeliveryRow>(
    `UPDATE stac_higher.delivery_log
        SET status = 'failed', attempts = 0,
            next_attempt_at = now(), updated_at = now()
      WHERE id = $1 AND association_id = $2 AND status = 'dead'
      RETURNING ${DELIVERY_COLUMNS}`,
    [deliveryId, associationId],
  );
  return result.rows[0] ? toApiDelivery(result.rows[0]) : null;
}
