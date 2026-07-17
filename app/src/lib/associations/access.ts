/**
 * Access rules for /api/collections/[id]/connections (ROADMAP §7, Phase 4).
 *
 * The middleware guard enforces ROLE (mutations need operator|admin) and writes
 * the audit rows. These helpers enforce the GROUP dimension inside the routes:
 *
 *   - An association is visible to admins, to members of its connection's
 *     owning group, and to anyone who can manage its collection.
 *   - Creating an association requires the caller to both (a) be able to USE the
 *     connection (member of its group, or admin) and (b) MANAGE the collection.
 *   - Collection management follows the sparse-settings rule (ADR 0003): an
 *     UNOWNED collection (no settings row / group_id NULL) is manageable by any
 *     operator; an OWNED collection only by its group (or admin).
 *   - A non-visible association is a 404, not a 403 — existence is group-scoped.
 */
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { getCollectionSettings } from "@/lib/collections/settings";
import { getConnection } from "@/lib/connections/storage";
import { jsonResponse } from "@/lib/http/response";
import { getAssociation } from "./storage";
import type { AssociationWithGroup } from "./storage";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Guard path params before they hit a uuid column (avoids a 500). */
export function isUuid(value: string | undefined): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

export function notFound(): Response {
  return jsonResponse(404, { error: "Association not found" });
}

/** Member+ of the group (or admin) — mirrors connections/access. */
export function canAccessGroup(
  identity: CanonicalIdentity,
  groupId: string,
): boolean {
  return isAdmin(identity) || identity.groups.includes(groupId);
}

/**
 * Whether the caller may manage a collection's data-flow settings. Unowned
 * collections (ADR 0003) are manageable by any authenticated caller with the
 * operator role; owned ones only by their group or an admin. Role is checked by
 * the guard; this is purely the group dimension.
 */
export async function canManageCollection(
  identity: CanonicalIdentity,
  collectionId: string,
): Promise<boolean> {
  const settings = await getCollectionSettings(collectionId);
  if (settings.groupId === null) return true; // unowned/public
  return canAccessGroup(identity, settings.groupId);
}

/** Visibility for an existing association: connection group OR collection. */
async function canSeeAssociation(
  identity: CanonicalIdentity,
  association: AssociationWithGroup,
): Promise<boolean> {
  if (isAdmin(identity)) return true;
  if (
    association.connectionGroupId !== null &&
    identity.groups.includes(association.connectionGroupId)
  ) {
    return true;
  }
  return canManageCollection(identity, association.collection_id);
}

/**
 * Shared route preamble for a single association: authentication, (optionally)
 * the operator role, id shape, existence, that it belongs to `collectionId`,
 * and group visibility — in that order, so the 401/403 JSON shape matches the
 * guard and non-visible rows are indistinguishable from missing ones.
 */
export async function loadVisibleAssociation(
  auth: AuthContext | undefined,
  collectionId: string | undefined,
  assocId: string | undefined,
  requireOperator: boolean,
): Promise<{ association: AssociationWithGroup } | { response: Response }> {
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
  if (!collectionId || !isUuid(assocId)) return { response: notFound() };
  const association = await getAssociation(assocId);
  if (
    !association ||
    association.collection_id !== collectionId ||
    !(await canSeeAssociation(auth.identity, association))
  ) {
    return { response: notFound() };
  }
  return { association };
}

/**
 * Resolve the connection referenced in a create body and confirm the caller may
 * use it. Returns the connection's protocol (needed to validate storage_mode)
 * or an error response. A connection the caller can't see is reported as an
 * invalid `connection_id` (400) rather than 404 — it's a body field, and the
 * form should say the reference is unusable.
 */
export async function resolveUsableConnection(
  identity: CanonicalIdentity,
  connectionId: string,
): Promise<{ protocol: string } | { response: Response }> {
  const connection = await getConnection(connectionId);
  if (!connection || !canAccessGroup(identity, connection.group_id)) {
    return {
      response: jsonResponse(400, {
        error: "connection_id must reference a connection in one of your groups",
      }),
    };
  }
  return { protocol: connection.protocol };
}
