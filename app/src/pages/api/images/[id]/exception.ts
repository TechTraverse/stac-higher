/**
 * POST /api/images/[id]/exception — an admin's expiring exception (C-3,
 * container-images spec §4.4, §9.1). ADMIN only (checked here; the guard
 * gates operator+ and writes the audit row `exception` on
 * `container_image`, carrying the reason and expiry via `locals.auditDetail`).
 *
 * `{reason, expires_at}`: `expires_at` must be in the future and at most the
 * policy's `exception_max_days` away. A `rejected` or `flagged` image takes
 * an exception (-> `approved`); on an `approved` image that carries one, the
 * grant replaces it (C-4, spec §4.4). An exception never covers staleness
 * (spec §4.3); the gate still refuses a stale image.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { imageNotFound, invalidImageId, requireImageRole } from "@/lib/images/access";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy, type ImagePolicy } from "@/lib/images/policy";
import { imageExceptionSchema } from "@/lib/images/schemas";
import { getImage, grantImageException } from "@/lib/images/storage";

const DAY_MS = 86_400_000;

export const POST: APIRoute = async ({ params, request, locals }) => {
  const allowed = requireImageRole(locals.auth, "admin");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return invalidImageId();
  const id = params.id.toLowerCase();

  const parsed = imageExceptionSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
  }

  try {
    let policy: ImagePolicy;
    try {
      policy = loadImagePolicy();
    } catch (err) {
      if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
      throw err;
    }

    const now = new Date();
    const expiresAt = new Date(parsed.data.expires_at);
    if (expiresAt.getTime() <= now.getTime()) {
      return jsonResponse(400, {
        error: "expires_at must be in the future",
        code: "exception_expiry_invalid",
      });
    }
    if (expiresAt.getTime() - now.getTime() > policy.exception_max_days * DAY_MS) {
      return jsonResponse(422, {
        error: `An exception lasts at most ${policy.exception_max_days} days on this deployment (policy exception_max_days)`,
        code: "exception_too_long",
      });
    }

    const outcome = await grantImageException({
      imageId: id,
      reason: parsed.data.reason,
      by: allowed.identity.sub,
      expiresAt,
    });
    if (outcome.outcome === "not_found") return imageNotFound();
    if (outcome.outcome === "wrong_status") {
      return jsonResponse(409, {
        error: `An exception applies to a rejected or flagged image, or replaces the exception on an approved image that carries one; this one is ${outcome.status}`,
        code: "image_not_exceptionable",
      });
    }
    locals.auditDetail = { reason: parsed.data.reason, expires_at: expiresAt.toISOString() };
    const image = await getImage(id, { now, scanWindowDays: policy.scan_window_days });
    return jsonResponse(200, { image });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
