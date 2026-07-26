/**
 * POST .../deliveries/[deliveryId]/redeliver — dead-letter recovery
 * (Slice D, ROADMAP §6.4): flip a `dead` delivery_log row back into the retry
 * path (`failed`, fresh attempt cycle, `next_attempt_at = now()`). The
 * pipeline's retry sweep requeues it — the ADR 0004 bridge pattern, like
 * backfill; the app never enqueues jobs directly.
 *
 * Access: operator+ with association visibility (group-owned); the middleware
 * guard audits this route with action "redeliver". Only dead rows qualify —
 * pending/delivering/delivered/failed rows are already in the pipeline's
 * hands (409).
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { isUuid, loadVisibleAssociation } from "@/lib/associations/access";
import { getDelivery, redeliverDeadRow } from "@/lib/associations/deliveries";

export const POST: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      true,
    );
    if ("response" in loaded) return loaded.response;
    if (loaded.association.direction !== "deliver") {
      return jsonResponse(400, {
        error: "Redeliver applies to deliver associations only",
      });
    }
    if (!loaded.association.enabled) {
      return jsonResponse(409, {
        error: "Enable the association before redelivering",
      });
    }
    if (!isUuid(params.deliveryId)) {
      return jsonResponse(404, { error: "Delivery not found" });
    }
    const redelivered = await redeliverDeadRow(
      loaded.association.id,
      params.deliveryId,
    );
    if (!redelivered) {
      // Distinguish "no such row" from "row exists but is not dead" — the
      // UPDATE's status guard makes the flip itself concurrency-safe.
      const existing = await getDelivery(
        loaded.association.id,
        params.deliveryId,
      );
      if (!existing) return jsonResponse(404, { error: "Delivery not found" });
      return jsonResponse(409, {
        error: `Only dead deliveries can be redelivered (status: ${existing.status})`,
      });
    }
    return jsonResponse(202, { delivery: redelivered });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
