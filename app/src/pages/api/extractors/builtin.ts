/**
 * GET /api/extractors/builtin — the built-in extractor registry (X-4,
 * X-queue spec §7). Member+ (any authenticated user): it holds no secrets,
 * and the ingest form's picker needs it. Served from the document bundled at
 * build time (`@/lib/extractors/registry`), so what this returns is exactly
 * what `POST /api/processes/builtin` will instantiate.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { builtinExtractors } from "@/lib/extractors/registry";
import { jsonResponse } from "@/lib/http/response";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(
      401,
      "unauthenticated",
      "Authentication required to list built-in extractors",
    );
  }
  return jsonResponse(200, { extractors: builtinExtractors() });
};
