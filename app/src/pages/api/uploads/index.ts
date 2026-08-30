/**
 * POST /api/uploads — mint presigned PUT URLs for asset uploads.
 *
 * TWO modes, selected by the body (Phase 7 spec §4.1):
 *
 * - CANONICAL (body has `item`; ROADMAP Phase 3 / ADR 0005, unchanged): the
 *   trusted UI path. The item form calls this before submit; bytes go
 *   straight to canonical storage (`assets/{collection}/{item}/{filename}`)
 *   and the returned `/api/assets/...` href resolves immediately.
 *
 * - STAGED (body WITHOUT `item`; Phase 7 push ingest): untrusted external
 *   bytes. Mints presigned PUTs into `staging/{upload_id}/{filename}`,
 *   inserts ONE `staged_uploads` binding row (`pending` — the authorization
 *   binding finalize checks, and where the client polls the async verdict),
 *   and returns `staging://{upload_id}/{filename}` hrefs the pushed item
 *   carries verbatim (§4.2). Preconditions, enforced BEFORE any bytes move:
 *   operator+ in the collection's owning group (unowned → any operator,
 *   ADR 0003), `externally_writable = true` (missing settings row = false —
 *   a collection must be explicitly flagged before it accepts push), and not
 *   `archived` (409, ADR 0011). `expires_at` is the §4.1 governing clock:
 *   ledger `created_at + STAGING_TTL_SECONDS`.
 *
 * Gating: `POST /api/uploads` is in the middleware's gated-route table, so the
 * guard enforces operator|admin and writes the audit row (the staged response
 * carries a top-level `id` so the guard's created-id extraction records the
 * session). The in-route check is defense-in-depth, matching the connections
 * routes.
 */
import type { APIRoute } from "astro";
import { z } from "zod";
import { authzError } from "@/lib/authz/guard";
import { canMutate, isAdmin } from "@/lib/authz/permissions";
import { getCollectionSettings } from "@/lib/collections/settings";
import { jsonResponse } from "@/lib/http/response";
import {
  assertSafeSegment,
  assetHref,
  canonicalAssetKey,
  sanitizeFilename,
  stagedHref,
  stagingKey,
  StorageKeyError,
} from "@/lib/storage/keys";
import { presignPutUrl } from "@/lib/storage/presign";
import { createStagedUpload } from "@/lib/uploads/storage";

const fileSchema = z.object({
  filename: z.string().min(1),
  contentType: z.string().optional(),
});

const uploadRequestSchema = z.object({
  collection: z.string().min(1),
  /** Present → canonical mode (trusted UI); absent → staged mode (§4.1). */
  item: z.string().min(1).optional(),
  files: z
    .array(fileSchema)
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

  // ------------------------------------------------------------------ staged
  if (item === undefined) {
    let filenames: string[];
    try {
      assertSafeSegment(collection, "collection");
      filenames = files.map((f) => sanitizeFilename(f.filename));
    } catch (err) {
      if (err instanceof StorageKeyError) {
        return jsonResponse(400, { error: err.message });
      }
      throw err;
    }
    if (new Set(filenames).size !== filenames.length) {
      return jsonResponse(400, {
        error: "Duplicate filenames in one upload session (after sanitizing)",
      });
    }

    try {
      // Preconditions (§4.1) — fail fast, before any bytes move. One sparse
      // settings read serves all three; an unknown collection has no row and
      // therefore refuses push (externally_writable defaults false).
      const settings = await getCollectionSettings(collection);
      if (
        settings.groupId !== null &&
        !isAdmin(auth.identity) &&
        !auth.identity.groups.includes(settings.groupId)
      ) {
        return authzError(
          403,
          "forbidden",
          "This collection is owned by a group you are not a member of",
        );
      }
      if (!settings.externallyWritable) {
        return jsonResponse(403, {
          error: `Collection "${collection}" does not accept external pushes (enable externally_writable in its Settings tab first)`,
          code: "not_externally_writable",
        });
      }
      if (settings.archived) {
        return jsonResponse(409, {
          error: `Collection "${collection}" is archived and takes no item writes`,
          code: "collection_archived",
        });
      }

      const session = await createStagedUpload({
        collectionId: collection,
        createdBy: auth.identity.sub,
        groupId: settings.groupId,
        filenames,
      });

      const uploads = await Promise.all(
        files.map(async ({ contentType }, i) => {
          const filename = filenames[i];
          const url = await presignPutUrl(
            stagingKey(session.id, filename),
            contentType,
          );
          return {
            filename,
            url,
            staged_href: stagedHref(session.id, filename),
          };
        }),
      );

      return jsonResponse(200, {
        // `id` mirrors upload_id so the guard's audit row records the session.
        id: session.id,
        upload_id: session.id,
        uploads,
        expires_at: session.expiresAt,
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to mint staged upload";
      return jsonResponse(500, { error: message });
    }
  }

  // --------------------------------------------------------------- canonical
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
