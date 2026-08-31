/**
 * /api/processes/[id]/revisions — list + deploy (M5-A, spec §5.6/§10).
 *
 * GET  — member+ of the owning group. Revisions are immutable snapshots, so
 *        this is the deploy history.
 * POST — operator+; THE DEPLOY VERB, audited as `deploy` on
 *        `process_revision`. Creates a snapshot and repoints
 *        `current_revision` in one transaction.
 *
 * `runtime` is validated by the WRITE gate (`processRuntimeSchema`), which
 * refuses the `container` arm this slice — the contract carries it and the
 * pipeline parses it, but user-supplied images stay out of the first
 * accreditation scope (spec §4, ADR 0013).
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess, processNotFound } from "@/lib/processes/access";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";
import { deployRevision, listRevisions } from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  try {
    return jsonResponse(200, {
      revisions: await listRevisions(loaded.process.id),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const POST: APIRoute = async ({ params, request, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;

  try {
    const body = await request.json().catch(() => null);
    const parsed = processRevisionCreateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    const revision = await deployRevision({
      processId: loaded.process.id,
      runtime: data.runtime,
      code: data.code,
      env: data.env,
      createdBy: loaded.identity.sub,
    });
    // Null means the process vanished between the visibility check and the
    // transaction (a concurrent delete) — the same not-found either way.
    if (!revision) return processNotFound();
    return jsonResponse(201, revision);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
