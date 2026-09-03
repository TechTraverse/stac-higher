/**
 * /api/processes/[id]/outputs — list + create output collections (M5-A).
 *
 * GET  — member+ of the owning group.
 * POST — operator+ who can also MANAGE the destination collection. An
 *        archived collection is refused: outputs WRITE items, and archive
 *        means the data plane is winding down.
 *
 * Cycle refusal (I-64, M5-D): attaching an output adds a
 * process → collection edge, so it is refused when that collection already
 * reaches this process through a source. The one-hop case — the same
 * collection as both source and output — is caught too: it would re-trigger
 * the process on its own output forever.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canManageCollection } from "@/lib/associations/access";
import { getCollectionSettings } from "@/lib/collections/settings";
import { formatPath, collectionNode, processNode, wouldCycle } from "@/lib/graph/edges";
import { loadGraphEdges } from "@/lib/graph/storage";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess, refuseIfExtractor } from "@/lib/processes/access";
import { processOutputCreateSchema } from "@/lib/processes/schemas";
import {
  createOutput,
  DuplicateOutputError,
  listOutputs,
} from "@/lib/processes/storage";


/**
 * Refuse a wiring that would close a loop over OUR edges (I-64, M5-D).
 *
 * Returns a 409 Response with the path, or null when the edge is safe.
 * Paths through connections or external systems are not statically decidable
 * and are deliberately out of scope — the §7 run-rate ceiling is the backstop
 * for those.
 */
async function refuseCycle(
  from: string,
  to: string,
): Promise<Response | null> {
  const path = wouldCycle(await loadGraphEdges(), from, to);
  if (!path) return null;
  return jsonResponse(409, {
    error:
      "This would create a processing loop: " +
      formatPath(path) +
      ". A process cannot consume, directly or indirectly, what it produces.",
    cycle: path,
  });
}

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
  const refused = refuseIfExtractor(loaded.process);
  if (refused) return refused;

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

    const cycle = await refuseCycle(
      processNode(loaded.process.id),
      collectionNode(collectionId),
    );
    if (cycle) return cycle;

    return jsonResponse(201, await createOutput(loaded.process.id, collectionId));
  } catch (err) {
    if (err instanceof DuplicateOutputError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
