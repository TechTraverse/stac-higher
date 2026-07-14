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

export type GatedAction = "create" | "update" | "delete";

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
 * Today's gated surface is the extensions CRUD; Phase 2+ adds connections,
 * associations, and collection settings here.
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
