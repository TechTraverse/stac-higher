/**
 * GET /api/assets/{collection}/{item}/{asset} — asset access (ROADMAP §3, Phase 3).
 *
 * Item asset hrefs point here rather than at storage directly, so the catalog
 * stays stable even if storage moves and every download passes an access check.
 * The `{asset}` segment is the stored object's filename.
 *
 * Flow: authorize → resolve the redirect target (presigned canonical URL today;
 * `reference`-mode source in Phase 4) → 302. GET is not gated by the middleware
 * (only mutations are), so the authorization check lives here.
 *
 * Authorization: the caller must be authenticated. Per-collection/group
 * authorization for reads depends on the read-visibility mapping deferred with
 * ADR 0002 (Phase 1 carry-forward); until then authentication is the boundary,
 * and in dev-bypass mode the static identity satisfies it so local flows work.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/http/response";
import { resolveAssetTarget } from "@/lib/storage/resolve";
import { StorageKeyError } from "@/lib/storage/keys";

export const GET: APIRoute = async ({ params, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(403, "forbidden", "Authentication is required to access assets");
  }

  const { collection, item, asset } = params;
  if (!collection || !item || !asset) {
    return jsonResponse(404, { error: "Asset not found" });
  }

  try {
    const target = await resolveAssetTarget(collection, item, asset);
    return new Response(null, {
      status: 302,
      headers: {
        Location: target.url,
        // presigned URLs are short-lived and per-request; never let a shared
        // cache hold the signed URL.
        "Cache-Control": "private, no-store",
      },
    });
  } catch (err) {
    if (err instanceof StorageKeyError) {
      return jsonResponse(400, { error: "Invalid asset path" });
    }
    const message = err instanceof Error ? err.message : "Failed to resolve asset";
    return jsonResponse(500, { error: message });
  }
};
