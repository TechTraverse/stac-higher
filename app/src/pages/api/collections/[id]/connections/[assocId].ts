/**
 * /api/collections/[id]/connections/[assocId] — get / update / delete an
 * ingest association (ROADMAP §5.1, §7 — Phase 4). `[id]` = collection id.
 *
 * GET    — any authenticated caller who can SEE the association.
 * PUT    — operator|admin who can see it; patches enabled/config/expectation.
 *          `storage_mode: reference` stays restricted to s3 connections.
 * DELETE — operator|admin who can see it. ingest_files rows cascade.
 *
 * A non-visible or wrong-collection association is a 404 (existence is
 * group-scoped). Role + audit live in the guard; re-checked here for defense.
 */
import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/http/response";
import {
  loadVisibleAssociation,
  resolveUsableConnection,
} from "@/lib/associations/access";
import { parseAssociationUpdate } from "@/lib/associations/schemas";
import {
  deleteAssociation,
  toApiAssociation,
  updateAssociation,
} from "@/lib/associations/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      false,
    );
    if ("response" in loaded) return loaded.response;
    return jsonResponse(200, toApiAssociation(loaded.association));
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      true,
    );
    if ("response" in loaded) return loaded.response;
    const existing = loaded.association;

    const body = await request.json().catch(() => null);
    const parsed = parseAssociationUpdate(body);
    if (!parsed.success) {
      return jsonResponse(400, {
        error: "Validation failed",
        details: parsed.error.issues,
      });
    }
    const data = parsed.data;

    if (
      data.config?.storage_mode === "reference" &&
      locals.auth?.authenticated
    ) {
      const connection = await resolveUsableConnection(
        locals.auth.identity,
        existing.connection_id,
      );
      if ("response" in connection) return connection.response;
      if (connection.protocol !== "s3") {
        return jsonResponse(400, {
          error:
            "storage_mode 'reference' requires an object-store (s3) connection",
        });
      }
    }

    const updated = await updateAssociation(existing.id, {
      enabled: data.enabled,
      config: data.config,
      expectation: data.expectation,
    });
    if (!updated) return jsonResponse(404, { error: "Association not found" });
    return jsonResponse(200, toApiAssociation(updated));
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  try {
    const loaded = await loadVisibleAssociation(
      locals.auth,
      params.id,
      params.assocId,
      true,
    );
    if ("response" in loaded) return loaded.response;
    await deleteAssociation(loaded.association.id);
    return new Response(null, { status: 204 });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
