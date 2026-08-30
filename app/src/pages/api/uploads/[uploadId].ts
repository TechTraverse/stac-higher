/**
 * GET /api/uploads/[uploadId] — poll a staged-upload session (Phase 7 spec
 * §4.2; the ADR 0004 request-table poll shape, reused).
 *
 * Read-only: returns the `staged_uploads` row the pipeline updates
 * (pending → finalizing → finalized | rejected | expired, with a
 * FinalizeResult-subset `result`). This is the API-visible outcome of a push
 * — the documented required last step: a push client's 201/200 from the item
 * write is provisional until this row goes terminal (spec §6.3 Tier 1).
 *
 * Access: authenticated; admin, member of the session's group, or the
 * session's creator (which covers sessions minted on unowned collections).
 * A non-visible or unknown session is a 404 — existence is scoped.
 *
 * The pipeline-written verdict columns pass through the lenient Zod reader
 * (`pushUploadStatusSchema` — the push-upload-status contract); a row the
 * reader cannot parse is a broken contract and 500s loudly rather than
 * serving a shape no client was promised.
 */
import type { APIRoute } from "astro";
import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/http/response";
import { canSeeStagedUpload, isUploadId } from "@/lib/uploads/access";
import { pushUploadStatusSchema } from "@/lib/uploads/schemas";
import { getStagedUpload } from "@/lib/uploads/storage";

export const GET: APIRoute = async ({ params, locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required for this action");
  }

  try {
    if (!isUploadId(params.uploadId)) {
      return jsonResponse(404, { error: "Upload session not found" });
    }
    const upload = await getStagedUpload(params.uploadId);
    if (!upload || !canSeeStagedUpload(auth.identity, upload)) {
      return jsonResponse(404, { error: "Upload session not found" });
    }

    const verdict = pushUploadStatusSchema.safeParse({
      status: upload.status,
      result: upload.result,
      error: upload.error,
    });
    if (!verdict.success) {
      return jsonResponse(500, {
        error: `staged_uploads row ${upload.id} violates the push-upload-status contract`,
      });
    }

    return jsonResponse(200, {
      upload: {
        id: upload.id,
        collection_id: upload.collectionId,
        item_id: upload.itemId,
        filenames: upload.filenames,
        status: verdict.data.status,
        result: verdict.data.result ?? null,
        error: verdict.data.error ?? null,
        created_at: upload.createdAt,
        expires_at: upload.expiresAt,
        finalized_at: upload.finalizedAt,
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
