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
import { loadHardwareProfiles, publicProfiles } from "@/lib/processes/hardware";

/** K-5 replaces the constant with `PROCESS_EXECUTOR`. */
const EXECUTOR_BACKEND = "docker";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required to list hardware profiles");
  }
  try {
    return jsonResponse(200, { backend: EXECUTOR_BACKEND, profiles: publicProfiles(loadHardwareProfiles()) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
