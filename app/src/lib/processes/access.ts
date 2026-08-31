/**
 * Group-ownership access rules for /api/processes (ROADMAP §7, spec §10).
 *
 * Same split as connections: the middleware guard enforces ROLE (mutations
 * need operator|admin) and writes the audit rows; these helpers enforce the
 * GROUP dimension inside the routes, where the row's group_id is known.
 *
 *   - member+ of the owning group (or admin): may SEE the process.
 *   - operator+ of the owning group (or admin): may mutate, deploy, test it.
 *   - A process outside the caller's groups is a 404, not a 403 — group
 *     ownership scopes existence, not just permission.
 */
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canAccessGroup, isUuid } from "@/lib/connections/access";
import { jsonResponse } from "@/lib/http/response";
import { getProcess, type ApiProcess } from "./storage";

export function processNotFound(): Response {
  return jsonResponse(404, { error: "Process not found" });
}

/**
 * Shared route preamble: authentication, (optionally) the operator role, id
 * shape, row existence, and group visibility — in that order, so the 401/403
 * JSON shape matches the middleware guard and a non-visible row stays
 * indistinguishable from a missing one.
 */
export async function loadVisibleProcess(
  auth: AuthContext | undefined,
  id: string | undefined,
  requireOperator: boolean,
): Promise<
  { process: ApiProcess; identity: CanonicalIdentity } | { response: Response }
> {
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
  if (!isUuid(id)) return { response: processNotFound() };
  const process = await getProcess(id);
  if (!process || !canAccessGroup(auth.identity, process.group_id)) {
    return { response: processNotFound() };
  }
  // The identity comes back with the row: this preamble already PROVED the
  // caller is authenticated, so routes should not have to re-narrow
  // `locals.auth` with a non-null assertion to use it.
  return { process, identity: auth.identity };
}
