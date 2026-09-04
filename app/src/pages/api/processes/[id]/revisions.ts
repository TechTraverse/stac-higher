/**
 * /api/processes/[id]/revisions — list + deploy (M5-A, spec §5.6/§10).
 *
 * GET  — member+ of the owning group. Revisions are immutable snapshots, so
 *        this is the deploy history.
 * POST — operator+; THE DEPLOY VERB, audited as `deploy` on
 *        `process_revision`. Creates a snapshot and repoints
 *        `current_revision` in one transaction.
 *
 * A revision's `env` may carry `secret_ref` pointers into a connection's
 * encrypted credentials. Those are re-checked here against the PROCESS's
 * group (`findUnresolvableSecretRef`) — the schema can only validate the
 * shape, and after this the reference is immutable and is next read by the
 * pipeline at run launch.
 *
 * `runtime` is validated by the WRITE gate (`processRuntimeSchema`), which
 * refuses the `container` arm this slice — the contract carries it and the
 * pipeline parses it, but user-supplied images stay out of the first
 * accreditation scope (spec §4, ADR 0013). The same gate accepts only
 * `network.level: isolated` this slice; the deployment cap
 * (`PROCESS_NETWORK_MAX`, GOES spec §4) is checked here as well so that once
 * the gate opens, a level above the cap is still refused at the form.
 * `runtime.runtime_image` (X-queue spec §8) is an alias of a PLATFORM image,
 * validated by the schema's enum — the same set the pipeline resolves at
 * launch, where an alias it has no image for dies with a reason.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import {
  findUnresolvableSecretRef,
  loadVisibleProcess,
  processNotFound,
  secretRefOutOfScope,
} from "@/lib/processes/access";
import {
  NETWORK_CAP_MESSAGE,
  getNetworkMax,
  networkLevelWithinCap,
} from "@/lib/processes/network";
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

    // GOES spec §4: the deployment maximum, enforced independently by the
    // pipeline at launch; checked here so the refusal lands at deploy time.
    const cap = getNetworkMax();
    if (!networkLevelWithinCap(data.runtime.network.level, cap)) {
      return jsonResponse(400, {
        error: NETWORK_CAP_MESSAGE(data.runtime.network.level, cap),
      });
    }

    const unresolvable = await findUnresolvableSecretRef(
      data.env,
      loaded.process.group_id,
    );
    if (unresolvable) return secretRefOutOfScope(unresolvable);

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
