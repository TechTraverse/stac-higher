/**
 * /api/images — the platform-wide image registry (C-3, container-images
 * spec §9.1, ADR 0021).
 *
 * GET  — member+. `?status=<image status>`, `?q=<text>` (reference:tag
 *        substring), `?in_use=true|false`. Every row carries its computed
 *        `stale` flag and `in_use_by` count.
 * POST — operator+, audited `create` on `container_image` (`image.add`).
 *        `{reference, tag?, registry_connection_id?}`. The typed reference is
 *        normalized to C-1's stored grammar, its registry host must be in the
 *        policy's `allowed_registries`, and a credential must be an ENABLED
 *        `registry` connection of one of the caller's groups for that same
 *        host. The app never touches a registry: it INSERTs a `pending` row
 *        (digest NULL) and an `admission` scan request, and answers 202 with
 *        both ids. The scanner (C-2) resolves the tag and the drain may
 *        de-duplicate by digest. The client follows the scan id.
 */
import type { APIRoute } from "astro";
import { canAccessGroup } from "@/lib/connections/access";
import { getConnection } from "@/lib/connections/storage";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, requireImageRole } from "@/lib/images/access";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { canonicalRegistryHost, normalizeImageInput } from "@/lib/images/normalize";
import {
  ImagePolicyUnavailable,
  loadImagePolicy,
  registryAllowed,
  type ImagePolicy,
} from "@/lib/images/policy";
import { registryHost } from "@/lib/images/reference";
import { imageAddSchema } from "@/lib/images/schemas";
import { IMAGE_STATUSES, type ImageStatus } from "@/lib/images/status";
import {
  findOpenAdmission,
  insertImageWithAdmission,
  listImages,
} from "@/lib/images/storage";

const Q_MAX = 200;

export const GET: APIRoute = async ({ url, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;

  const status = url.searchParams.get("status");
  if (status !== null && !(IMAGE_STATUSES as readonly string[]).includes(status)) {
    return jsonResponse(400, { error: `status must be one of ${IMAGE_STATUSES.join(", ")}` });
  }
  const inUse = url.searchParams.get("in_use");
  if (inUse !== null && inUse !== "true" && inUse !== "false") {
    return jsonResponse(400, { error: "in_use must be true or false" });
  }
  const q = url.searchParams.get("q")?.trim() ?? "";
  if (q.length > Q_MAX) {
    return jsonResponse(400, { error: `q is at most ${Q_MAX} characters` });
  }

  try {
    const view = currentImageView();
    const images = await listImages(
      {
        status: (status as ImageStatus | null) ?? undefined,
        q: q || undefined,
        inUse: inUse === null ? undefined : inUse === "true",
      },
      view,
    );
    return jsonResponse(200, { images, scan_window_days: view.scanWindowDays });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const POST: APIRoute = async ({ request, locals }) => {
  const allowed = requireImageRole(locals.auth, "operator");
  if ("response" in allowed) return allowed.response;
  const { identity } = allowed;

  const parsed = imageAddSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
  }
  const normalized = normalizeImageInput(parsed.data.reference, parsed.data.tag ?? null);
  if (!normalized.ok) {
    return jsonResponse(400, { error: normalized.error, code: "invalid_reference" });
  }

  try {
    let policy: ImagePolicy;
    try {
      policy = loadImagePolicy();
    } catch (err) {
      if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
      throw err;
    }

    const host = registryHost(normalized.reference);
    if (!registryAllowed(host, policy.allowed_registries)) {
      return jsonResponse(422, {
        error: `${host} is not an allowed registry on this deployment (allowed: ${policy.allowed_registries.join(", ")})`,
        code: "registry_not_allowed",
      });
    }

    const connectionId = parsed.data.registry_connection_id ?? null;
    if (connectionId !== null) {
      const connection = await getConnection(connectionId);
      // A credential outside the caller's groups is indistinguishable from a
      // missing one (the connections rule: group ownership scopes existence).
      if (!connection || !canAccessGroup(identity, connection.group_id)) {
        return jsonResponse(404, {
          error: "Registry connection not found",
          code: "registry_connection_not_found",
        });
      }
      if (connection.protocol !== "registry") {
        return jsonResponse(400, {
          error: `${connection.name} is a ${connection.protocol} connection; image pulls need a registry connection`,
          code: "not_a_registry_connection",
        });
      }
      // Only past the group and protocol checks: a foreign-group or
      // wrong-protocol credential must answer exactly as above, never
      // revealing whether it happens to be enabled.
      if (!connection.enabled) {
        return jsonResponse(422, {
          error: `${connection.name} is disabled; enable it before using it to add an image`,
          code: "registry_connection_disabled",
        });
      }
      const credentialHost = canonicalRegistryHost(String(connection.config.host ?? ""));
      if (credentialHost !== host) {
        return jsonResponse(422, {
          error: `${connection.name} holds credentials for ${credentialHost}, not ${host}`,
          code: "registry_connection_host_mismatch",
        });
      }
    }

    const target = {
      reference: normalized.reference,
      tag: normalized.tag,
      registryConnectionId: connectionId,
    };
    const added =
      (await findOpenAdmission(target)) ??
      (await insertImageWithAdmission({ ...target, addedBy: identity.sub }));

    locals.auditDetail = {
      reference: normalized.reference,
      tag: normalized.tag,
      registry_connection_id: connectionId,
      scan_id: added.scan_id,
      deduplicated: added.deduplicated,
    };
    // `id` is the new image, so the guard's created-id extraction audits it.
    return jsonResponse(202, {
      id: added.image_id,
      ...added,
      reference: normalized.reference,
      tag: normalized.tag,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
