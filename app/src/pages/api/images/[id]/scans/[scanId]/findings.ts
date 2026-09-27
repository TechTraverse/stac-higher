/**
 * GET /api/images/[id]/scans/[scanId]/findings — the full Grype JSON of a
 * scan (C-3, container-images spec §9.1 "a presigned/proxied read of
 * findings_ref"). member+: the registry and its findings are platform-wide.
 *
 * Authorize, then 302 to a short-lived presigned URL: the run-log
 * precedent (`runs/[runId]/log`). Findings can be megabytes and the app
 * never streams them.
 */
import type { APIRoute } from "astro";
import { isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { invalidImageId, requireImageRole } from "@/lib/images/access";
import { getImageScan } from "@/lib/images/storage";
import { presignGetUrl } from "@/lib/storage/presign";

export const GET: APIRoute = async ({ params, locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;
  if (!isUuid(params.id) || !isUuid(params.scanId)) return invalidImageId();
  const id = params.id.toLowerCase();
  const scanId = params.scanId.toLowerCase();

  try {
    const scan = await getImageScan(id, scanId);
    if (!scan) return jsonResponse(404, { error: "Scan not found" });
    if (!scan.findings_ref) {
      return jsonResponse(404, { error: "This scan has no stored findings" });
    }
    const url = await presignGetUrl(scan.findings_ref);
    return new Response(null, {
      status: 302,
      headers: { Location: url, "Cache-Control": "private, no-store" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
