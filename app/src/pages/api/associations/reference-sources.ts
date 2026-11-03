/**
 * GET /api/associations/reference-sources — the cube sink's source picker
 * (Z-8a; virtual cube spec §7, §9).
 *
 * One row per source collection that has an enabled, live reference-mode
 * ingest association: `{ collection_id, title, associations[], refusal }`.
 * `refusal` is the 422 code PUT /api/collections/[id]/cube-sink would give
 * for that source (null = eligible), computed by the same
 * `referenceSourceRefusal` the route uses, so the picker cannot disagree.
 *
 * Group rule: `canManageCollection`, not the connection-group rule of
 * /api/monitoring/flows. A collection is listed when it is unowned (ADR 0003)
 * or owned by one of the caller's groups; admins see all. PUT refuses any
 * source the caller cannot manage, so listing one would offer a dead choice.
 *
 * Read-only and ungated: a GET has no role gate and no audit row
 * (`matchGatedRoute` is null), so any authenticated caller may list; the
 * group filter is applied in SQL.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { listReferenceSourceCollections } from "@/lib/cubes/storage";
import { referenceSourceRefusal } from "@/lib/cubes/status";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required to list cube sources");
  }
  try {
    const collections = await listReferenceSourceCollections(
      isAdmin(auth.identity) ? null : auth.identity.groups,
    );
    return jsonResponse(200, {
      collections: collections.map((c) => ({
        ...c,
        refusal: referenceSourceRefusal(c.associations)?.code ?? null,
      })),
    });
  } catch (err) {
    return jsonResponse(500, { error: err instanceof Error ? err.message : "Unknown error" });
  }
};
