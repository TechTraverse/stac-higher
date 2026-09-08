import type { StacCatalog } from "@/stores/catalogStore";
import type { StacLink } from "@/lib/stac-api/types";

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

// ---------------------------------------------------------------------------
// Item links → in-app targets (D-2).
//
// A `derived_from` link's href is whatever the writer put there: D-1 stamps
// root-relative `/collections/{c}/items/{i}` (the catalog's own href), an
// external catalog may carry absolute hrefs into itself or another catalog.
// Resolution maps the href onto a catalog THIS browser knows — the page's own
// first, then every configured one — and hands `itemHref` the match, so the
// built-in catalog lands on the product page and any other on its browse page
// with `?src=` (UI-15). Anything unmatched is an external anchor: never a
// dead in-app route.
// ---------------------------------------------------------------------------

export interface LinkTarget {
  href: string;
  label: string;
  /** True when the href matched no configured catalog and opens as written. */
  external: boolean;
}

const ITEM_PATH = /^\/collections\/([^/?#]+)\/items\/([^/?#]+)\/?$/;

function parseItemPath(path: string): { collection: string; item: string } | null {
  const m = ITEM_PATH.exec(path.split(/[?#]/)[0]);
  if (!m) return null;
  try {
    return { collection: decodeURIComponent(m[1]), item: decodeURIComponent(m[2]) };
  } catch {
    return null;
  }
}

const ITEM_PATH_TAIL = /\/collections\/([^/?#]+)\/items\/([^/?#]+)\/?$/;

/** The link's title, else `collection / item` when the href ends in an item
 *  path (wherever the catalog lives), else the href's last two segments. */
function externalLabel(link: StacLink): string {
  if (link.title) return link.title;
  const path = link.href.split(/[?#]/)[0];
  const tail = ITEM_PATH_TAIL.exec(path);
  if (tail) {
    try {
      return `${decodeURIComponent(tail[1])} / ${decodeURIComponent(tail[2])}`;
    } catch {
      // fall through to the segment label
    }
  }
  const segments = path.split("/").filter(Boolean);
  return segments.slice(-2).join(" / ") || link.href;
}

export function resolveItemLink(
  link: StacLink,
  pageCatalog: StacCatalog | null | undefined,
  catalogs: readonly StacCatalog[],
): LinkTarget {
  const href = link.href.trim();
  let catalog: StacCatalog | null | undefined = undefined;
  let path: string | null = null;

  if (href.startsWith("/")) {
    // Root-relative: the catalog this page is showing.
    catalog = pageCatalog;
    path = href;
  } else {
    const candidates = [pageCatalog, ...catalogs].filter(
      (c, i, all): c is StacCatalog => !!c && all.findIndex((o) => o?.id === c.id) === i,
    );
    for (const c of candidates) {
      const base = normalizeCatalogUrl(c.url);
      if (base && href.startsWith(`${base}/`)) {
        catalog = c;
        path = href.slice(base.length);
        break;
      }
    }
  }

  const parsed = path === null ? null : parseItemPath(path);
  if (!parsed) {
    return { href: link.href, label: externalLabel(link), external: true };
  }
  return {
    href: itemHref(catalog, parsed.collection, parsed.item),
    label: link.title ?? `${parsed.collection} / ${parsed.item}`,
    external: false,
  };
}
