/**
 * /api/processes/[id]/runs/[runId]/rerun — dead-run recovery (M5-C, spec §6).
 *
 * POST — operator+ of the owning group, audited as `rerun` (the `redeliver`
 *        analog). Flips a dead row back to `queued`; the pipeline's run tick
 *        claims it on the next pass. The app executes nothing.
 *
 * Only a DEAD run is re-runnable, and the storage UPDATE enforces that in its
 * WHERE clause rather than here — a check-then-write would race the pipeline
 * claiming the same row. A non-dead (or unknown) run is a 409 with the reason,
 * not a silent no-op, because "I clicked re-run and nothing happened" is the
 * worst possible answer for an operator recovering a flow.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { getRun, rerunRun } from "@/lib/processes/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.runId)) {
    return jsonResponse(404, { error: "Run not found" });
  }

  try {
    const requeued = await rerunRun(loaded.process.id, params.runId);
    if (requeued) return jsonResponse(202, requeued);

    // The conditional UPDATE matched nothing: say WHICH reason.
    const existing = await getRun(loaded.process.id, params.runId);
    if (!existing) return jsonResponse(404, { error: "Run not found" });
    return jsonResponse(409, {
      error: `Only dead runs can be re-run; this one is '${existing.status}'`,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
