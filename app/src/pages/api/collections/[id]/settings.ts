/**
 * /api/collections/[id]/settings — collection platform settings (M2-E, §7).
 *
 * GET — any authenticated user (member+): ownership, exposure, retention,
 *       archived. Sparse-table defaults applied on read (ADR 0003).
 * PUT — operator+ (role via the middleware guard AND here), group rule via
 *       `canManageCollection`: unowned collections are manageable by any
 *       operator (ADR 0003); owned ones by their group or an admin. Setting
 *       group_id transfers ownership — the TARGET group must also be one of
 *       the caller's (admins excepted), so an operator cannot hand a
 *       collection to a group they are not in. Full-document write, audited
 *       by the guard as `collection_settings`.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canAccessGroup, canManageCollection } from "@/lib/associations/access";
import { jsonResponse } from "@/lib/connections/access";
import {
  getCollectionSettings,
  upsertCollectionSettings,
} from "@/lib/collections/settings";
import { parseCollectionSettingsUpdate } from "@/lib/collections/settings-schemas";

export const GET: APIRoute = async ({ params, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to read collection settings",
    );
  }
  const collectionId = params.id;
  if (!collectionId) {
    return jsonResponse(404, { error: "Collection not found" });
  }
  try {
    const settings = await getCollectionSettings(collectionId);
    return jsonResponse(200, settings);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
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
  const collectionId = params.id;
  if (!collectionId) {
    return jsonResponse(404, { error: "Collection not found" });
  }

  try {
    const body = await request.json().catch(() => null);
    const parsed = parseCollectionSettingsUpdate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    // Current-owner rule: only the owning group (or any operator when
    // unowned, or an admin) may change the settings at all.
    if (!(await canManageCollection(auth.identity, collectionId))) {
      return authzError(
        403,
        "forbidden",
        "This collection belongs to another group",
      );
    }
    // Target-owner rule: transfers must land in one of the caller's groups.
    if (
      data.group_id !== null &&
      !canAccessGroup(auth.identity, data.group_id)
    ) {
      return authzError(403, "forbidden", "group_id must be one of your groups");
    }

    const settings = await upsertCollectionSettings(collectionId, {
      groupId: data.group_id,
      externallyWritable: data.externally_writable,
      retentionDays: data.retention_days,
      gcGraceDays: data.gc_grace_days,
      archived: data.archived,
    });
    return jsonResponse(200, settings);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
