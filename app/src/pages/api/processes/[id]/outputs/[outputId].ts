/**
 * /api/processes/[id]/outputs/[outputId] — detach an output (M5-A).
 *
 * DELETE — operator+ of the owning group. Detaching an output stops FUTURE
 *          publishing; it never touches items already published there. Those
 *          are ordinary catalog items owned by the collection, and ADR 0009's
 *          nothing-cascades-into-history stance applies.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { deleteOutput } from "@/lib/processes/storage";

export const DELETE: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.outputId)) {
    return jsonResponse(404, { error: "Process output not found" });
  }

  try {
    const deleted = await deleteOutput(loaded.process.id, params.outputId);
    if (!deleted) {
      return jsonResponse(404, { error: "Process output not found" });
    }
    return jsonResponse(200, { deleted: true, id: params.outputId });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
