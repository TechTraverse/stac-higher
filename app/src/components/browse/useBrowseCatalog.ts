import { useStore } from "@nanostores/react";
import { $catalogs, type StacCatalog } from "@/stores/catalogStore";

/**
 * Resolve the catalog a browse route addresses. The id comes from the URL, so
 * it may not exist (a stale link, or a catalog removed on another device —
 * catalogs live in localStorage). `undefined` while the persistent store is
 * still hydrating, `null` once we know the id is unknown.
 */
export function useBrowseCatalog(
  catalogId: string,
): StacCatalog | null | undefined {
  const catalogs = useStore($catalogs);
  if (catalogs.length === 0) return undefined;
  return catalogs.find((c) => c.id === catalogId) ?? null;
}
