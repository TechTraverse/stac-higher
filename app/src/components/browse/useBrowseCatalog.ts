import { useStore } from "@nanostores/react";
import { $catalogs, type StacCatalog } from "@/stores/catalogStore";
import { parseSrc, resolveBrowseCatalog } from "@/lib/browse/paths";

/**
 * The catalog a browse route addresses: by `?src=` URL when the link carries
 * one (shareable), otherwise by the path id (local link). `undefined` while
 * the persistent store hydrates, `null` when this browser has no such catalog
 * — which is what makes `BrowseFrame` offer to add it.
 */
export function useBrowseCatalog(
  catalogId: string,
  src?: string | null,
): StacCatalog | null | undefined {
  const catalogs = useStore($catalogs);
  return resolveBrowseCatalog(catalogs, catalogId, parseSrc(src));
}
