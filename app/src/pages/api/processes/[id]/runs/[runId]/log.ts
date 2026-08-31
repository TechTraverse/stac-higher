/**
 * GET /api/processes/[id]/runs/[runId]/log — the captured run log (§9, I-62).
 *
 * Authorize → 302 to a short-lived presigned URL for the log object, the same
 * shape as the asset route. The app never streams the bytes: run logs can be
 * up to `PROCESS_LOG_MAX_BYTES` (10 MB) and proxying them would put untrusted
 * user output through the app process for no benefit.
 *
 * Visibility is the process's group (member+), which is stricter than the
 * asset route's authentication-only check — deliberately: a run log is
 * arbitrary output from an operator's own code, so it stays inside the group
 * that owns the process rather than being readable by any authenticated user.
 *
 * A run with no `log_ref` is a 404, not an empty redirect: the log either
 * failed to store or the run never produced one, and both are better said
 * than papered over.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { getRun } from "@/lib/processes/storage";
import { presignGetUrl } from "@/lib/storage/presign";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.runId)) return jsonResponse(404, { error: "Run not found" });

  try {
    const run = await getRun(loaded.process.id, params.runId);
    if (!run) return jsonResponse(404, { error: "Run not found" });
    if (!run.log_ref) {
      return jsonResponse(404, { error: "This run has no stored log" });
    }
    const url = await presignGetUrl(run.log_ref);
    return new Response(null, {
      status: 302,
      headers: {
        Location: url,
        // Presigned URLs are short-lived and per-request; a shared cache must
        // never hand one to a different caller (the asset-route rule).
        "Cache-Control": "private, no-store",
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
