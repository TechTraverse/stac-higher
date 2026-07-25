/**
 * POST /api/collections/[id]/connections/[assocId]/backfill — request a
 * backfill of the collection's EXISTING items into a deliver association
 * (Slice C, ROADMAP §6.4: late-added associations apply to new items only;
 * backfill is an explicit operator action).
 *
 * The app half of the backfill bridge (ADR 0004 pattern): INSERT one 'queued'
 * stac_higher.delivery_backfills row and return it (202). The pipeline sweep
 * claims it, enqueues chunked bulk delivery jobs, and records progress.
 * Clients poll GET .../backfills/[backfillId].
 *
 * Access: operator+ with association visibility (group-owned); the middleware
 * guard audits this route with action "backfill". Only enabled deliver
 * associations qualify — ingest associations 400, disabled ones 409, and an
 * already-open backfill 409s instead of stacking duplicate bulk work.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleAssociation } from "@/lib/associations/access";
import { hasOpenBackfill, insertBackfill } from "@/lib/associations/backfills";

export const POST: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      true,
    );
    if ("response" in loaded) return loaded.response;
    const association = loaded.association;
    if (association.direction !== "deliver") {
      return jsonResponse(400, {
        error: "Backfill applies to deliver associations only",
      });
    }
    if (!association.enabled) {
      return jsonResponse(409, {
        error: "Enable the association before backfilling",
      });
    }
    if (await hasOpenBackfill(association.id)) {
      return jsonResponse(409, {
        error: "A backfill is already queued or running for this association",
      });
    }
    // loadVisibleAssociation guarantees an authenticated identity here.
    const identity = locals.auth.authenticated ? locals.auth.identity : null;
    const backfill = await insertBackfill(
      association.id,
      identity?.sub ?? "unknown",
    );
    return jsonResponse(202, { backfill });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
