/**
 * POST /api/alerts/[id]/ack — acknowledge a firing alert (M2-B, §3.3).
 *
 * operator|admin (role gated by the middleware guard AND here, defense in
 * depth; audited as action `ack`). Group-owned: operators act only on alerts
 * whose derived group is one of theirs. Acknowledging suppresses notification,
 * not detection — the pipeline keeps bumping last_seen, and only `firing`
 * rows can be acknowledged (anything else → 409).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/connections/access";
import { loadActionableAlert } from "@/lib/alerts/access";
import { ackAlert } from "@/lib/alerts/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required for this action");
  }
  if (!canMutate(auth.identity)) {
    return authzError(403, "forbidden", "This action requires the operator or admin role");
  }
  try {
    const loaded = await loadActionableAlert(auth.identity, params.id);
    if ("response" in loaded) return loaded.response;
    const updated = await ackAlert(loaded.alert.id, auth.identity.sub);
    if (!updated) {
      return jsonResponse(409, {
        error: `Only a firing alert can be acknowledged (state: ${loaded.alert.state})`,
      });
    }
    return jsonResponse(200, updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
