/**
 * Alert reads + the operator ack/resolve transitions (M2-B, ROADMAP §6.6).
 *
 * The PIPELINE owns detection: its flow_monitor job raises alerts, bumps
 * `last_seen` on a re-observed condition, and auto-resolves when the condition
 * clears. The app only reads the table and performs the two user-initiated,
 * audited transitions: `firing → acknowledged` and `* → resolved`. Both are
 * status-guarded single statements, so a concurrent auto-resolve cannot be
 * clobbered (the route maps a no-row result to its 409).
 *
 * Group scoping is derived at read time — alert → connection (directly or via
 * the association) → connections.group_id — so an alert follows its
 * connection's ownership instead of carrying a copy that could drift.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "@/lib/associations/storage";

export type AlertSource = "flow" | "health" | "job_failure";
export type AlertState = "firing" | "acknowledged" | "resolved";

interface AlertRow {
  id: string;
  source: AlertSource;
  kind: string;
  connection_id: string | null;
  association_id: string | null;
  state: AlertState;
  message: string;
  first_seen: Date | string;
  last_seen: Date | string;
  acknowledged_at: Date | string | null;
  acknowledged_by: string | null;
  resolved_at: Date | string | null;
  group_id: string | null;
  connection_name: string | null;
  collection_id: string | null;
}

export interface ApiAlert {
  id: string;
  source: AlertSource;
  kind: string;
  connection_id: string | null;
  association_id: string | null;
  state: AlertState;
  message: string;
  first_seen: string;
  last_seen: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  /** Joined context for display/scoping (null only if the connection row is gone). */
  group_id: string | null;
  connection_name: string | null;
  collection_id: string | null;
}

/** ApiAlert plus nothing extra — the group is already on the shape; alias for
 * route-side readability where the group check is the point. */
export type AlertWithGroup = ApiAlert;

// Joined through the association when the alert carries one: the effective
// connection is COALESCE(direct, association's). No deleted_at filters —
// alerts on a soft-deleted connection remain visible history.
const ALERT_SELECT = `
  SELECT a.id, a.source, a.kind, a.connection_id, a.association_id,
         a.state, a.message, a.first_seen, a.last_seen,
         a.acknowledged_at, a.acknowledged_by, a.resolved_at,
         c.group_id, c.name AS connection_name, cc.collection_id
    FROM stac_higher.alerts a
    LEFT JOIN stac_higher.collection_connections cc ON cc.id = a.association_id
    LEFT JOIN stac_higher.connections c
      ON c.id = COALESCE(a.connection_id, cc.connection_id)`;

function toApiAlert(row: AlertRow): ApiAlert {
  return {
    id: row.id,
    source: row.source,
    kind: row.kind,
    connection_id: row.connection_id,
    association_id: row.association_id,
    state: row.state,
    message: row.message,
    first_seen: iso(row.first_seen) ?? "",
    last_seen: iso(row.last_seen) ?? "",
    acknowledged_at: iso(row.acknowledged_at),
    acknowledged_by: row.acknowledged_by,
    resolved_at: iso(row.resolved_at),
    group_id: row.group_id,
    connection_name: row.connection_name,
    collection_id: row.collection_id,
  };
}

export interface ListAlertsOptions {
  /** "open" = firing + acknowledged (the operator work list). */
  state?: AlertState | "open";
  limit?: number;
}

/**
 * Alerts visible to the caller: `groups = null` for admin (all), otherwise the
 * caller's groups. Firing first, then most recently seen.
 */
export async function listAlerts(
  groups: string[] | null,
  options: ListAlertsOptions = {},
): Promise<ApiAlert[]> {
  await runMigrations();
  const limit = options.limit ?? 50;
  const where: string[] = [];
  const params: unknown[] = [];
  if (groups !== null) {
    params.push(groups);
    where.push(`c.group_id = ANY($${params.length}::text[])`);
  }
  if (options.state === "open") {
    where.push(`a.state <> 'resolved'`);
  } else if (options.state) {
    params.push(options.state);
    where.push(`a.state = $${params.length}`);
  }
  params.push(limit);
  const sql = `${ALERT_SELECT}
    ${where.length ? `WHERE ${where.join(" AND ")}` : ""}
    ORDER BY (a.state = 'firing') DESC, a.last_seen DESC
    LIMIT $${params.length}`;
  const result = await query<AlertRow>(sql, params);
  return result.rows.map(toApiAlert);
}

export async function getAlert(id: string): Promise<ApiAlert | null> {
  await runMigrations();
  const result = await query<AlertRow>(`${ALERT_SELECT} WHERE a.id = $1`, [id]);
  return result.rows[0] ? toApiAlert(result.rows[0]) : null;
}

/**
 * `firing → acknowledged`. Suppresses notification, not detection (§3.3) —
 * the pipeline keeps bumping last_seen. Returns null when the alert is not
 * (or no longer) firing, which the route maps to 409.
 */
export async function ackAlert(
  id: string,
  actor: string,
): Promise<ApiAlert | null> {
  await runMigrations();
  const result = await query<{ id: string }>(
    `UPDATE stac_higher.alerts
        SET state = 'acknowledged', acknowledged_at = now(), acknowledged_by = $2
      WHERE id = $1 AND state = 'firing'
      RETURNING id`,
    [id, actor],
  );
  return result.rows[0] ? getAlert(id) : null;
}

/**
 * Manual resolve from either open state. The condition may still hold — the
 * monitor will then raise a NEW row (which re-notifies, §3.3's re-fire
 * semantics). Returns null when already resolved / missing → route 409/404.
 */
export async function resolveAlert(id: string): Promise<ApiAlert | null> {
  await runMigrations();
  const result = await query<{ id: string }>(
    `UPDATE stac_higher.alerts
        SET state = 'resolved', resolved_at = now()
      WHERE id = $1 AND state <> 'resolved'
      RETURNING id`,
    [id],
  );
  return result.rows[0] ? getAlert(id) : null;
}
