/**
 * GET /api/monitoring/history — the daily flow-stats strip (P9-E, M5-F).
 *
 * `?subject_kind=association|process&subject_id=<uuid>&days=30`
 *
 * Feeds the collection lineage strip and the `/processes` sparklines. Member+
 * scoped through the subject's owner: an association by its connection's
 * group, a process source by its process's group — the same derived rules the
 * flows and graph endpoints use.
 *
 * The window is CLAMPED server-side. This reads a table whose row count is
 * subjects x days, and an unbounded `?days=` would let one page view scan the
 * whole retention window.
 *
 * Today's bucket is deliberately NOT in the table (the rollup writes complete
 * days only), so a caller asking for "the last 30 days" gets up to 30
 * finished days and derives today live from `flow_stats` if it wants it.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { listDailyStats, SUBJECT_KINDS } from "@/lib/monitoring/history";

const MAX_DAYS = 400;
const DEFAULT_DAYS = 30;

export const GET: APIRoute = async ({ request, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to view flow history",
    );
  }

  const params = new URL(request.url).searchParams;
  const subjectKind = params.get("subject_kind") ?? "";
  const subjectId = params.get("subject_id") ?? "";
  if (!SUBJECT_KINDS.includes(subjectKind as (typeof SUBJECT_KINDS)[number])) {
    return jsonResponse(400, {
      error: `subject_kind must be one of ${SUBJECT_KINDS.join(", ")}`,
    });
  }
  if (!isUuid(subjectId)) {
    return jsonResponse(400, { error: "subject_id must be a UUID" });
  }

  const requested = Number(params.get("days") ?? DEFAULT_DAYS);
  const days = Number.isFinite(requested)
    ? Math.min(Math.max(Math.trunc(requested), 1), MAX_DAYS)
    : DEFAULT_DAYS;

  try {
    const rows = await listDailyStats(
      subjectKind as (typeof SUBJECT_KINDS)[number],
      subjectId,
      days,
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    // Null means the subject exists but is not the caller's to see, or does
    // not exist at all — indistinguishable on purpose, like every other
    // group-scoped read here.
    if (rows === null) return jsonResponse(404, { error: "Subject not found" });
    return jsonResponse(200, { subject_kind: subjectKind, subject_id: subjectId, days: rows });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
