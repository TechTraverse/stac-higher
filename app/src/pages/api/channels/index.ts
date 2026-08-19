/**
 * /api/channels — notification channel list + create (M2-C, spec §4, ADR 0010).
 *
 * GET  — any authenticated user (member+). Members/operators see their own
 *        groups' channels; admins see all. The webhook signing secret is
 *        write-only — responses carry `has_secret`, never the value.
 * POST — operator|admin (role enforced by the middleware guard AND here);
 *        group_id must be one of the caller's groups unless admin.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { canAccessGroup, jsonResponse } from "@/lib/connections/access";
import { parseChannelCreate } from "@/lib/notifications/schemas";
import { createChannel, listChannels } from "@/lib/notifications/storage";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list notification channels",
    );
  }
  try {
    const channels = await listChannels(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, { channels });
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
    const parsed = parseChannelCreate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    // §7: operators configure channels only for their own groups.
    if (!canAccessGroup(auth.identity, data.group_id)) {
      return authzError(403, "forbidden", "group_id must be one of your groups");
    }

    const channel = await createChannel({
      groupId: data.group_id,
      kind: data.kind,
      config: data.config,
      createdBy: auth.identity.sub,
    });
    return jsonResponse(201, channel);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
