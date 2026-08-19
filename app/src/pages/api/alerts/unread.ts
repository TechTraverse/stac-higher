/**
 * GET /api/alerts/unread — the caller's unread firing-alert count (M2-C).
 *
 * The in-app channel's read half: unread = firing alerts (group-scoped like
 * /api/alerts) first RAISED after the caller's read watermark. Feeds the
 * M2-D header bell. Member+ — personal UI state, not an operator verb.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/connections/access";
import { countUnreadAlerts } from "@/lib/alerts/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to read alert state",
    );
  }
  try {
    const count = await countUnreadAlerts(
      auth.identity.sub,
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, { unread: count });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
