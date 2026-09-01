import type { StacCatalog } from "@/stores/catalogStore";

/**
 * Route helpers for the two worlds UI-10 split apart.
 *
 * `/collections*` is the PRODUCT surface: built-in catalog only, full CRUD
 * through the ADR 0008 BFF. `/catalogs/[catalogId]/collections*` is the
 * read-only browser for every other catalog. Anything that renders a link to a
 * collection or item for a catalog it did not hard-code should go through
 * `collectionHref` / `itemHref` so the two never cross.
 */
export function browseCollectionsPath(catalogId: string): string {
  return `/catalogs/${encodeURIComponent(catalogId)}/collections`;
}

export function browseCollectionPath(
  catalogId: string,
  collectionId: string,
): string {
  return `${browseCollectionsPath(catalogId)}/${encodeURIComponent(collectionId)}`;
}

export function browseItemsPath(
  catalogId: string,
  collectionId: string,
): string {
  return `${browseCollectionPath(catalogId, collectionId)}/items`;
}

export function browseItemPath(
  catalogId: string,
  collectionId: string,
  itemId: string,
): string {
  return `${browseItemsPath(catalogId, collectionId)}/${encodeURIComponent(itemId)}`;
}

/** Product page for the built-in catalog, browse page for any other. */
export function collectionHref(
  catalog: StacCatalog | null | undefined,
  collectionId: string,
): string {
  return catalog && !catalog.builtIn
    ? browseCollectionPath(catalog.id, collectionId)
    : `/collections/${encodeURIComponent(collectionId)}`;
}

/** Product item page for the built-in catalog, browse page for any other. */
export function itemHref(
  catalog: StacCatalog | null | undefined,
  collectionId: string,
  itemId: string,
): string {
  return catalog && !catalog.builtIn
    ? browseItemPath(catalog.id, collectionId, itemId)
    : `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`;
}
