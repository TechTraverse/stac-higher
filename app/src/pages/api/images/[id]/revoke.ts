/**
 * POST /api/images/[id]/revoke — the terminal verb (C-3, container-images
 * spec §4.3/§4.4, §9.1). ADMIN only (checked here; the guard audits
 * `revoke` on `container_image`). Any status -> `revoked`, ending a live
 * exception. There is no DELETE in v1: immutable revisions may still
 * snapshot the id, and the pipeline dies their runs at launch (C-2).
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, imageNotFound, invalidImageId, requireImageRole } from "@/lib/images/access";
import { getImage, revokeImage } from "@/lib/images/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "admin");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return invalidImageId();
  const id = params.id.toLowerCase();

  try {
    const outcome = await revokeImage(id);
    if (outcome.outcome === "not_found") return imageNotFound();
    if (outcome.outcome === "already_revoked") {
      return jsonResponse(409, { error: "This image is already revoked", code: "image_already_revoked" });
    }
    return jsonResponse(200, { image: await getImage(id, currentImageView()) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
