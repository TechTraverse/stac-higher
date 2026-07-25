/**
 * GET /api/connections/[id]/impact — pre-flight deletion impact (ADR 0009).
 *
 * The counted blast radius the warn-and-proceed dialog shows before a
 * connection DELETE: live associations that will stop, reference-backed items
 * that will be removed from the catalog (per collection), and the history
 * rows that are retained. operator+ (it exists solely to stage a mutation);
 * non-visible connections are 404 like every other connection read.
 */
import type { APIRoute } from "astro";
import { jsonResponse, loadVisibleConnection } from "@/lib/connections/access";
import { connectionDeleteImpact } from "@/lib/connections/deletion";

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleConnection(locals.auth, params.id, true);
    if ("response" in loaded) return loaded.response;
    const impact = await connectionDeleteImpact(loaded.connection.id);
    return jsonResponse(200, { impact });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
