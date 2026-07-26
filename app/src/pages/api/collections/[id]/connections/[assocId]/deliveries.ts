/**
 * GET /api/collections/[id]/connections/[assocId]/deliveries — delivery
 * status for a deliver association (Slice D): recent delivery_log rows plus
 * per-status counts, so the Data-flow tab can surface delivered/failed/dead
 * state and the redeliver affordance.
 *
 * Access: member+ with association visibility — mirrors the backfill-poll
 * read pattern. The pipeline writes these rows; this surface is read-only.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleAssociation } from "@/lib/associations/access";
import { listDeliveries } from "@/lib/associations/deliveries";

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      false,
    );
    if ("response" in loaded) return loaded.response;
    if (loaded.association.direction !== "deliver") {
      return jsonResponse(400, {
        error: "Delivery status applies to deliver associations only",
      });
    }
    const listing = await listDeliveries(loaded.association.id);
    return jsonResponse(200, listing);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
