/**
 * /api/processes/[id] — get / update / soft-delete (M5-A, spec §10).
 *
 * GET    — member+ of the owning group (admin sees all).
 * PUT    — operator+ of the owning group; audited by the guard. Cannot move
 *          `current_revision`: only a deploy does that, so an ordinary edit
 *          can never silently change what executes.
 * DELETE — operator+; SOFT delete per ADR 0009, so run history survives and
 *          the name frees up for re-use.
 */
import type { APIRoute } from "astro";
import { countAssociationsUsingExtractor } from "@/lib/associations/storage";
import { authzError } from "@/lib/authz/guard";
import { canAccessGroup } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { loadVisibleProcess, processNotFound } from "@/lib/processes/access";
import { processUpdateSchema } from "@/lib/processes/schemas";
import {
  DuplicateProcessNameError,
  softDeleteProcess,
  updateProcess,
} from "@/lib/processes/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, false);
  if ("response" in loaded) return loaded.response;
  return jsonResponse(200, loaded.process);
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;

  try {
    const body = await request.json().catch(() => null);
    const parsed = processUpdateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    // Re-homing a process needs rights on the DESTINATION group too —
    // otherwise an operator could move a process somewhere they cannot manage
    // (or, worse, out of their own reach).
    if (
      data.group_id !== undefined &&
      !canAccessGroup(loaded.identity, data.group_id)
    ) {
      return authzError(403, "forbidden", "group_id must be one of your groups");
    }

    const updated = await updateProcess(loaded.process.id, {
      name: data.name,
      description: data.description,
      groupId: data.group_id,
      enabled: data.enabled,
      maxRunsPerHour: data.max_runs_per_hour,
    });
    if (!updated) return processNotFound();
    return jsonResponse(200, updated);
  } catch (err) {
    if (err instanceof DuplicateProcessNameError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  const loaded = await loadVisibleProcess(locals.auth, params.id, true);
  if ("response" in loaded) return loaded.response;

  try {
    if (loaded.process.kind === "extractor") {
      const users = await countAssociationsUsingExtractor(loaded.process.id);
      if (users > 0) {
        return jsonResponse(409, {
          error:
            `This extractor is named by ${users} ingest association${users === 1 ? "" : "s"}. ` +
            "Switch those associations to another metadata strategy first.",
        });
      }
    }
    const deleted = await softDeleteProcess(loaded.process.id);
    if (!deleted) return processNotFound();
    return jsonResponse(200, { deleted: true, id: loaded.process.id });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
