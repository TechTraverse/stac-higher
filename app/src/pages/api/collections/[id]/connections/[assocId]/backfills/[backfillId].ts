/**
 * GET /api/collections/[id]/connections/[assocId]/backfills/[backfillId] —
 * poll a backfill request (Slice C). Read-only: returns the row the pipeline
 * updates (queued → running → completed|failed, with items_enqueued
 * progress).
 *
 * Access: member+ with association visibility (same as reading the
 * association itself).
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { isUuid, loadVisibleAssociation } from "@/lib/associations/access";
import { getBackfill } from "@/lib/associations/backfills";

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      false,
    );
    if ("response" in loaded) return loaded.response;
    if (!isUuid(params.backfillId)) {
      return jsonResponse(404, { error: "Backfill not found" });
    }
    const backfill = await getBackfill(
      loaded.association.id,
      params.backfillId,
    );
    if (!backfill) return jsonResponse(404, { error: "Backfill not found" });
    return jsonResponse(200, { backfill });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
