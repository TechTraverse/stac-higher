/**
 * Audit log write + read path (ROADMAP §5 AUDIT_LOG, §5.5 "Audit").
 *
 * Invariants:
 *   - APPEND-ONLY. This module exposes no update/delete; the table also has
 *     BEFORE UPDATE/DELETE/TRUNCATE triggers that reject mutation (migration
 *     003).
 *   - `detail` NEVER contains secrets. `sanitizeDetail` redacts
 *     credential-shaped keys and values defensively before every insert —
 *     callers should still never pass request bodies wholesale.
 *   - Audit failures never fail the audited request. `writeAudit` catches
 *     everything, logs loudly, and resolves `false`.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

export interface AuditEntry {
  /** Canonical subject (`sub` claim) or `"anonymous"`. */
  actor: string;
  actorGroups: string[];
  /** e.g. create | update | delete | login | logout (ROADMAP §5). */
  action: string;
  resourceType: string;
  resourceId?: string | null;
  /** Context for the event. Redacted before insert; never pass secrets. */
  detail?: Record<string, unknown>;
}

export interface AuditRecord {
  /** bigserial — kept as string to stay bigint-safe in JSON. */
  id: string;
  actor: string;
  actorGroups: string[];
  action: string;
  resourceType: string;
  resourceId: string | null;
  detail: Record<string, unknown>;
  at: string;
}

const REDACTED = "[REDACTED]";
const MAX_DEPTH = 6;

/** Keys whose values are credential-shaped regardless of content. */
const SECRET_KEY_PATTERN =
  /(pass(word|phrase)?|secret|token|credential|api[-_]?key|private[-_]?key|authorization|auth[-_]?header|cookie|session|client[-_]?secret)/i;

/** Values that look like credentials even under an innocent key. */
function isSecretShapedValue(value: string): boolean {
  // JWT: three dot-separated base64url segments starting with a JSON header.
  if (/^eyJ[\w-]+\.[\w-]+\.[\w-]*$/.test(value)) return true;
  if (/^(Bearer|Basic)\s+\S+/i.test(value)) return true;
  if (value.includes("-----BEGIN")) return true;
  return false;
}

/**
 * Recursively redact credential-shaped keys and values. Depth-capped so a
 * pathological payload cannot recurse forever.
 */
export function sanitizeDetail(value: unknown, depth = 0): unknown {
  if (depth > MAX_DEPTH) return "[TRUNCATED]";
  if (typeof value === "string") {
    return isSecretShapedValue(value) ? REDACTED : value;
  }
  if (Array.isArray(value)) {
    return value.map((v) => sanitizeDetail(v, depth + 1));
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
      out[key] = SECRET_KEY_PATTERN.test(key)
        ? REDACTED
        : sanitizeDetail(v, depth + 1);
    }
    return out;
  }
  return value;
}

/**
 * Insert one audit row. Never throws — an audit failure must not 500 the
 * audited request; it logs loudly and resolves `false` instead. Callers that
 * need the row (tests) can await and assert the return value.
 */
export async function writeAudit(entry: AuditEntry): Promise<boolean> {
  try {
    // Idempotent + memoized; makes login/logout audit work even on the very
    // first request, before any /api/extensions call triggered migrations.
    await runMigrations();
    await query(
      `INSERT INTO stac_higher.audit_log
         (actor, actor_groups, action, resource_type, resource_id, detail)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [
        entry.actor,
        entry.actorGroups,
        entry.action,
        entry.resourceType,
        entry.resourceId ?? null,
        JSON.stringify(sanitizeDetail(entry.detail ?? {})),
      ],
    );
    return true;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(
      `[audit] FAILED to write audit row (action=${entry.action} actor=${entry.actor} resource=${entry.resourceType}/${entry.resourceId ?? "-"}): ${msg}`,
    );
    return false;
  }
}

export interface AuditQueryOptions {
  /**
   * Group visibility filter (ROADMAP §7): operators see rows whose
   * actor_groups overlap their own groups; admins pass `null` for all rows.
   * An empty array matches nothing.
   */
  groups: string[] | null;
  /** Page size (caller-clamped; defaults to 50 here as a backstop). */
  limit?: number;
  /** Exclusive cursor: return rows with id < before (newest-first pages). */
  before?: string | null;
}

interface AuditRow {
  id: string;
  actor: string;
  actor_groups: string[];
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: Record<string, unknown>;
  at: Date;
}

export async function listAuditEntries(
  opts: AuditQueryOptions,
): Promise<{ entries: AuditRecord[]; nextCursor: string | null }> {
  const limit = opts.limit ?? 50;
  const where: string[] = [];
  const params: unknown[] = [];

  if (opts.groups !== null) {
    params.push(opts.groups);
    where.push(`actor_groups && $${params.length}::text[]`);
  }
  if (opts.before) {
    params.push(opts.before);
    where.push(`id < $${params.length}::bigint`);
  }
  params.push(limit + 1);

  const result = await query<AuditRow>(
    `SELECT id, actor, actor_groups, action, resource_type, resource_id, detail, at
       FROM stac_higher.audit_log
       ${where.length > 0 ? `WHERE ${where.join(" AND ")}` : ""}
       ORDER BY id DESC
       LIMIT $${params.length}`,
    params,
  );

  const hasMore = result.rows.length > limit;
  const rows = hasMore ? result.rows.slice(0, limit) : result.rows;
  const entries = rows.map((row) => ({
    id: String(row.id),
    actor: row.actor,
    actorGroups: row.actor_groups,
    action: row.action,
    resourceType: row.resource_type,
    resourceId: row.resource_id,
    detail: row.detail,
    at: row.at instanceof Date ? row.at.toISOString() : String(row.at),
  }));

  return {
    entries,
    nextCursor: hasMore ? entries[entries.length - 1].id : null,
  };
}
