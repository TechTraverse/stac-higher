/**
 * GET /api/processes/image-policy — the deployment's image policy for the
 * UI (C-3, container-images spec §9.1). member+. The UI reads it to explain
 * a verdict and to say which registries an image may come from. The
 * scanner's `scan_limits` are pipeline-only and are left out.
 * Missing/invalid policy -> 503 (the loaders fail closed).
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { requireImageRole } from "@/lib/images/access";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy } from "@/lib/images/policy";
import type { PublicImagePolicy } from "@/lib/images/types";

export const GET: APIRoute = async ({ locals }) => {
  const allowed = requireImageRole(locals.auth, "member");
  if ("response" in allowed) return allowed.response;

  try {
    const policy = loadImagePolicy();
    const visible: PublicImagePolicy = {
      version: policy.version,
      allowed_registries: policy.allowed_registries,
      platform: policy.platform,
      max_image_size_mb: policy.max_image_size_mb,
      block: policy.block,
      scan_window_days: policy.scan_window_days,
      rescan_interval_hours: policy.rescan_interval_hours,
      exception_max_days: policy.exception_max_days,
    };
    return jsonResponse(200, visible);
  } catch (err) {
    if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
