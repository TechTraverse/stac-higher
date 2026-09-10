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
 * Collection-anchored alerts (P7-H `push_rejected`) derive their group from
 * `collection_settings.group_id` the same way: an unowned collection yields a
 * null group, i.e. admin-only visibility (set collection ownership if your
 * operators should see push alerts).
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
  channel_id: string | null;
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
  process_id: string | null;
  source_id: string | null;
}

export interface ApiAlert {
  id: string;
  source: AlertSource;
  kind: string;
  connection_id: string | null;
  association_id: string | null;
  /** Set on channel-anchored alerts (M2-C `webhook_failed`). */
  channel_id: string | null;
  state: AlertState;
  message: string;
  first_seen: string;
  last_seen: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  /** Derived for display/scoping: the connection's, channel's, or (P7-H)
   * collection-settings group — null when the anchor row is gone or the
   * collection is unowned (admin-only). */
  group_id: string | null;
  connection_name: string | null;
  /** The alert's own collection anchor (P7-H `push_rejected`), falling back
   * to the association's collection for display. */
  collection_id: string | null;
  /** The EFFECTIVE process for a process-anchored alert: the alert's own
   * `process_id`, else the parent of its `source_id` (`process_stalled`
   * anchors on a SOURCE — I-63). Same read-time COALESCE shape as
   * `collection_id`. Null for every non-process kind. (I-84) */
  process_id: string | null;
  /** The raw `process_sources.id` anchor, when the alert has one. */
  source_id: string | null;
}

/** ApiAlert plus nothing extra — the group is already on the shape; alias for
 * route-side readability where the group check is the point. */
export type AlertWithGroup = ApiAlert;

// Joined through the association when the alert carries one: the effective
// connection is COALESCE(direct, association's). Channel-anchored alerts
// (M2-C webhook failures) derive their group from the channel instead;
// collection-anchored alerts (P7-H push rejections) from the collection's
// settings row. No deleted_at filters — alerts on a soft-deleted connection
// remain visible history.
const ALERT_SELECT = `
  SELECT a.id, a.source, a.kind, a.connection_id, a.association_id,
         a.channel_id, a.state, a.message, a.first_seen, a.last_seen,
         a.acknowledged_at, a.acknowledged_by, a.resolved_at,
         COALESCE(c.group_id, nch.group_id, cs.group_id, pr.group_id) AS group_id,
         c.name AS connection_name,
         COALESCE(a.collection_id, cc.collection_id) AS collection_id,
         COALESCE(a.process_id, ps.process_id) AS process_id,
         a.source_id
    FROM stac_higher.alerts a
    LEFT JOIN stac_higher.collection_connections cc ON cc.id = a.association_id
    LEFT JOIN stac_higher.connections c
      ON c.id = COALESCE(a.connection_id, cc.connection_id)
    LEFT JOIN stac_higher.notification_channels nch ON nch.id = a.channel_id
    LEFT JOIN stac_higher.collection_settings cs
      ON cs.collection_id = a.collection_id
    -- M5-E: process-anchored alerts. process_stalled anchors on a SOURCE
    -- (I-63), so the group comes through its parent process — one more hop
    -- than the other anchors, but the same derived-at-read-time shape.
    LEFT JOIN stac_higher.process_sources ps ON ps.id = a.source_id
    LEFT JOIN stac_higher.processes pr
      ON pr.id = COALESCE(a.process_id, ps.process_id)`;

function toApiAlert(row: AlertRow): ApiAlert {
  return {
    id: row.id,
    source: row.source,
    kind: row.kind,
    connection_id: row.connection_id,
    association_id: row.association_id,
    channel_id: row.channel_id,
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
    process_id: row.process_id,
    source_id: row.source_id,
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
    where.push(
      `COALESCE(c.group_id, nch.group_id, cs.group_id, pr.group_id)` +
        ` = ANY($${params.length}::text[])`,
    );
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

// ---------------------------------------------------------------------------
// Per-user read state (M2-C, spec §4): the in-app channel's "unread" half.
// A single watermark per user, not per-alert rows — "read" means the bell was
// opened; per-row triage is ack/resolve on the alert itself. An alert counts
// as unread while it is FIRING and first RAISED after the watermark, so a
// last_seen bump on an already-seen condition does not re-unread it, while a
// resolve→re-fire cycle (a NEW row) does.
// ---------------------------------------------------------------------------

/**
 * Firing alerts the caller has not seen yet (group-scoped like listAlerts;
 * `groups = null` for admin).
 */
export async function countUnreadAlerts(
  userSub: string,
  groups: string[] | null,
): Promise<number> {
  await runMigrations();
  const params: unknown[] = [userSub];
  let groupClause = "";
  if (groups !== null) {
    params.push(groups);
    groupClause =
      ` AND COALESCE(c.group_id, nch.group_id, cs.group_id, pr.group_id)` +
      ` = ANY($${params.length}::text[])`;
  }
  const result = await query<{ count: string }>(
    `SELECT count(*) AS count
       FROM stac_higher.alerts a
       LEFT JOIN stac_higher.collection_connections cc ON cc.id = a.association_id
       LEFT JOIN stac_higher.connections c
         ON c.id = COALESCE(a.connection_id, cc.connection_id)
       LEFT JOIN stac_higher.notification_channels nch ON nch.id = a.channel_id
       LEFT JOIN stac_higher.collection_settings cs
         ON cs.collection_id = a.collection_id
       LEFT JOIN stac_higher.process_sources ps ON ps.id = a.source_id
       LEFT JOIN stac_higher.processes pr
         ON pr.id = COALESCE(a.process_id, ps.process_id)
      WHERE a.state = 'firing'
        AND a.first_seen > COALESCE(
          (SELECT r.last_read_at FROM stac_higher.alert_reads r
            WHERE r.user_sub = $1),
          '-infinity'::timestamptz)${groupClause}`,
    params,
  );
  return Number(result.rows[0]?.count ?? 0);
}

/** Advance the caller's watermark to now. Returns the new watermark (ISO). */
export async function markAlertsRead(userSub: string): Promise<string> {
  await runMigrations();
  const result = await query<{ last_read_at: Date | string }>(
    `INSERT INTO stac_higher.alert_reads (user_sub, last_read_at)
     VALUES ($1, now())
     ON CONFLICT (user_sub) DO UPDATE SET last_read_at = now()
     RETURNING last_read_at`,
    [userSub],
  );
  return iso(result.rows[0]?.last_read_at ?? null) ?? "";
}
