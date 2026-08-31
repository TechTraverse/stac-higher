/**
 * /api/processes/[id]/outputs — list + create output collections (M5-A).
 *
 * GET  — member+ of the owning group.
 * POST — operator+ who can also MANAGE the destination collection. An
 *        archived collection is refused: outputs WRITE items, and archive
 *        means the data plane is winding down.
 *
 * NOT here yet: the M5-D cycle check (this is its second hook point, with
 * sources) and the finalize wiring that actually publishes into these
 * collections (ADR 0014).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canManageCollection } from "@/lib/associations/access";
import { getCollectionSettings } from "@/lib/collections/settings";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { processOutputCreateSchema } from "@/lib/processes/schemas";
import {
  createOutput,
  DuplicateOutputError,
  listOutputs,
} from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  try {
    return jsonResponse(200, { outputs: await listOutputs(loaded.process.id) });
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
    const parsed = processOutputCreateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const { collection_id: collectionId } = parsed.data;

    if (!(await canManageCollection(loaded.identity, collectionId))) {
      return authzError(
        403,
        "forbidden",
        "You do not have permission to manage this collection",
      );
    }
    if ((await getCollectionSettings(collectionId)).archived) {
      return jsonResponse(409, {
        error: `Collection '${collectionId}' is archived and cannot gain new data flows`,
      });
    }

    return jsonResponse(201, await createOutput(loaded.process.id, collectionId));
  } catch (err) {
    if (err instanceof DuplicateOutputError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
