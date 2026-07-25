/**
 * GET /api/collections/[id]/connections/[assocId]/impact — pre-flight
 * deletion impact for an association (ADR 0009): the history rows that are
 * retained and the reference-backed items that stop being managed. operator+
 * (it exists solely to stage a mutation); non-visible associations 404.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleAssociation } from "@/lib/associations/access";
import { associationDeleteImpact } from "@/lib/associations/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      true,
    );
    if ("response" in loaded) return loaded.response;
    const impact = await associationDeleteImpact(loaded.association.id);
    return jsonResponse(200, { impact });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
