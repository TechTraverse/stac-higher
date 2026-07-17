/**
 * /api/collections/[id]/connections — list + create ingest associations
 * (ROADMAP §5.1, §6.1, §7 — Phase 4 ingest slice). `[id]` is the collection id.
 *
 * GET  — any authenticated user who can SEE the collection's associations
 *        (member of an association's connection group, collection manager, or
 *        admin). flow_stats is included; no connection secrets are ever present.
 * POST — operator|admin (role by the guard AND here), who can USE the referenced
 *        connection and MANAGE the collection. `direction` is 'ingest' only this
 *        phase; `storage_mode: reference` is restricted to object-store (s3)
 *        connections (§5.1). A duplicate (collection, connection, direction) is
 *        a 409.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import {
  canManageCollection,
  resolveUsableConnection,
} from "@/lib/associations/access";
import { parseAssociationCreate } from "@/lib/associations/schemas";
import {
  createAssociation,
  DuplicateAssociationError,
  listAssociations,
  toApiAssociation,
} from "@/lib/associations/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list associations",
    );
  }
  const collectionId = params.id;
  if (!collectionId) return jsonResponse(404, { error: "Collection not found" });

  try {
    const rows = await listAssociations(collectionId);
    // Reads are scoped by visibility: admins see all; others see associations
    // whose connection is in one of their groups, or (if they can manage the
    // collection) every association on it.
    const manages = await canManageCollection(auth.identity, collectionId);
    const visible = rows.filter(
      (row) =>
        manages ||
        (row.connectionGroupId !== null &&
          auth.identity.groups.includes(row.connectionGroupId)),
    );
    return jsonResponse(200, {
      associations: visible.map(toApiAssociation),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const POST: APIRoute = async ({ params, request, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required for this action",
    );
  }
  // Defense in depth — the guard also gates this to operator|admin.
  if (!canMutate(auth.identity)) {
    return authzError(
      403,
      "forbidden",
      "This action requires the operator or admin role",
    );
  }
  const collectionId = params.id;
  if (!collectionId) return jsonResponse(404, { error: "Collection not found" });

  try {
    const body = await request.json().catch(() => null);
    const parsed = parseAssociationCreate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    if (!(await canManageCollection(auth.identity, collectionId))) {
      return authzError(
        403,
        "forbidden",
        "You do not have permission to manage this collection",
      );
    }

    const connection = await resolveUsableConnection(
      auth.identity,
      data.connection_id,
    );
    if ("response" in connection) return connection.response;

    // reference mode catalogs assets in place at the source, so it only makes
    // sense for an object-store source (§5.1).
    if (
      data.config.storage_mode === "reference" &&
      connection.protocol !== "s3"
    ) {
      return jsonResponse(400, {
        error: "storage_mode 'reference' requires an object-store (s3) connection",
      });
    }

    const association = await createAssociation({
      collectionId,
      connectionId: data.connection_id,
      direction: data.direction,
      enabled: data.enabled,
      config: data.config,
      expectation: data.expectation,
      createdBy: auth.identity.sub,
    });
    return jsonResponse(201, toApiAssociation(association));
  } catch (err) {
    if (err instanceof DuplicateAssociationError) {
      return jsonResponse(409, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
