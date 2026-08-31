/**
 * Daily flow-stats history reads (P9-E, M5-F).
 *
 * The pipeline writes `flow_stats_daily`; the app only reads it. Scoping is
 * derived from the subject's owner in the SAME query that fetches the rows —
 * a separate visibility check would be a second round trip and a chance for
 * the two to disagree.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

export const SUBJECT_KINDS = ["association", "process"] as const;
export type SubjectKind = (typeof SUBJECT_KINDS)[number];

export interface DailyStatsRow {
  day: string;
  files: number;
  items: number;
  bytes: number;
  delivered: number;
  failed: number;
  dead: number;
  runs: number;
}

interface Row extends Omit<DailyStatsRow, "day"> {
  day: Date | string;
}

/**
 * One subject's trailing window, oldest first, or `null` when the subject is
 * not visible to the caller.
 *
 * Days with no activity are ABSENT rather than zero-filled: the rollup writes
 * a row per subject per day regardless, so a gap means the job did not run,
 * which is worth being able to tell apart from a genuinely quiet day. The UI
 * fills the calendar.
 */
export async function listDailyStats(
  subjectKind: SubjectKind,
  subjectId: string,
  days: number,
  groups: string[] | null,
): Promise<DailyStatsRow[] | null> {
  await runMigrations();

  // Ownership: an association through its connection, a process source
  // through its process. `groups === null` is admin.
  const ownerSql =
    subjectKind === "association"
      ? `SELECT c.group_id
           FROM stac_higher.collection_connections cc
           JOIN stac_higher.connections c ON c.id = cc.connection_id
          WHERE cc.id = $1 AND cc.deleted_at IS NULL`
      : `SELECT p.group_id
           FROM stac_higher.process_sources s
           JOIN stac_higher.processes p ON p.id = s.process_id
          WHERE s.id = $1 AND p.deleted_at IS NULL`;

  const owner = await query<{ group_id: string | null }>(ownerSql, [subjectId]);
  if (owner.rows.length === 0) return null;
  const groupId = owner.rows[0].group_id;
  if (groups !== null && (groupId === null || !groups.includes(groupId))) {
    return null;
  }

  const result = await query<Row>(
    `SELECT day, files, items, bytes, delivered, failed, dead, runs
       FROM stac_higher.flow_stats_daily
      WHERE subject_kind = $1 AND subject_id = $2
        AND day >= (CURRENT_DATE - $3::int)
      ORDER BY day`,
    [subjectKind, subjectId, days],
  );
  return result.rows.map((row) => ({
    ...row,
    day:
      row.day instanceof Date
        ? row.day.toISOString().slice(0, 10)
        : String(row.day).slice(0, 10),
  }));
}
