/**
 * /api/processes/[id]/test — request a test run (M5-A, ADR 0004 bridge).
 *
 * POST — operator+ of the owning group, audited as `test` on `process`.
 *
 * The app does not execute anything: it INSERTs a `pending` row in
 * `process_checks` and hands back the id to poll, exactly like a connection
 * test. The pipeline claims the row, turns it into a flagged run on the
 * pinned revision, and writes the result columns back (M5-B/M5-C). That seam
 * is the whole point of ADR 0004 — the app never reaches into the executor.
 *
 * A process with no `current_revision` has nothing to run, so this is a 409
 * rather than a request that would sit pending forever.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { insertProcessCheck } from "@/lib/processes/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;

  const revisionId = loaded.process.current_revision;
  if (!revisionId) {
    return jsonResponse(409, {
      error: "Deploy a revision before requesting a test run",
    });
  }

  try {
    const check = await insertProcessCheck(
      loaded.process.id,
      revisionId,
      loaded.identity.sub,
    );
    return jsonResponse(202, check);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
