/**
 * GET /api/images/[id]/scans/[scanId] — poll a scan (C-3, the test-run
 * polling pattern). member+.
 *
 * The client polls the ids the 202 handed out. The drain may de-duplicate
 * by digest (spec §9.1), re-pointing the scan at an existing image and
 * deleting the provisional one. `getImageScan` still finds the scan through
 * the old id, and the `image` returned is the one the scan now belongs to,
 * which is authoritative.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { currentImageView, invalidImageId, requireImageRole } from "@/lib/images/access";
import { getImage, getImageScan } from "@/lib/images/storage";

function scanNotFound(): Response {
  return jsonResponse(404, { error: "Scan not found" });
}

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id) || !isUuid(params.scanId)) return invalidImageId();
  const id = params.id.toLowerCase();
  const scanId = params.scanId.toLowerCase();

  try {
    const scan = await getImageScan(id, scanId);
    if (!scan) return scanNotFound();
    const image = await getImage(scan.image_id, currentImageView());
    return jsonResponse(200, { scan, image });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
