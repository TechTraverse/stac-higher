/**
 * /api/processes/[id]/checks/[checkId] — poll a test run (M5-A, ADR 0004).
 *
 * GET — member+ of the owning group. A READ, so it is not in the guard's
 *       gated table; visibility is scoped by the process's group like every
 *       other read here (the connection-check poll precedent).
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { getProcessCheck } from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.checkId)) {
    return jsonResponse(404, { error: "Test run not found" });
  }

  try {
    const check = await getProcessCheck(loaded.process.id, params.checkId);
    if (!check) return jsonResponse(404, { error: "Test run not found" });
    return jsonResponse(200, check);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
