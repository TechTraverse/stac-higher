/**
 * /api/alerts — list (M2-B, ROADMAP §6.6 + §7).
 *
 * GET — any authenticated user (member+), scoped like the connections list:
 * members/operators see alerts whose (derived) group is one of theirs; admins
 * see all. Optional filters: ?state=firing|acknowledged|resolved|open
 * ("open" = firing + acknowledged) and ?limit=N (1-200, default 50).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/connections/access";
import { listAlerts, type ListAlertsOptions } from "@/lib/alerts/storage";

const STATES = new Set(["firing", "acknowledged", "resolved", "open"]);

export const GET: APIRoute = async ({ url, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list alerts",
    );
  }
  const stateParam = url.searchParams.get("state");
  if (stateParam !== null && !STATES.has(stateParam)) {
    return jsonResponse(400, {
      error: "state must be one of firing, acknowledged, resolved, open",
    });
  }
  const limitParam = url.searchParams.get("limit");
  const limit = limitParam === null ? undefined : Number(limitParam);
  if (limit !== undefined && (!Number.isInteger(limit) || limit < 1 || limit > 200)) {
    return jsonResponse(400, { error: "limit must be an integer between 1 and 200" });
  }
  try {
    const options: ListAlertsOptions = {
      state: (stateParam as ListAlertsOptions["state"]) ?? undefined,
      limit,
    };
    const alerts = await listAlerts(
      isAdmin(auth.identity) ? null : auth.identity.groups,
      options,
    );
    return jsonResponse(200, { alerts });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
