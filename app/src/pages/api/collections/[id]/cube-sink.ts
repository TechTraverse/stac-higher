/**
 * /api/collections/[id]/cube-sink — the virtual cube sink whose CUBE
 * collection is [id] (virtual cube spec §7, ADR 0022).
 *
 * GET    — member+ of the cube collection's group: the sink + ledger summary.
 * PUT    — operator+: create or replace. Both collections must exist, the
 *          source must have an enabled reference-mode ingest association,
 *          and every such association must read anonymously (§13) — else
 *          422 with a `code`. Once the repository exists the layout
 *          (parser, append_dim, variables, loadable_variables) is locked
 *          (409 cube_layout_locked).
 * PATCH  — operator+: { enabled }.
 * DELETE — operator+: removes the row; the repository stays until the
 *          collection is deleted (asset_gc), so this is reversible.
 *
 * Group rule (§14.1): the sink follows the cube collection's ownership
 * (`canManageCollection`). Outside the caller's groups every verb is a 404.
 * PUT also requires the caller to manage the SOURCE collection; one they
 * can't is reported exactly like a missing one (422
 * source_collection_not_found).
 * Role + audit (`cube_sink`) live in the guard; re-checked here.
 */
import type { APIRoute } from "astro";
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canManageCollection } from "@/lib/associations/access";
import { jsonResponse } from "@/lib/http/response";
import { cubeSinkPatchSchema, cubeSinkPutSchema, layoutChanged } from "@/lib/cubes/schemas";
import {
  cubeLedgerSummary,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  referenceIngestSources,
  setCubeSinkEnabled,
  upsertCubeSink,
} from "@/lib/cubes/storage";

const notFound = () => jsonResponse(404, { error: "Cube sink not found" });
const refuse = (status: number, code: string, error: string) => jsonResponse(status, { error, code });

/** 401 / 403 / 404 preamble shared by every verb. Returns the caller. */
async function preamble(
  auth: AuthContext | undefined,
  collectionId: string | undefined,
  requireOperator: boolean,
): Promise<{ identity: CanonicalIdentity; collectionId: string } | { response: Response }> {
  if (!auth?.authenticated) {
    return { response: authzError(401, "unauthenticated", "Authentication required for this action") };
  }
  if (requireOperator && !canMutate(auth.identity)) {
    return { response: authzError(403, "forbidden", "This action requires the operator or admin role") };
  }
  if (!collectionId || !(await canManageCollection(auth.identity, collectionId))) {
    return { response: notFound() };
  }
  return { identity: auth.identity, collectionId };
}

function failure(err: unknown): Response {
  return jsonResponse(500, { error: err instanceof Error ? err.message : "Unknown error" });
}

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, false);
    if ("response" in pre) return pre.response;
    const sink = await getCubeSink(pre.collectionId);
    if (!sink) return notFound();
    return jsonResponse(200, { sink, ledger: await cubeLedgerSummary(sink.id) });
  } catch (err) {
    return failure(err);
  }
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    const cube = pre.collectionId;

    const parsed = cubeSinkPutSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
    }
    const { source_collection_id: source, config, enabled } = parsed.data;
    if (source === cube) {
      return jsonResponse(400, { error: "source_collection_id must differ from the cube collection" });
    }
    // The source must be manageable too, so a sink cannot hitch another
    // group's ingest flow. Same answer as a missing source: a collection
    // outside the caller's groups is not disclosed.
    const sourceNotFound = () =>
      refuse(422, "source_collection_not_found", `Source collection '${source}' does not exist`);
    if (!(await canManageCollection(pre.identity, source))) return sourceNotFound();

    const existing = await existingCollections([cube, source]);
    if (!existing.has(cube)) {
      return refuse(422, "cube_collection_not_found", `Collection '${cube}' does not exist`);
    }
    if (!existing.has(source)) return sourceNotFound();
    const refs = await referenceIngestSources(source);
    if (refs.length === 0) {
      return refuse(422, "no_reference_ingest",
        `Source collection '${source}' has no enabled reference-mode ingest association`);
    }
    if (refs.some((r) => !r.anonymous)) {
      return refuse(422, "signed_source_unsupported",
        "Cube sinks support anonymous (public) source connections only in v1");
    }

    const current = await getCubeSink(cube);
    if (current?.last_snapshot_id && layoutChanged(current.config, config)) {
      return refuse(409, "cube_layout_locked",
        "The cube repository already exists; parser, append_dim, variables and loadable_variables cannot change");
    }

    const { sink, created } = await upsertCubeSink({
      cubeCollectionId: cube, sourceCollectionId: source, config, enabled, createdBy: pre.identity.sub,
    });
    return jsonResponse(created ? 201 : 200, { sink });
  } catch (err) {
    return failure(err);
  }
};

export const PATCH: APIRoute = async ({ params, request, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    const parsed = cubeSinkPatchSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
    }
    const sink = await setCubeSinkEnabled(pre.collectionId, parsed.data.enabled);
    return sink ? jsonResponse(200, { sink }) : notFound();
  } catch (err) {
    return failure(err);
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    return (await deleteCubeSink(pre.collectionId)) ? jsonResponse(200, { deleted: true }) : notFound();
  } catch (err) {
    return failure(err);
  }
};
