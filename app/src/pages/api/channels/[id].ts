/**
 * /api/channels/[id] — get / update / delete one notification channel
 * (M2-C, spec §4, ADR 0010).
 *
 * GET    — member+ of the owning group (or admin). Secret stays redacted.
 * PUT    — operator+ of the owning group; replaces `config` WHOLESALE (the
 *          connections credential rule: dropping the secret = PUT without it).
 *          kind and group are immutable — delete + recreate to change them.
 * DELETE — operator+ of the owning group. Pending webhook deliveries and
 *          channel-anchored alerts cascade away with the row.
 *
 * Out-of-group rows 404 (existence is group-scoped, like connections).
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canAccessGroup, isUuid, jsonResponse } from "@/lib/connections/access";
import type { AuthContext } from "@/lib/auth/types";
import {
  inAppChannelConfigSchema,
  parseChannelUpdate,
  webhookChannelConfigSchema,
} from "@/lib/notifications/schemas";
import {
  deleteChannel,
  getChannel,
  updateChannelConfig,
  type ApiChannel,
} from "@/lib/notifications/storage";

async function loadVisibleChannel(
  auth: AuthContext | undefined,
  id: string | undefined,
  requireOperator: boolean,
): Promise<{ channel: ApiChannel } | { response: Response }> {
  if (!auth?.authenticated) {
    return {
      response: authzError(
        401,
        "unauthenticated",
        "Authentication required for this action",
      ),
    };
  }
  if (requireOperator && !canMutate(auth.identity)) {
    return {
      response: authzError(
        403,
        "forbidden",
        "This action requires the operator or admin role",
      ),
    };
  }
  if (!isUuid(id)) {
    return { response: jsonResponse(404, { error: "Channel not found" }) };
  }
  const channel = await getChannel(id);
  if (!channel || !canAccessGroup(auth.identity, channel.group_id)) {
    return { response: jsonResponse(404, { error: "Channel not found" }) };
  }
  return { channel };
}

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleChannel(locals.auth, params.id, false);
    if ("response" in loaded) return loaded.response;
    return jsonResponse(200, loaded.channel);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
  try {
    const loaded = await loadVisibleChannel(locals.auth, params.id, true);
    if ("response" in loaded) return loaded.response;

    const body = await request.json().catch(() => null);
    const parsed = parseChannelUpdate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    // The union above cannot see the row's kind — re-validate against it so a
    // webhook channel cannot be stripped to {} (and in_app gains no url).
    const kindSchema =
      loaded.channel.kind === "webhook"
        ? webhookChannelConfigSchema
        : inAppChannelConfigSchema;
    const config = kindSchema.safeParse(parsed.data.config);
    if (!config.success) {
      return jsonResponse(400, {
        error: `Validation failed for a ${loaded.channel.kind} channel config`,
        details: config.error.issues,
      });
    }

    const updated = await updateChannelConfig(loaded.channel.id, config.data);
    if (!updated) {
      return jsonResponse(404, { error: "Channel not found" });
    }
    return jsonResponse(200, updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleChannel(locals.auth, params.id, true);
    if ("response" in loaded) return loaded.response;
    const deleted = await deleteChannel(loaded.channel.id);
    if (!deleted) {
      return jsonResponse(404, { error: "Channel not found" });
    }
    return jsonResponse(200, { deleted: true, id: loaded.channel.id });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
