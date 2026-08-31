/**
 * /api/processes — list + create (Phase 9 / M5-A, spec §10).
 *
 * GET  — any authenticated user (member+). Members/operators see their own
 *        groups' processes; admins see all.
 * POST — operator|admin (role by the middleware guard AND here, defense in
 *        depth). `group_id` must be one of the caller's groups unless admin.
 *        A process is created WITHOUT a revision: deploying one is a separate
 *        audited verb, so "exists" and "has something to run" stay distinct.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { canAccessGroup } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { processCreateSchema } from "@/lib/processes/schemas";
import {
  createProcess,
  DuplicateProcessNameError,
  listProcesses,
} from "@/lib/processes/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list processes",
    );
  }
  try {
    const processes = await listProcesses(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, { processes });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const POST: APIRoute = async ({ request, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required for this action",
    );
  }
  if (!canMutate(auth.identity)) {
    return authzError(
      403,
      "forbidden",
      "This action requires the operator or admin role",
    );
  }

  try {
    const body = await request.json().catch(() => null);
    const parsed = processCreateSchema.safeParse(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    // §7: operators create only in their own groups.
    if (!canAccessGroup(auth.identity, data.group_id)) {
      return authzError(403, "forbidden", "group_id must be one of your groups");
    }

    const process = await createProcess({
      name: data.name,
      description: data.description,
      groupId: data.group_id,
      enabled: data.enabled,
      maxRunsPerHour: data.max_runs_per_hour,
      createdBy: auth.identity.sub,
    });
    return jsonResponse(201, process);
  } catch (err) {
    if (err instanceof DuplicateProcessNameError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
