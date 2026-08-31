/**
 * /api/processes/[id]/sources — list + create trigger sources (M5-A).
 *
 * GET  — member+ of the owning group; includes the pipeline-written
 *        `flow_stats` rollup the UI reads.
 * POST — operator+ who can also MANAGE the source collection (the
 *        association precedent: wiring a flow needs rights on both ends).
 *        Archived collections are refused, matching M2-F.
 *
 * NOT here yet: the M5-D cycle check. Creating or re-enabling a source is one
 * of its two hook points (the other is outputs) — it runs a DFS over the
 * shared {ingest, deliver, process_source, process_output} edge model and
 * 409s with the path. Until then the §7 rate ceiling is the backstop.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canManageCollection } from "@/lib/associations/access";
import { getCollectionSettings } from "@/lib/collections/settings";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { processSourceCreateSchema } from "@/lib/processes/schemas";
import {
  createSource,
  DuplicateSourceError,
  listSources,
} from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  try {
    return jsonResponse(200, { sources: await listSources(loaded.process.id) });
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
    const parsed = processSourceCreateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    if (!(await canManageCollection(loaded.identity, data.collection_id))) {
      return authzError(
        403,
        "forbidden",
        "You do not have permission to manage this collection",
      );
    }
    // ADR 0009/0011: an archived collection is winding down, not gaining flows.
    if ((await getCollectionSettings(data.collection_id)).archived) {
      return jsonResponse(409, {
        error: `Collection '${data.collection_id}' is archived and cannot gain new data flows`,
      });
    }

    const source = await createSource({
      processId: loaded.process.id,
      collectionId: data.collection_id,
      trigger: data.trigger,
      expectation: data.expectation,
      enabled: data.enabled,
    });
    return jsonResponse(201, source);
  } catch (err) {
    if (err instanceof DuplicateSourceError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
