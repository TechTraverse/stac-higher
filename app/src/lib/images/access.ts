/**
 * The /api/images route preamble (C-3, container-images spec §9.1).
 *
 * The registry is PLATFORM-WIDE (spec decision 7): every authenticated
 * member sees every image, so there is no group-scoped 404 on an image the
 * way there is on a process or a connection. Group ownership applies to the
 * registry CREDENTIAL (checked on add, and by the deploy gate) and to the
 * processes named on the detail page. The guard enforces operator+ on the
 * mutation routes and writes their audit rows. These helpers repeat the
 * role check so a route is safe on its own (and unit-testable), and add the
 * ADMIN check the guard cannot express.
 */
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { loadImagePolicy } from "./policy";
import type { ImageView } from "./storage";

export type ImageRole = "member" | "operator" | "admin";

export function requireImageRole(
  auth: AuthContext | undefined,
  role: ImageRole,
): { identity: CanonicalIdentity } | { response: Response } {
  if (!auth?.authenticated) {
    return {
      response: authzError(401, "unauthenticated", "Authentication required for this action"),
    };
  }
  if (role === "operator" && !canMutate(auth.identity)) {
    return {
      response: authzError(403, "forbidden", "This action requires the operator or admin role"),
    };
  }
  if (role === "admin" && !isAdmin(auth.identity)) {
    return { response: authzError(403, "forbidden", "This action requires the admin role") };
  }
  return { identity: auth.identity };
}

export function imageNotFound(): Response {
  return jsonResponse(404, { error: "Image not found" });
}

/**
 * A malformed image or scan id in the path. Every route that takes one
 * validates it as a UUID BEFORE handing it to storage — an id column cast
 * (`WHERE id = $1`) throws on a non-UUID string, which would otherwise
 * surface as a 500 for a typo or a probing request instead of a clean 400.
 */
export function invalidImageId(): Response {
  return jsonResponse(400, { error: "id must be a UUID", code: "invalid_id" });
}

/** The policy-derived view a read needs. A broken policy must not take the
 * dashboard down: staleness becomes unknown (`null`) and the page says why,
 * while every WRITE that needs the policy still fails closed (503). */
export function currentImageView(now: Date = new Date()): ImageView {
  try {
    return { now, scanWindowDays: loadImagePolicy().scan_window_days };
  } catch {
    return { now, scanWindowDays: null };
  }
}
