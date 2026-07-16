/**
 * Group-ownership access rules for /api/connections (ROADMAP §7).
 *
 * The middleware guard (lib/authz) enforces ROLE (mutations need
 * operator|admin) and writes the audit rows. These helpers enforce the GROUP
 * dimension inside the routes, where the row's group_id is known:
 *
 *   - member+ of the owning group (or admin): may SEE the connection.
 *   - operator+ of the owning group (or admin): may mutate/test it.
 *   - A connection outside the caller's groups is a 404, not a 403 — group
 *     ownership also scopes existence (§7 "See group's connections").
 */
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { getConnection } from "./storage";
import type { ApiConnection } from "./storage";

export function canAccessGroup(
  identity: CanonicalIdentity,
  groupId: string,
): boolean {
  return isAdmin(identity) || identity.groups.includes(groupId);
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Guard path params before they hit a uuid column (avoids a 500). */
export function isUuid(value: string | undefined): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

// Re-exported so existing importers (`@/lib/connections/access`) keep working.
export { jsonResponse };

export function notFound(): Response {
  return jsonResponse(404, { error: "Connection not found" });
}

/**
 * Shared route preamble: authentication, (optionally) the operator role,
 * id shape, row existence, and group visibility — in that order, so the
 * 401/403 JSON shape matches the middleware guard and non-visible rows stay
 * indistinguishable from missing ones.
 */
export async function loadVisibleConnection(
  auth: AuthContext | undefined,
  id: string | undefined,
  requireOperator: boolean,
): Promise<{ connection: ApiConnection } | { response: Response }> {
  if (!auth?.authenticated) {
    return {
      response: authzError(
        401,
        "unauthenticated",
        "Authentication required for this action",
      ),
    };
  }
  if (requireOperator && !canMutate(auth.identity)) {
    return {
      response: authzError(
        403,
        "forbidden",
        "This action requires the operator or admin role",
      ),
    };
  }
  if (!isUuid(id)) return { response: notFound() };
  const connection = await getConnection(id);
  if (!connection || !canAccessGroup(auth.identity, connection.group_id)) {
    return { response: notFound() };
  }
  return { connection };
}
