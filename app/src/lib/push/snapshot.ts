/**
 * `prior_item` snapshot for brokered push PUTs (Phase 7 spec §4.3 / §6.3).
 *
 * A brokered PUT to an EXISTING item replaces the stored document at the
 * proxy before finalize ever runs — pgstac keeps no history, so if finalize
 * later rejects the push the pre-push version is gone unless someone saved
 * it. This module saves it: fetch the currently stored item and write it into
 * the upload session's ledger row (`staged_uploads.prior_item`), the restore
 * point the §6.3 `op = update, brokered` outcome upserts back.
 *
 * Review-residual R2 guards, both enforced here:
 * - FIRST WRITE WINS — the ledger update is `… AND prior_item IS NULL`, so a
 *   second brokered PUT in the same session can never overwrite an existing
 *   snapshot with its own (staged) predecessor: a rejection must restore the
 *   state before the FIRST push of the session, never the first push itself.
 * - NEVER SNAPSHOT A STAGED-HREF DOCUMENT — a stored item still carrying
 *   `staging://` hrefs is by definition not a restorable good version
 *   (restoring it would resurrect hrefs that will dangle); such a document is
 *   skipped, leaving any earlier snapshot (or none) in place.
 *
 * Fail-closed: if the current item cannot be read (upstream error, not a
 * 404) or the ledger write fails, the PUT is refused — forwarding without a
 * snapshot would silently downgrade a brokered update to the direct path's
 * leave-broken semantics (§6.3), which is exactly what the brokered contract
 * promises clients they avoid.
 */
import { builtinCatalogUrl } from "@/lib/catalog/transactions";
import { SafeFetchError, safeFetch } from "@/lib/http/safe-fetch";
import { bindPriorItemSnapshot } from "@/lib/uploads/storage";
import { hasStagedHrefs } from "./prevalidate";

export type SnapshotResult =
  | { ok: true }
  | { ok: false; status: number; error: string };

export async function snapshotPriorItem(args: {
  uploadId: string;
  collectionId: string;
  itemId: string;
}): Promise<SnapshotResult> {
  const url = `${builtinCatalogUrl()}/collections/${encodeURIComponent(
    args.collectionId,
  )}/items/${encodeURIComponent(args.itemId)}`;

  let status: number;
  let body: Uint8Array;
  try {
    const result = await safeFetch(url, {
      method: "GET",
      headers: { accept: "application/json" },
    });
    status = result.status;
    body = result.body;
  } catch (err) {
    const msg =
      err instanceof SafeFetchError || err instanceof Error
        ? err.message
        : "unknown error";
    return {
      ok: false,
      status: 502,
      error: `Could not read the current item to snapshot before the staged PUT: ${msg}`,
    };
  }

  // 404 = the PUT creates the item — nothing prior exists, nothing to save.
  if (status === 404) return { ok: true };
  if (status < 200 || status >= 300) {
    return {
      ok: false,
      status: 502,
      error: `Could not read the current item to snapshot before the staged PUT (upstream ${status})`,
    };
  }

  let stored: unknown;
  try {
    stored = JSON.parse(new TextDecoder().decode(body));
  } catch {
    return {
      ok: false,
      status: 502,
      error: "Current item is not valid JSON — refusing to proceed without a snapshot",
    };
  }

  // R2: a staged-href document is never a restore point.
  if (hasStagedHrefs(stored)) return { ok: true };

  try {
    await bindPriorItemSnapshot(args.uploadId, stored);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "unknown error";
    return {
      ok: false,
      status: 500,
      error: `Could not record the prior-item snapshot: ${msg}`,
    };
  }
  return { ok: true };
}
