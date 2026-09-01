import type { StacCatalog } from "@/stores/catalogStore";

/**
 * Route helpers for the two worlds UI-10 split apart.
 *
 * `/collections*` is the PRODUCT surface: built-in catalog only, full CRUD
 * through the ADR 0008 BFF. `/catalogs/[catalogId]/collections*` is the
 * read-only browser for every other catalog. Anything that renders a link to a
 * collection or item for a catalog it did not hard-code should go through
 * `collectionHref` / `itemHref` so the two never cross.
 *
 * **`?src=` makes a browse link shareable (UI-15).** The catalog id in the
 * path is minted by `crypto.randomUUID()` in the browser that added the
 * catalog, so it means nothing anywhere else — a link without `src` resolves
 * only in the sender's browser. `src` carries the catalog's URL, which is the
 * identity everyone agrees on: a recipient who already has that catalog
 * browses it under their own id, and one who does not is ASKED whether to add
 * it. Nothing is fetched before they answer.
 */
export interface BrowseTarget {
  id: string;
  /** The catalog's URL, emitted as `?src=` so the link travels. */
  url?: string | null;
}

function withSrc(path: string, target: BrowseTarget): string {
  return target.url
    ? `${path}?src=${encodeURIComponent(target.url)}`
    : path;
}

export function browseCollectionsPath(target: BrowseTarget): string {
  return withSrc(`/catalogs/${encodeURIComponent(target.id)}/collections`, target);
}

export function browseCollectionPath(
  target: BrowseTarget,
  collectionId: string,
): string {
  return withSrc(
    `/catalogs/${encodeURIComponent(target.id)}/collections/${encodeURIComponent(collectionId)}`,
    target,
  );
}

export function browseItemsPath(
  target: BrowseTarget,
  collectionId: string,
): string {
  return withSrc(
    `/catalogs/${encodeURIComponent(target.id)}/collections/${encodeURIComponent(collectionId)}/items`,
    target,
  );
}

export function browseItemPath(
  target: BrowseTarget,
  collectionId: string,
  itemId: string,
): string {
  return withSrc(
    `/catalogs/${encodeURIComponent(target.id)}/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    target,
  );
}

/** Product page for the built-in catalog, browse page for any other. */
export function collectionHref(
  catalog: StacCatalog | null | undefined,
  collectionId: string,
): string {
  return catalog && !catalog.builtIn
    ? browseCollectionPath(catalog, collectionId)
    : `/collections/${encodeURIComponent(collectionId)}`;
}

/** Product item page for the built-in catalog, browse page for any other. */
export function itemHref(
  catalog: StacCatalog | null | undefined,
  collectionId: string,
  itemId: string,
): string {
  return catalog && !catalog.builtIn
    ? browseItemPath(catalog, collectionId, itemId)
    : `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`;
}

/**
 * The link target to use while the route's catalog is unresolved.
 *
 * A page's breadcrumb and card hrefs are JSX PROPS, so they are built before
 * `BrowseFrame` gets a chance to early-return on a missing catalog — there is
 * no state in which "the catalog is null so this code won't run" is true. Fall
 * back to the route's own id and `?src=`, which is exactly what the current
 * URL already says, so the links stay correct on the add-catalog prompt too.
 */
export function browseTarget(
  catalog: StacCatalog | null | undefined,
  catalogId: string,
  src: string | null | undefined,
): BrowseTarget {
  return catalog ?? { id: catalogId, url: parseSrc(src) };
}

/** Trailing slashes only; the URL is compared, never fetched, at this point. */
export function normalizeCatalogUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

/**
 * A `?src=` value we are willing to show the user, or null.
 *
 * `src` arrives from a link someone else wrote, so it is untrusted: anything
 * that is not an http(s) URL is discarded rather than rendered or offered.
 * Even a valid one is only ever DISPLAYED here — adding the catalog, and
 * therefore fetching it, takes an explicit click.
 */
export function parseSrc(src: string | null | undefined): string | null {
  if (!src) return null;
  try {
    const parsed = new URL(src);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return normalizeCatalogUrl(src);
  } catch {
    return null;
  }
}

/**
 * Resolve the catalog a browse route addresses.
 *
 * With a `src`, the URL is the identity and the path id is ignored — the id in
 * a shared link belongs to the SENDER's browser, and a local catalog that
 * happens to carry the same id would be a different catalog entirely. Without
 * one, fall back to the id (a link the user made themselves, in this browser).
 *
 * Returns `undefined` while the persistent store is still empty (hydrating),
 * `null` once we know this browser has no such catalog.
 */
export function resolveBrowseCatalog(
  catalogs: StacCatalog[],
  catalogId: string,
  src: string | null,
): StacCatalog | null | undefined {
  if (catalogs.length === 0) return undefined;
  if (src) {
    const wanted = normalizeCatalogUrl(src);
    return (
      catalogs.find((c) => normalizeCatalogUrl(c.url) === wanted) ?? null
    );
  }
  return catalogs.find((c) => c.id === catalogId) ?? null;
}
