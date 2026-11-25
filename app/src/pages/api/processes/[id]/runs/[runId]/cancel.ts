/**
 * /api/processes/[id]/runs/[runId]/cancel — stop a run (K-4, spec §5.3;
 * closes I-81 and ADR 0016's `dismiss` gap).
 *
 * POST — operator+ of the owning group, audited as `cancel` on the process
 *        (the run id stays in the audited path, the `rerun` precedent).
 *
 * Two outcomes, both 202 `{ run, outcome }`:
 * - `cancelled`: a `queued` or retry-pending `failed` run flipped to
 *   `cancelled` at once — nothing had been submitted.
 * - `cancel_requested`: a `running` run is flagged and the pipeline is
 *   NOTIFYed in the same statement. The app never touches the executor
 *   (ADR 0004): the pipeline's cancel listener stops the run within seconds
 *   (or the next run tick, within a minute), keeps its log and lands it
 *   `cancelled`. A cancel wins even over a run that exits in the meantime.
 *
 * Anything else is 409 `run_not_cancellable` naming the status; an unknown
 * run, or one outside the caller's groups, is 404. Extractor runs are
 * cancellable (their ingest rows are released by the ingest stall sweep);
 * re-run stays refused for them.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { cancelRun, getRun } from "@/lib/processes/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.runId)) {
    return jsonResponse(404, { error: "Run not found" });
  }

  try {
    const cancelled = await cancelRun(loaded.process.id, params.runId);
    if (cancelled) return jsonResponse(202, cancelled);

    // The conditional statement matched nothing: say WHICH reason.
    const existing = await getRun(loaded.process.id, params.runId);
    if (!existing) return jsonResponse(404, { error: "Run not found" });
    return jsonResponse(409, {
      error: `Only queued, retrying or running runs can be cancelled; this one is '${existing.status}'`,
      code: "run_not_cancellable",
    });
  } catch (err) {
    // Never the raw database text (#74): log it, answer generically.
    console.error(err instanceof Error ? err.message : "Unknown error");
    return jsonResponse(500, { error: "internal error" });
  }
};
