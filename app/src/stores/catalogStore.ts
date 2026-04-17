import { computed } from "nanostores";
import { persistentAtom } from "@nanostores/persistent";

export interface StacCatalog {
  id: string;
  name: string;
  url: string;
  isDefault: boolean;
  proxy?: boolean;
}

export const $catalogs = persistentAtom<StacCatalog[]>(
  "stac-catalogs",
  [],
  {
    encode: JSON.stringify,
    decode: JSON.parse,
  },
);

export const $activeCatalogId = persistentAtom<string>(
  "stac-active-catalog",
  "",
);

export const $activeCatalog = computed(
  [$catalogs, $activeCatalogId],
  (catalogs, id) => {
    return catalogs.find((c) => c.id === id) ?? catalogs[0] ?? null;
  },
);

export function addCatalog(catalog: Omit<StacCatalog, "id">) {
  const id = crypto.randomUUID();
  const current = $catalogs.get();
  const isFirst = current.length === 0;
  $catalogs.set([
    ...current,
    { ...catalog, id, isDefault: isFirst || catalog.isDefault },
  ]);
  if (isFirst || catalog.isDefault) {
    $activeCatalogId.set(id);
  }
  return id;
}

export function updateCatalog(id: string, updates: Partial<StacCatalog>) {
  $catalogs.set(
    $catalogs.get().map((c) => (c.id === id ? { ...c, ...updates } : c)),
  );
}

export function removeCatalog(id: string) {
  const current = $catalogs.get();
  const filtered = current.filter((c) => c.id !== id);
  $catalogs.set(filtered);
  if ($activeCatalogId.get() === id && filtered.length > 0) {
    $activeCatalogId.set(filtered[0].id);
  }
}

export function setActiveCatalog(id: string) {
  $activeCatalogId.set(id);
}
