/**
 * POST /api/images/[id]/rescan — "Rescan now" (C-3, container-images spec
 * §9.1). operator+, audited `rescan` on `container_image`.
 *
 * INSERTs an `image_scans` request row for the pipeline to drain (ADR 0004).
 * An image that never scanned successfully gets an ADMISSION retry instead
 * of a rescan (a rescan re-matches the stored SBOM, and there is none). One
 * open scan per image (409 `scan_pending`, naming it); revoked is terminal
 * (409 `image_revoked`).
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { imageNotFound, invalidImageId, requireImageRole } from "@/lib/images/access";
import { requestImageScan } from "@/lib/images/storage";

export const POST: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "operator");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id)) return invalidImageId();
  const id = params.id.toLowerCase();

  try {
    const outcome = await requestImageScan(id, allowed.identity.sub);
    switch (outcome.outcome) {
      case "not_found":
        return imageNotFound();
      case "revoked":
        return jsonResponse(409, {
          error: "This image is revoked; add the reference again to scan it as a new image",
          code: "image_revoked",
        });
      case "already_pending":
        return jsonResponse(409, {
          error: "A scan of this image is already queued or running",
          code: "scan_pending",
          scan_id: outcome.scan_id,
        });
      case "requested":
        locals.auditDetail = { scan_id: outcome.scan_id, kind: outcome.kind };
        return jsonResponse(202, {
          image_id: id,
          scan_id: outcome.scan_id,
          kind: outcome.kind,
        });
      default:
        return imageNotFound();
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
