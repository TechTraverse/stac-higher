/**
 * /api/processes/[id]/runs — the run ledger, read-only (M5-C, spec §6).
 *
 * GET — member+ of the owning group. The pipeline owns run STATE; the app
 *       only reads it here. `?limit=` is clamped in storage so a UI bug
 *       cannot ask for an unbounded scan of a table the ROADMAP expects at
 *       M3 rates.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { listRuns } from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, request, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;

  try {
    const limitParam = new URL(request.url).searchParams.get("limit");
    const limit = limitParam ? Number(limitParam) : 50;
    return jsonResponse(200, {
      runs: await listRuns(
        loaded.process.id,
        Number.isFinite(limit) ? limit : 50,
      ),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
