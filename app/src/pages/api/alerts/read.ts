/**
 * POST /api/alerts/read — advance the caller's read watermark to now (M2-C).
 *
 * Marks the bell as opened: every currently-raised alert stops counting as
 * unread for THIS user. Member+ and deliberately NOT operator-gated or
 * audited — it is personal UI state, not a mutation of shared data (the
 * shared verbs are ack/resolve on the alert itself).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/connections/access";
import { markAlertsRead } from "@/lib/alerts/storage";

export const POST: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to mark alerts read",
    );
  }
  try {
    const lastReadAt = await markAlertsRead(auth.identity.sub);
    return jsonResponse(200, { last_read_at: lastReadAt });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
