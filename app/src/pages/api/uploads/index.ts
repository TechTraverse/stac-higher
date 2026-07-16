/**
 * POST /api/uploads — mint presigned PUT URLs for asset uploads (ROADMAP Phase 3,
 * flow C / §6.3).
 *
 * The item form calls this before submit: for each file it returns a short-lived
 * PUT URL (browser uploads bytes straight to object storage) plus the
 * `/api/assets/...` href to persist on the asset. Manual UI uploads write
 * directly to CANONICAL storage — the app owns the item id and is trusted — so
 * the asset resolves through the asset route immediately. The untrusted external
 * push path (staging + finalize) is Phase 7; its staging key layout already
 * lives in `lib/storage/keys` (`stagingKey`).
 *
 * Gating: `POST /api/uploads` is in the middleware's gated-route table, so the
 * guard enforces operator|admin and writes the audit row. The in-route check is
 * defense-in-depth, matching the connections routes.
 */
import type { APIRoute } from "astro";
import { z } from "zod";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { jsonResponse } from "@/lib/http/response";
import { canonicalAssetKey, assetHref, StorageKeyError } from "@/lib/storage/keys";
import { presignPutUrl } from "@/lib/storage/presign";

const uploadRequestSchema = z.object({
  collection: z.string().min(1),
  item: z.string().min(1),
  files: z
    .array(
      z.object({
        filename: z.string().min(1),
        contentType: z.string().optional(),
      }),
    )
    .min(1, "At least one file is required")
    .max(50, "Too many files in one request"),
});

export const POST: APIRoute = async ({ request, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required for this action");
  }
  if (!canMutate(auth.identity)) {
    return authzError(403, "forbidden", "This action requires the operator or admin role");
  }

  const body = await request.json().catch(() => null);
  const parsed = uploadRequestSchema.safeParse(body);
  if (!parsed.success) {
    return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
  }

  const { collection, item, files } = parsed.data;

  try {
    const uploads = await Promise.all(
      files.map(async ({ filename, contentType }) => {
        const key = canonicalAssetKey(collection, item, filename);
        const url = await presignPutUrl(key, contentType);
        return { filename, key, url, href: assetHref(collection, item, filename) };
      }),
    );
    return jsonResponse(200, { uploads });
  } catch (err) {
    if (err instanceof StorageKeyError) {
      return jsonResponse(400, { error: err.message });
    }
    const message = err instanceof Error ? err.message : "Failed to presign upload";
    return jsonResponse(500, { error: message });
  }
};
