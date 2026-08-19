/**
 * /api/catalog/[...path] — the BFF for built-in-catalog browser writes
 * (ADR 0008, resolves ISSUES I-50).
 *
 * In auth-enforced mode every catalog transaction at the proxy needs a bearer
 * token, but the browser can't present one: the access token is sealed inside
 * the httpOnly session cookie by design. This route forwards the mutation to
 * the built-in catalog and injects the caller's session access token
 * server-side. The proxy stays the enforcement point — the app only adds the
 * token. In dev-bypass mode there is no token and none is attached; the
 * pass-through proxy accepts the write, so dev and enforced posture exercise
 * the same code path (ADR 0008 decision 5).
 *
 * Scope is deliberately narrow: `matchCatalogTransaction` (shared with the
 * permission guard, so forwarded ≡ gated+audited) admits only transaction
 * endpoints, writes only, and the target URL is server-configured — this
 * cannot be used as a generic authenticated proxy. Reads keep their existing
 * direct path; external catalogs keep /api/proxy.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import {
  builtinCatalogUrl,
  matchCatalogTransaction,
} from "@/lib/catalog/transactions";
import { getCollectionSettings } from "@/lib/collections/settings";
import { markAssetGcTolerant } from "@/lib/gc/marks";
import { forwardUpstream } from "@/lib/http/forward";
import { jsonResponse } from "@/lib/http/response";
import { DEFAULT_MAX_BYTES } from "@/lib/http/safe-fetch";

const FORWARDED_RESPONSE_HEADERS = ["content-type", "etag", "last-modified"];

/** `collections/{c}` / `collections/{c}/items/{i}` → the ids, for the M2-F
 * hooks below. Null for shapes without a collection in the path. */
function pathIds(path: string): { collection: string; item: string | null } | null {
  const item = path.match(/^collections\/([^/]+)\/items\/([^/]+)$/);
  if (item) return { collection: item[1], item: item[2] };
  const items = path.match(/^collections\/([^/]+)\/items$/);
  if (items) return { collection: items[1], item: null };
  const collection = path.match(/^collections\/([^/]+)$/);
  if (collection) return { collection: collection[1], item: null };
  return null;
}

const handler: APIRoute = async ({ params, request, cookies }) => {
  const path = (params.path ?? "").replace(/\/+$/, "");
  const txn = matchCatalogTransaction(request.method, path);
  if (!txn) {
    return jsonResponse(404, {
      error: "Not a built-in-catalog transaction endpoint",
    });
  }

  // Archived = "delete the data, keep the record" (ADR 0009/0011): the data
  // plane of an archived collection is read-only — item writes are refused
  // while collection-metadata edits and deletes stay allowed.
  const ids = pathIds(path);
  if (txn.resourceType === "catalog_item" && txn.action !== "delete" && ids) {
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

  // Token injection (ADR 0008): oidc sessions carry the access token in the
  // sealed cookie — middleware has already refreshed it if it was near
  // expiry. Bypass mode has no token; the write goes out bare and the
  // pass-through proxy accepts it. (The guard has already 401'd anonymous
  // oidc callers before this handler runs.)
  const headers: Record<string, string> = { accept: "application/json" };
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

  const contentType = request.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  let body: ArrayBuffer | undefined;
  if (request.method.toUpperCase() !== "DELETE") {
    body = await request.arrayBuffer();
    if (body.byteLength > DEFAULT_MAX_BYTES) {
      return jsonResponse(413, {
        error: `Request body exceeds ${DEFAULT_MAX_BYTES} bytes`,
      });
    }
  }

  const response = await forwardUpstream(
    `${builtinCatalogUrl()}/${path}`,
    { method: request.method, headers, body },
    FORWARDED_RESPONSE_HEADERS,
  );

  // M2-F (ADR 0011): a successful catalog delete schedules the canonical
  // asset bytes for collection — item delete marks the item prefix,
  // collection delete the whole-collection prefix (closes I-51's GC half).
  // Best-effort AFTER upstream success; a failed mark never fails the
  // request the catalog already applied.
  if (response.ok && txn.action === "delete" && ids) {
    await markAssetGcTolerant({
      collectionId: ids.collection,
      itemId: txn.resourceType === "catalog_item" ? ids.item : null,
      reason:
        txn.resourceType === "catalog_item" ? "item_delete" : "collection_delete",
    });
  }

  return response;
};

export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
