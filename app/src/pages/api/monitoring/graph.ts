/**
 * /api/monitoring/graph — the pipeline graph (P9-E, M5-E).
 *
 * GET — member+ scoped, the same derived rules as `/api/monitoring/flows`:
 *       connections and processes by their own `group_id`, collections via
 *       `collection_settings` (an unowned collection is public, per ADR 0003).
 *
 * Nodes and edges come from `lib/graph/*` — the SAME module the M5-D cycle
 * check walks. That sharing is deliberate: a loop the graph draws but the
 * write gate permits (or vice versa) would be a UI contradicting its own API.
 * Note the graph returns ingest/deliver edges too, which the cycle check
 * deliberately does NOT traverse — drawing a connection hop is useful, but
 * treating it as a decidable data path is not (I-64).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { loadGraph } from "@/lib/graph/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to view the pipeline graph",
    );
  }
  try {
    const graph = await loadGraph(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, graph);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
