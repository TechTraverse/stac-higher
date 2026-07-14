/**
 * GET /api/audit — audit log viewer seam for the Phase 6 UI.
 *
 * Access (ROADMAP §7 "View audit log"): operator sees rows whose
 * actor_groups overlap the operator's own groups; admin sees everything;
 * member and anonymous are rejected. This read is always enforced (it is a
 * new, sensitive endpoint — the Phase 1 "reads stay open" compatibility rule
 * covers pre-existing surfaces only).
 *
 * Query params:
 *   limit  — page size, 1..200, default 50
 *   before — exclusive id cursor from a previous page's `nextCursor`
 *
 * Response: { entries: AuditRecord[], nextCursor: string | null }
 */
import type { APIRoute } from "astro";
import { listAuditEntries } from "@/lib/audit/log";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

export const GET: APIRoute = async ({ url, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to view the audit log",
    );
  }

  const identity = auth.identity;
  const admin = isAdmin(identity);
  if (!admin && !identity.roles.includes("operator")) {
    return authzError(
      403,
      "forbidden",
      "Viewing the audit log requires the operator or admin role",
    );
  }

  const rawLimit = url.searchParams.get("limit");
  let limit = DEFAULT_LIMIT;
  if (rawLimit !== null) {
    const parsed = Number.parseInt(rawLimit, 10);
    if (Number.isNaN(parsed) || parsed < 1) {
      return new Response(
        JSON.stringify({ error: "limit must be a positive integer" }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      );
    }
    limit = Math.min(parsed, MAX_LIMIT);
  }

  const before = url.searchParams.get("before");
  if (before !== null && !/^\d+$/.test(before)) {
    return new Response(
      JSON.stringify({ error: "before must be a numeric audit id cursor" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  try {
    const { entries, nextCursor } = await listAuditEntries({
      // §7: admins see all groups; operators see their own group's rows.
      groups: admin ? null : identity.groups,
      limit,
      before,
    });
    return new Response(JSON.stringify({ entries, nextCursor }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
};
