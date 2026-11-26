/**
 * GET /api/processes/hardware-profiles — the deployment's hardware profiles
 * for the picker (K-1, spec §3.3): member+, each profile minus its
 * pipeline-only `backend` block, plus which executor backend runs them so the
 * UI can word its wait states. Reads are ungated by the route guard; the
 * in-route check is the whole gate.
 */
import type { APIRoute } from "astro";

import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/http/response";
import { getExecutorBackend } from "@/lib/processes/executor";
import { loadHardwareProfiles, publicProfiles } from "@/lib/processes/hardware";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required to list hardware profiles");
  }
  try {
    // K-5: read per request, so the answer is the deployment's PROCESS_EXECUTOR.
    return jsonResponse(200, {
      backend: getExecutorBackend(),
      profiles: publicProfiles(loadHardwareProfiles()),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
