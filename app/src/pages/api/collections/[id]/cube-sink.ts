/**
 * /api/collections/[id]/cube-sink — the virtual cube sink whose CUBE
 * collection is [id] (virtual cube spec §7, ADR 0022).
 *
 * GET    — member+ of the cube collection's group: the sink + ledger summary.
 * PUT    — operator+: create or replace, after the source checks below.
 *          409 reserved_item_id if the cube collection holds an item
 *          `_cube`. Once the repository exists (`last_snapshot_id`), the
 *          layout (parser, append_dim, variables, loadable_variables) and
 *          the source are locked (409 cube_layout_locked). Before that, a
 *          source change resets the old source's prefixes, error and ledger.
 * PATCH  — operator+: { enabled }. Enabling re-runs the source checks;
 *          disabling never does.
 * DELETE — operator+: only while no repository exists (else 409
 *          cube_repository_exists). Afterwards the row carries the layout
 *          lock and the ledger, so it goes with the cube collection; disable
 *          the sink to stop appends.
 *
 * Source checks (§7, §13), shared by PUT and enabling: the caller manages
 * the source (one they can't reads exactly like a missing one), both
 * collections exist, the source has an enabled reference-mode ingest
 * association, and every such association reads anonymously — else 422 with
 * a `code`.
 *
 * Group rule (§14.1): the sink follows the cube collection's ownership
 * (`canManageCollection`). Outside the caller's groups every verb is a 404.
 * Role + audit (`cube_sink`) live in the guard; re-checked here.
 */
import type { APIRoute } from "astro";
import type { AuthContext, CanonicalIdentity } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canManageCollection } from "@/lib/associations/access";
import { jsonResponse } from "@/lib/http/response";
import { cubeSinkPatchSchema, cubeSinkPutSchema, layoutChanged } from "@/lib/cubes/schemas";
import { CUBE_ITEM_ID } from "@/lib/cubes/reserved";
import {
  collectionHasItem,
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

/** The source checks shared by PUT and enabling (see the module doc).
 * Returns the refusal, or null when the source qualifies. */
async function refuseSource(
  identity: CanonicalIdentity,
  cube: string,
  source: string,
): Promise<Response | null> {
  const sourceNotFound = () =>
    refuse(422, "source_collection_not_found", `Source collection '${source}' does not exist`);
  // Before any lookup, so nothing about a foreign collection is disclosed.
  if (!(await canManageCollection(identity, source))) return sourceNotFound();
  const [existing, refs] = await Promise.all([
    existingCollections([cube, source]),
    referenceIngestSources(source),
  ]);
  if (!existing.has(cube)) {
    return refuse(422, "cube_collection_not_found", `Collection '${cube}' does not exist`);
  }
  if (!existing.has(source)) return sourceNotFound();
  if (refs.length === 0) {
    return refuse(422, "no_reference_ingest",
      `Source collection '${source}' has no enabled reference-mode ingest association`);
  }
  if (refs.some((r) => !r.anonymous)) {
    return refuse(422, "signed_source_unsupported",
      "Cube sinks support anonymous (public) source connections only in v1");
  }
  return null;
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
    const refused = await refuseSource(pre.identity, cube, source);
    if (refused) return refused;

    const [hasCubeItem, firstRead] = await Promise.all([
      collectionHasItem(cube, CUBE_ITEM_ID),
      getCubeSink(cube),
    ]);
    // `_cube` is the repository's prefix: an existing item of that id would
    // own it for GC and serving (ADR 0022), so it must go first.
    if (hasCubeItem) {
      return refuse(409, "reserved_item_id",
        `Collection '${cube}' already holds an item named '${CUBE_ITEM_ID}'; delete or rename it first`);
    }
    const locked = () =>
      refuse(409, "cube_layout_locked",
        "The cube repository already exists; its source, parser, append_dim, variables and loadable_variables cannot change");

    // The lock check and the write are one optimistic step (#98): the upsert
    // applies only if last_snapshot_id is still what this check read. If the
    // first append committed in between, re-read and re-check once — a
    // window-only edit still lands; a layout or source change is refused.
    let current = firstRead;
    for (let attempt = 0; attempt < 2; attempt++) {
      if (attempt > 0) current = await getCubeSink(cube);
      const sourceChanged = current !== null && current.source_collection_id !== source;
      if (current?.last_snapshot_id && (sourceChanged || layoutChanged(current.config, config))) {
        return locked();
      }
      const written = await upsertCubeSink({
        cubeCollectionId: cube, sourceCollectionId: source, config, enabled,
        createdBy: pre.identity.sub, resetSourceState: sourceChanged,
        expectedSnapshotId: current?.last_snapshot_id ?? null,
      });
      if (written) return jsonResponse(written.created ? 201 : 200, { sink: written.sink });
    }
    return locked();
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
    const current = await getCubeSink(pre.collectionId);
    if (!current) return notFound();
    if (parsed.data.enabled) {
      const refused = await refuseSource(pre.identity, pre.collectionId, current.source_collection_id);
      if (refused) return refused;
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
    const current = await getCubeSink(pre.collectionId);
    if (!current) return notFound();
    // The row holds the repository's layout lock and ledger; the guarded
    // DELETE also covers a first commit landing after this read.
    if (current.last_snapshot_id || !(await deleteCubeSink(pre.collectionId))) {
      return refuse(409, "cube_repository_exists",
        "The cube repository exists; disable the sink instead, or delete the cube collection to remove both");
    }
    return jsonResponse(200, { deleted: true });
  } catch (err) {
    return failure(err);
  }
};
