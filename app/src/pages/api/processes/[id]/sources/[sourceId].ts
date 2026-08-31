/**
 * /api/processes/[id]/sources/[sourceId] — update / delete a source (M5-A).
 *
 * PUT    — operator+ of the owning group. `collection_id` is not editable:
 *          it is half the row's unique key and an edge in the M5-D cycle
 *          graph, so re-pointing a source is a delete plus a create.
 * DELETE — operator+. HARD delete: a source is configuration, not history.
 *          Runs keep their pinned revision and their `source_id` goes NULL,
 *          so removing a trigger never removes the record that it ran.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { formatPath, collectionNode, processNode, wouldCycle } from "@/lib/graph/edges";
import { loadGraphEdges } from "@/lib/graph/storage";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess } from "@/lib/processes/access";
import { processSourceUpdateSchema } from "@/lib/processes/schemas";
import { deleteSource, getSource, updateSource } from "@/lib/processes/storage";

function sourceNotFound(): Response {
  return jsonResponse(404, { error: "Process source not found" });
}

export const PUT: APIRoute = async ({ params, request, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.sourceId)) return sourceNotFound();

  try {
    const body = await request.json().catch(() => null);
    const parsed = processSourceUpdateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    // Re-enabling is a cycle hook point too (spec §8): a disabled source is
    // not an edge, so a loop can be assembled while it is off and closed by
    // flipping it back on. Only the off→on transition is checked — an edit to
    // an already-enabled source cannot add an edge that is not there.
    if (parsed.data.enabled === true) {
      const existing = await getSource(loaded.process.id, params.sourceId);
      if (existing && !existing.enabled) {
        const path = wouldCycle(
          await loadGraphEdges(),
          collectionNode(existing.collection_id),
          processNode(loaded.process.id),
        );
        if (path) {
          return jsonResponse(409, {
            error:
              "Re-enabling this source would create a processing loop: " +
              formatPath(path) +
              ". A process cannot consume, directly or indirectly, what it produces.",
            cycle: path,
          });
        }
      }
    }

    const updated = await updateSource(
      loaded.process.id,
      params.sourceId,
      parsed.data,
    );
    if (!updated) return sourceNotFound();
    return jsonResponse(200, updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;
  if (!isUuid(params.sourceId)) return sourceNotFound();

  try {
    const deleted = await deleteSource(loaded.process.id, params.sourceId);
    if (!deleted) return sourceNotFound();
    return jsonResponse(200, { deleted: true, id: params.sourceId });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
