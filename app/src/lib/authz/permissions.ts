/**
 * Capability checks + the gated-route policy table (ROADMAP §7).
 *
 * Capabilities derive from the canonical identity (`locals.auth`) produced by
 * the claims-mapping layer — this module never reads tokens or cookies.
 *
 * §7 capability table, as it applies to today's Astro API surface:
 *   - Reads stay open (anonymous catalog/extension reads keep working;
 *     DEFAULT_PUBLIC stays true this phase — visibility is additive later).
 *   - Mutations require operator | admin.
 *   - Audit log viewing: operator (own groups) | admin (all) — enforced in
 *     the /api/audit route, not here, because it is a filtered read.
 */
import type { CanonicalIdentity, CanonicalRole } from "@/lib/auth/types";

export const MUTATION_ROLES: readonly CanonicalRole[] = ["operator", "admin"];

export function hasAnyRole(
  identity: CanonicalIdentity,
  roles: readonly CanonicalRole[],
): boolean {
  return identity.roles.some((role) => roles.includes(role));
}

/** §7: create/edit/delete resources requires operator or admin. */
export function canMutate(identity: CanonicalIdentity): boolean {
  return hasAnyRole(identity, MUTATION_ROLES);
}

export function isAdmin(identity: CanonicalIdentity): boolean {
  return identity.roles.includes("admin");
}

/** `test` = test-connection (ROADMAP §5 audit action enum). */
export type GatedAction = "create" | "update" | "delete" | "test";

export interface GatedRouteMatch {
  action: GatedAction;
  resourceType: string;
  /** Known for update/delete (from the path); null for create. */
  resourceId: string | null;
}

/** Sub-paths of /api/extensions that are utilities, not extension ids. */
const EXTENSION_UTILITY_PATHS = new Set([
  "import",
  "preview",
  "resolve-schema",
]);

/**
 * Match a request against the gated mutation table. Returns null for
 * everything that stays open (all reads, and the read-shaped POST utilities
 * `preview` / `resolve-schema`, which persist no user data beyond a TTL
 * cache).
 *
 * Today's gated surface is the extensions CRUD and the connections CRUD
 * (Phase 2); Phase 2+ adds associations and collection settings here.
 *
 * Note on connections: this table gates by ROLE (operator|admin). GROUP
 * ownership (§7: operators act only within their own groups) needs the row's
 * group_id, so it is enforced inside the /api/connections routes — the guard
 * cannot see the DB. The route-level denial still lands in the guard's audit
 * row via the response status.
 */
export function matchGatedRoute(
  method: string,
  pathname: string,
): GatedRouteMatch | null {
  const m = method.toUpperCase();
  if (m !== "POST" && m !== "PUT" && m !== "DELETE" && m !== "PATCH") {
    return null;
  }
  const path = pathname.replace(/\/+$/, "") || "/";

  if (m === "POST" && path === "/api/extensions") {
    return { action: "create", resourceType: "extension", resourceId: null };
  }
  if (m === "POST" && path === "/api/extensions/import") {
    return { action: "create", resourceType: "extension", resourceId: null };
  }

  if (m === "POST" && path === "/api/connections") {
    return { action: "create", resourceType: "connection", resourceId: null };
  }

  // Phase 3: minting presigned upload URLs is a gated mutation (operator+),
  // audited by the guard. The resource is the target collection, not a row id.
  if (m === "POST" && path === "/api/uploads") {
    return { action: "create", resourceType: "upload", resourceId: null };
  }

  // Phase 4: collection↔connection ingest associations. Create is audited with
  // the new association id (extracted from the 201 body); update/delete carry
  // the association id from the path. Group ownership is enforced in-route.
  const collConnCreate = path.match(/^\/api\/collections\/([^/]+)\/connections$/);
  if (m === "POST" && collConnCreate) {
    return {
      action: "create",
      resourceType: "collection_connection",
      resourceId: null,
    };
  }
  const collConnId = path.match(
    /^\/api\/collections\/([^/]+)\/connections\/([^/]+)$/,
  );
  if (collConnId) {
    if (m === "PUT" || m === "PATCH") {
      return {
        action: "update",
        resourceType: "collection_connection",
        resourceId: collConnId[2],
      };
    }
    if (m === "DELETE") {
      return {
        action: "delete",
        resourceType: "collection_connection",
        resourceId: collConnId[2],
      };
    }
  }
  const connTest = path.match(/^\/api\/connections\/([^/]+)\/test$/);
  if (m === "POST" && connTest) {
    return { action: "test", resourceType: "connection", resourceId: connTest[1] };
  }
  const connHostKeyReset = path.match(
    /^\/api\/connections\/([^/]+)\/host-key\/reset$/,
  );
  if (m === "POST" && connHostKeyReset) {
    // Modeled as an update of the connection (clears the TOFU pin); the
    // request path in the audit detail distinguishes it from a config edit.
    return {
      action: "update",
      resourceType: "connection",
      resourceId: connHostKeyReset[1],
    };
  }
  const connId = path.match(/^\/api\/connections\/([^/]+)$/);
  if (connId) {
    if (m === "PUT" || m === "PATCH") {
      return { action: "update", resourceType: "connection", resourceId: connId[1] };
    }
    if (m === "DELETE") {
      return { action: "delete", resourceType: "connection", resourceId: connId[1] };
    }
  }

  const idMatch = path.match(/^\/api\/extensions\/([^/]+)$/);
  if (idMatch && !EXTENSION_UTILITY_PATHS.has(idMatch[1])) {
    if (m === "PUT" || m === "PATCH") {
      return {
        action: "update",
        resourceType: "extension",
        resourceId: idMatch[1],
      };
    }
    if (m === "DELETE") {
      return {
        action: "delete",
        resourceType: "extension",
        resourceId: idMatch[1],
      };
    }
  }

  return null;
}
