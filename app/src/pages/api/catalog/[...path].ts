/**
 * /api/catalog/[...path] — the BFF for built-in-catalog writes (ADR 0008,
 * resolves ISSUES I-50; extended by Phase 7 §4.3 as the documented BROKERED
 * push write path — P7-D).
 *
 * In auth-enforced mode every catalog transaction at the proxy needs a bearer
 * token. Browser sessions can't present one (the access token is sealed in
 * the httpOnly cookie), so this route forwards the mutation and injects the
 * caller's session access token server-side. Bearer-identity callers (Phase
 * 7 push clients, spec §3) instead have their OWN token forwarded verbatim —
 * the proxy stays the token-enforcement point either way. In dev-bypass mode
 * there is no token and none is attached; the pass-through proxy accepts the
 * write, so dev and enforced posture exercise the same code path.
 *
 * Phase 7 additions (spec §4.3):
 * - EVERY bearer-identity write is held to the §4.1 precondition set first:
 *   `externally_writable = true` (missing settings row = false), the ADR
 *   0003/M2-E group rule, and not `archived` for item writes. Without this
 *   the enforced-mode `X-BFF-Auth` exemption (§5.2/ADR 0015) would let any
 *   bearer operator write into ANY collection through the brokered route
 *   (review residual R1). Session-cookie callers are untouched — the browser
 *   UI keeps writing to every collection it manages (ADR 0008's obligation);
 *   the split is on HOW the identity arrived (`locals.bearerToken` is set
 *   only for bearer-derived identities), not on who it is.
 * - Bodies containing `staging://` hrefs are pre-validated synchronously
 *   (grammar, §4.2 session rules, `collection` field, light shape — Tier 0
 *   of §6.3) and, on a PUT to an existing item, the current stored document
 *   is snapshotted into the session's ledger row (`prior_item`) under the R2
 *   guards. Staged-href PATCHes are rejected — updates require PUT (R3).
 * - When `CATALOG_BFF_SHARED_SECRET` is configured, forwarded writes carry
 *   `X-BFF-Auth` so the enforced-mode proxy policy exempts app-mediated
 *   writes from the external-writability CQL2 gate. The exemption is sound
 *   only because of the bearer precondition set above — the two are one
 *   decision (ADR 0015).
 *
 * Scope is deliberately narrow: `matchCatalogTransaction` (shared with the
 * permission guard, so forwarded ≡ gated+audited) admits only transaction
 * endpoints, writes only, and the target URL is server-configured — this
 * cannot be used as a generic authenticated proxy. Reads keep their existing
 * direct path; external catalogs keep /api/proxy. The route stays thin in
 * ADR 0008's sense: preconditions + pre-validation + snapshot + forwarding;
 * STAC semantics live in the catalog plane and finalize.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import { authzError } from "@/lib/authz/guard";
import { isAdmin } from "@/lib/authz/permissions";
import {
  builtinCatalogUrl,
  matchCatalogTransaction,
} from "@/lib/catalog/transactions";
import { getCollectionSettings } from "@/lib/collections/settings";
import { CUBE_ITEM_ID, isCubeItemId, writtenItemIds } from "@/lib/cubes/reserved";
import { cubeSinksOnCollectionDeleteTolerant } from "@/lib/cubes/storage";
import { markAssetGcTolerant } from "@/lib/gc/marks";
import { forwardUpstream } from "@/lib/http/forward";
import { jsonResponse } from "@/lib/http/response";
import { DEFAULT_MAX_BYTES } from "@/lib/http/safe-fetch";
import { BFF_AUTH_HEADER, getBffSharedSecret } from "@/lib/push/config";
import {
  hasStagedHrefs,
  preValidateStagedWrite,
} from "@/lib/push/prevalidate";
import { snapshotPriorItem } from "@/lib/push/snapshot";

const FORWARDED_RESPONSE_HEADERS = ["content-type", "etag", "last-modified"];

/** `collections/{c}` / `collections/{c}/items[/{i}]` → the ids, for the
 * M2-F/P7-D hooks below. Null for shapes without a collection in the path. */
function pathIds(path: string): { collection: string; item: string | null } | null {
  const item = path.match(/^collections\/([^/]+)\/items\/([^/]+)$/);
  if (item) return { collection: item[1], item: item[2] };
  const items = path.match(/^collections\/([^/]+)\/items$/);
  if (items) return { collection: items[1], item: null };
  const collection = path.match(/^collections\/([^/]+)$/);
  if (collection) return { collection: collection[1], item: null };
  return null;
}

const handler: APIRoute = async ({ params, request, cookies, locals }) => {
  const path = (params.path ?? "").replace(/\/+$/, "");
  const method = request.method.toUpperCase();
  const txn = matchCatalogTransaction(method, path);
  if (!txn) {
    return jsonResponse(404, {
      error: "Not a built-in-catalog transaction endpoint",
    });
  }

  const ids = pathIds(path);
  const bearerToken = locals.bearerToken;
  const isItemWrite = txn.resourceType === "catalog_item" && txn.action !== "delete";

  if (typeof bearerToken === "string") {
    // §4.3 R1: the full §4.1 precondition set for EVERY bearer-identity
    // write, staged hrefs or not. Fail CLOSED — tolerating a settings-read
    // failure here would invert `externally_writable` on the documented
    // default push path.
    const auth = locals.auth;
    if (!auth?.authenticated) {
      // The permission guard 401s first; defense in depth.
      return authzError(401, "unauthenticated", "Authentication required for catalog writes");
    }
    if (!ids) {
      // POST /collections — no collection to evaluate the precondition set
      // against; collection creation is UI/BFF-session work (ADR 0015).
      return authzError(
        403,
        "forbidden",
        "External API clients cannot create collections through the brokered path",
      );
    }
    let settings;
    try {
      settings = await getCollectionSettings(ids.collection);
    } catch {
      return jsonResponse(503, {
        error:
          "Collection settings are unavailable — bearer catalog writes are refused until they can be checked",
      });
    }
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
        error: `Collection "${ids.collection}" does not accept external pushes (enable externally_writable in its Settings tab first)`,
        code: "not_externally_writable",
      });
    }
    if (isItemWrite && settings.archived) {
      return jsonResponse(409, {
        error: `Collection '${ids.collection}' is archived and no longer accepts item writes`,
        code: "collection_archived",
      });
    }
  } else if (isItemWrite && ids) {
    // Archived = "delete the data, keep the record" (ADR 0009/0011): the
    // data plane of an archived collection is read-only — item writes are
    // refused while collection-metadata edits and deletes stay allowed.
    // Session-caller posture is unchanged: best-effort on a settings error.
    try {
      const settings = await getCollectionSettings(ids.collection);
      if (settings.archived) {
        return jsonResponse(409, {
          error: `Collection '${ids.collection}' is archived and no longer accepts item writes`,
        });
      }
    } catch {
      // Settings unreadable (dev DB down) — don't block the write path the
      // proxy will authorize anyway; archived enforcement is best-effort.
    }
  }

  // Token injection: a bearer caller's OWN token is forwarded verbatim (§4.3
  // — the proxy's policy sees the external identity, not the app's). For
  // session callers, oidc sessions carry the access token in the sealed
  // cookie — middleware has already refreshed it if it was near expiry.
  // Bypass mode has no token; the write goes out bare and the pass-through
  // proxy accepts it. (The guard has already 401'd anonymous oidc callers
  // before this handler runs.)
  const headers: Record<string, string> = { accept: "application/json" };
  if (typeof bearerToken === "string") {
    headers.authorization = `Bearer ${bearerToken}`;
  } else {
    const cfg = getAuthConfig();
    if (cfg.mode === "oidc") {
      const session = cfg.sessionSecret
        ? await readSession(cookies, cfg.sessionSecret)
        : null;
      if (!session) {
        return jsonResponse(401, {
          error: "Authentication required for catalog writes",
        });
      }
      headers.authorization = `Bearer ${session.accessToken}`;
    }
  }

  // ADR 0015: app-mediated writes carry the shared-secret header when the
  // deployment configures one (mandatory in the enforced overlay — P7-G).
  // Never log the value.
  const bffSecret = getBffSharedSecret();
  if (bffSecret !== null) headers[BFF_AUTH_HEADER] = bffSecret;

  const contentType = request.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  let body: ArrayBuffer | undefined;
  if (method !== "DELETE") {
    body = await request.arrayBuffer();
    if (body.byteLength > DEFAULT_MAX_BYTES) {
      return jsonResponse(413, {
        error: `Request body exceeds ${DEFAULT_MAX_BYTES} bytes`,
      });
    }

    // §4.3 staged pre-validation (Tier 0 of §6.3), for ANY caller whose body
    // involves `staging://` hrefs — the admission rules protect finalize
    // semantics, not authorization, so they hold regardless of how the
    // identity arrived. Ordinary writes never contain staged hrefs and skip
    // all of this (a non-JSON body cannot carry them either). The ORIGINAL
    // bytes are forwarded — the parse is analysis only.
    let doc: unknown;
    try {
      doc = JSON.parse(new TextDecoder().decode(body));
    } catch {
      doc = undefined;
    }
    // Z-2 (ADR 0022): `_cube` is reserved in every collection — it is a cube
    // repository's prefix, and a repository can outlive its sink row, so the
    // reservation does not depend on one (and cannot race a sink PUT).
    if (isItemWrite && ids && writtenItemIds(ids.item, doc).some(isCubeItemId)) {
      return jsonResponse(422, {
        error: `Item id '${CUBE_ITEM_ID}' is reserved for cube repositories`,
        code: "reserved_item_id",
      });
    }
    if (doc !== undefined && hasStagedHrefs(doc)) {
      const verdict = await preValidateStagedWrite(doc, {
        method,
        resourceType: txn.resourceType,
        pathCollection: ids?.collection ?? null,
        pathItemId: ids?.item ?? null,
      });
      if (!verdict.ok) {
        const { status, error, code } = verdict;
        return jsonResponse(status, code ? { error, code } : { error });
      }
      // §4.3: on a PUT to an existing item, snapshot the stored document
      // into the session's ledger row (the §6.3 restore point) under the R2
      // guards — first write wins, never a staged-href document. Fail
      // closed: without the snapshot a brokered update silently degrades to
      // the direct path's leave-broken rejection semantics.
      if (method === "PUT" && ids?.item) {
        const snap = await snapshotPriorItem({
          uploadId: verdict.uploadId,
          collectionId: ids.collection,
          itemId: ids.item,
        });
        if (!snap.ok) return jsonResponse(snap.status, { error: snap.error });
      }
    }
  }

  const response = await forwardUpstream(
    `${builtinCatalogUrl()}/${path}`,
    { method, headers, body },
    FORWARDED_RESPONSE_HEADERS,
  );

  // M2-F (ADR 0011): a successful catalog delete schedules the canonical
  // asset bytes for collection — item delete marks the item prefix,
  // collection delete the whole-collection prefix (closes I-51's GC half).
  // Best-effort AFTER upstream success; a failed mark never fails the
  // request the catalog already applied.
  // (markAssetGc never marks an item `_cube`: that prefix is a cube
  // repository's, collected only with the whole collection — Z-2.)
  if (response.ok && txn.action === "delete" && ids) {
    await markAssetGcTolerant({
      collectionId: ids.collection,
      itemId: txn.resourceType === "catalog_item" ? ids.item : null,
      reason:
        txn.resourceType === "catalog_item" ? "item_delete" : "collection_delete",
    });
    // Z-2: a deleted cube collection takes its sink with it (its repository
    // rides the collection_delete GC mark above); a deleted SOURCE only
    // disables the sinks it fed, so the cube keeps its lock and ledger.
    if (txn.resourceType === "catalog_collection") {
      await cubeSinksOnCollectionDeleteTolerant(ids.collection);
    }
  }

  return response;
};

export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
