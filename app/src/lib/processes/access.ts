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
import { getConnection } from "@/lib/connections/storage";
import type { ProcessEnv } from "./schemas";
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

/**
 * The first `secret_ref` in `env` that this process's group cannot resolve,
 * or null when every reference is in scope (M3-W-2).
 *
 * A `secret_ref` is a pointer into another row's write-only credentials, and
 * the deploy verb is the last moment the platform can check it: after this,
 * the reference lives in an immutable revision and is next read by the
 * PIPELINE, at run launch, with the master key in hand and no notion of who
 * deployed it.
 *
 * The check is against the PROCESS's group, deliberately, not the caller's.
 * An admin can deploy into any group's process; they must not thereby be able
 * to widen what that process reaches. A missing connection and a foreign one
 * are the same answer for the same reason a process outside your groups is a
 * 404: distinguishing them would make the deploy form an oracle for which
 * connection ids exist in groups you cannot see.
 */
export async function findUnresolvableSecretRef(
  env: ProcessEnv,
  groupId: string,
): Promise<string | null> {
  for (const entry of env) {
    if (!entry.secret_ref) continue;
    const connection = await getConnection(entry.secret_ref.connection_id);
    if (!connection || connection.group_id !== groupId) return entry.name;
  }
  return null;
}

/** Pinned so the route and its tests agree, and so the wording never names
 * whether the connection exists. */
export function secretRefOutOfScope(name: string): Response {
  return jsonResponse(400, {
    error:
      `${name}: secret_ref does not name a connection this process's group ` +
      "owns. A process can only reference credentials belonging to its own " +
      "group (docs/processes.md).",
  });
}
