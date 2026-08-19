/**
 * POST /api/alerts/[id]/resolve — manually resolve an open alert (M2-B, §3.3).
 *
 * operator|admin (role gated by the middleware guard AND here, defense in
 * depth; audited as action `resolve`). Group-owned like ack. Works from
 * `firing` or `acknowledged`; an already-resolved alert → 409. The condition
 * may still hold — the pipeline's monitor then raises a NEW row, which is the
 * resolve→re-fire cycle that notifies again.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/connections/access";
import { loadActionableAlert } from "@/lib/alerts/access";
import { resolveAlert } from "@/lib/alerts/storage";

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
    const updated = await resolveAlert(loaded.alert.id);
    if (!updated) {
      return jsonResponse(409, { error: "Alert is already resolved" });
    }
    return jsonResponse(200, updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
