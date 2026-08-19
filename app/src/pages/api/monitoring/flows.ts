/**
 * GET /api/monitoring/flows — cross-collection flow telemetry (M2-D, spec §7).
 *
 * Every association visible to the caller (member+: connections in their
 * groups; admin: all) with its pipeline-written `flow_stats` and declared
 * expectation — the /monitoring page's per-association flow view. Read-only;
 * mutations stay on the per-collection association routes.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/connections/access";
import {
  listAssociationsForGroups,
  toApiAssociation,
} from "@/lib/associations/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to view monitoring data",
    );
  }
  try {
    const rows = await listAssociationsForGroups(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, { flows: rows.map(toApiAssociation) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
