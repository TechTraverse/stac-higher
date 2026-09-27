/**
 * GET /api/internal/images/approved?digest=sha256:… — `{approved: bool}` for
 * a cluster admission policy (C-3 builds it, K-6 wires it: container-images
 * spec §9.1, §12). Defence in depth behind the deploy gate and the
 * pipeline's launch check.
 *
 * "Approved" here means "could LAUNCH": a row with that digest is `approved`
 * or `flagged`, was scanned inside the policy window, and — if it carries an
 * exception — that exception has not expired (spec §4.3, §4.4). Flagged
 * blocks deploys, not runs, and the admission policy guards runs.
 *
 * No session: the caller is the cluster, not a person. FAILS CLOSED
 * (controller ruling F3): when `INTERNAL_API_TOKEN` is unset or empty, this
 * route answers 404, as if it did not exist — a deployment must configure
 * the token before this seam is reachable at all. When the token is set, a
 * request must carry `X-Internal-Token` equal to it, compared in constant
 * time; missing, wrong, or a different length is 401. Missing/invalid
 * policy -> 503, never a silent "approved".
 */
import { timingSafeEqual } from "node:crypto";
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import { imagePolicyUnavailable } from "@/lib/images/gate";
import { ImagePolicyUnavailable, loadImagePolicy } from "@/lib/images/policy";
import { isImageDigest } from "@/lib/images/reference";
import { isDigestLaunchable } from "@/lib/images/storage";

function tokenMatches(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export const GET: APIRoute = async ({ url, request }) => {
  const expected = process.env.INTERNAL_API_TOKEN?.trim();
  if (!expected) {
    return jsonResponse(404, { error: "Not found" });
  }
  if (!tokenMatches(request.headers.get("x-internal-token") ?? "", expected)) {
    return jsonResponse(401, { error: "internal token required", code: "unauthenticated" });
  }
  const digest = url.searchParams.get("digest") ?? "";
  if (!isImageDigest(digest)) {
    return jsonResponse(400, { error: "digest must be sha256:<64 lowercase hex>" });
  }
  try {
    const policy = loadImagePolicy();
    return jsonResponse(200, {
      approved: await isDigestLaunchable(digest, policy.scan_window_days),
    });
  } catch (err) {
    if (err instanceof ImagePolicyUnavailable) return imagePolicyUnavailable(err);
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
