/**
 * Built-in-catalog BFF seam (ADR 0008): the single source of truth for which
 * catalog paths are transaction endpoints and where the built-in catalog
 * lives.
 *
 * Both the BFF route (`/api/catalog/[...path]` — forwards a request iff it
 * matches) and the permission guard (`lib/authz/permissions.ts` — gates and
 * audits the same match) call `matchCatalogTransaction`, so "forwardable" and
 * "gated+audited" are the same set by construction. A shape added here is
 * automatically both; the two can never drift apart — drift would reopen the
 * exact audit gap I-50 closed.
 */

export interface CatalogTransaction {
  action: "create" | "update" | "delete";
  resourceType: "catalog_collection" | "catalog_item";
  /** Known for update/delete (from the path); null for create — the guard
   * fills it from the upstream 200/201 body (STAC returns the object). */
  resourceId: string | null;
}

/**
 * Classify a catalog-relative path (e.g. `collections/c1/items/i1`) as a
 * transaction, or null for everything else (reads, /search, arbitrary paths).
 */
export function matchCatalogTransaction(
  method: string,
  path: string,
): CatalogTransaction | null {
  const m = method.toUpperCase();

  if (m === "POST" && path === "collections") {
    return { action: "create", resourceType: "catalog_collection", resourceId: null };
  }
  if (m === "POST" && /^collections\/[^/]+\/items$/.test(path)) {
    return { action: "create", resourceType: "catalog_item", resourceId: null };
  }

  const collection = path.match(/^collections\/([^/]+)$/);
  if (collection) {
    if (m === "PUT" || m === "PATCH") {
      return {
        action: "update",
        resourceType: "catalog_collection",
        resourceId: collection[1],
      };
    }
    if (m === "DELETE") {
      return {
        action: "delete",
        resourceType: "catalog_collection",
        resourceId: collection[1],
      };
    }
  }

  const item = path.match(/^collections\/([^/]+)\/items\/([^/]+)$/);
  if (item) {
    const resourceId = `${item[1]}/${item[2]}`;
    if (m === "PUT" || m === "PATCH") {
      return { action: "update", resourceType: "catalog_item", resourceId };
    }
    if (m === "DELETE") {
      return { action: "delete", resourceType: "catalog_item", resourceId };
    }
  }

  return null;
}

/**
 * Where the BFF forwards to. Server-side override first, then the public
 * build-time var, then the compose default — keep the default literal in sync
 * with `stores/catalogStore.ts`, which derives the CLIENT's built-in entry
 * (and thus the `builtIn` flag that triggers BFF routing) from
 * `PUBLIC_BUILTIN_CATALOG_URL` alone.
 */
export function builtinCatalogUrl(): string {
  return (
    process.env.BUILTIN_CATALOG_URL?.trim() ||
    process.env.PUBLIC_BUILTIN_CATALOG_URL?.trim() ||
    "http://localhost:8081"
  ).replace(/\/+$/, "");
}
