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
 *
 * Per-status counts (M2-A) come from the pipeline-maintained
 * `collection_connections.flow_stats.counts` snapshot instead of aggregating
 * the association's whole log on this 15s-polled path (unbounded by M3). Two
 * carve-outs keep the snapshot honest: `listDeliveries` falls back to the
 * legacy aggregate until the pipeline's first write seeds the `counts` key,
 * and `redeliverDeadRow` applies its own dead→failed delta in the same
 * statement as the flip — the one flow_stats write the app performs (the
 * "never writes flow_stats" rule in `storage.ts` is about user edits
 * clobbering telemetry, not this counter).
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

const ZERO_COUNTS: DeliveryCounts = {
  pending: 0,
  delivering: 0,
  delivered: 0,
  failed: 0,
  dead: 0,
};

/** Zero-filled counts from a flow_stats `counts` bag (extra keys dropped). */
function toCounts(raw: Record<string, unknown>): DeliveryCounts {
  const counts = { ...ZERO_COUNTS };
  for (const status of Object.keys(counts) as DeliveryStatus[]) {
    const value = raw[status];
    if (typeof value === "number" && Number.isFinite(value)) {
      counts[status] = value;
    }
  }
  return counts;
}

/**
 * Recent delivery rows (most recently touched first) plus per-status counts
 * for the association's WHOLE log, so the summary stays truthful even when
 * the list is truncated. Counts read the pipeline's flow_stats snapshot; an
 * association the pipeline has not written since M2-A landed (no `counts` key
 * yet) falls back to the legacy aggregate — the snapshot is seeded on the
 * pipeline's next transition, so the fallback's unbounded GROUP BY only ever
 * runs for that transitional tail.
 */
export async function listDeliveries(
  associationId: string,
  limit = 20,
): Promise<DeliveryListing> {
  await runMigrations();
  const [rows, stats] = await Promise.all([
    query<DeliveryRow>(
      `SELECT ${DELIVERY_COLUMNS}
         FROM stac_higher.delivery_log
        WHERE association_id = $1
        ORDER BY updated_at DESC
        LIMIT $2`,
      [associationId, limit],
    ),
    query<{ counts: Record<string, unknown> | null }>(
      `SELECT flow_stats -> 'counts' AS counts
         FROM stac_higher.collection_connections
        WHERE id = $1`,
      [associationId],
    ),
  ]);
  const snapshot = stats.rows[0]?.counts;
  if (snapshot) {
    return { deliveries: rows.rows.map(toApiDelivery), counts: toCounts(snapshot) };
  }
  const totals = await query<{ status: DeliveryStatus; count: string }>(
    `SELECT status, count(*)::text AS count
       FROM stac_higher.delivery_log
      WHERE association_id = $1
      GROUP BY status`,
    [associationId],
  );
  const counts = { ...ZERO_COUNTS };
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
  // Single statement: the dead→failed counter delta rides the flip's CTE so
  // the flow_stats snapshot cannot drift from the row (M2-A). The
  // `? 'counts'` guard skips associations the pipeline hasn't seeded yet —
  // listDeliveries falls back to the aggregate for those.
  const result = await query<DeliveryRow>(
    `WITH flipped AS (
       UPDATE stac_higher.delivery_log
          SET status = 'failed', attempts = 0,
              next_attempt_at = now(), updated_at = now()
        WHERE id = $1 AND association_id = $2 AND status = 'dead'
        RETURNING ${DELIVERY_COLUMNS}
     ), stats AS (
       UPDATE stac_higher.collection_connections cc
          SET flow_stats = jsonb_set(
                jsonb_set(
                  cc.flow_stats,
                  '{counts,failed}',
                  to_jsonb(coalesce((cc.flow_stats #>> '{counts,failed}')::bigint, 0) + 1)
                ),
                '{counts,dead}',
                to_jsonb(greatest(coalesce((cc.flow_stats #>> '{counts,dead}')::bigint, 0) - 1, 0))
              )
         FROM flipped
        WHERE cc.id = flipped.association_id AND cc.flow_stats ? 'counts'
     )
     SELECT * FROM flipped`,
    [deliveryId, associationId],
  );
  return result.rows[0] ? toApiDelivery(result.rows[0]) : null;
}
