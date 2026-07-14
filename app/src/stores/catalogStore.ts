import { computed } from "nanostores";
import { persistentAtom } from "@nanostores/persistent";

export interface StacCatalog {
  id: string;
  name: string;
  url: string;
  isDefault: boolean;
  proxy?: boolean;
  /** Seeded by the app; cannot be deleted and its URL cannot be edited. */
  builtIn?: boolean;
}

/** Stable id of the built-in (local stac-fastapi) catalog entry. */
export const BUILT_IN_CATALOG_ID = "built-in";

const DEFAULT_BUILT_IN_URL = "http://localhost:8081";

function builtInCatalogUrl(): string {
  // PUBLIC_BUILTIN_CATALOG_URL is inlined by Astro/Vite at build time; the
  // guard keeps this module usable in non-Vite runtimes (plain node, tests).
  const fromEnv =
    typeof import.meta !== "undefined" && import.meta.env
      ? (import.meta.env.PUBLIC_BUILTIN_CATALOG_URL as string | undefined)
      : undefined;
  return fromEnv?.trim() || DEFAULT_BUILT_IN_URL;
}

function createBuiltInCatalog(): StacCatalog {
  return {
    id: BUILT_IN_CATALOG_ID,
    name: "Built-in Catalog",
    url: builtInCatalogUrl(),
    isDefault: true,
    builtIn: true,
  };
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

/**
 * Guarantee the built-in catalog exists (users may carry persisted
 * localStorage state that predates it) and keep its URL pinned to the
 * configured value. Runs on module init and is exported for tests.
 */
export function ensureBuiltInCatalog(): void {
  const builtIn = createBuiltInCatalog();
  const current = $catalogs.get();
  const existing = current.find((c) => c.id === BUILT_IN_CATALOG_ID);

  if (!existing) {
    $catalogs.set([builtIn, ...current]);
  } else if (existing.url !== builtIn.url || !existing.builtIn) {
    $catalogs.set(
      current.map((c) =>
        c.id === BUILT_IN_CATALOG_ID
          ? { ...c, url: builtIn.url, builtIn: true }
          : c,
      ),
    );
  }

  if (!$activeCatalogId.get()) {
    $activeCatalogId.set(BUILT_IN_CATALOG_ID);
  }
}

try {
  ensureBuiltInCatalog();
} catch {
  // Storage engine unavailable or read-only (non-browser runtime) — seeding
  // happens in the browser, the only place the catalog store is used.
}

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
    $catalogs.get().map((c) => {
      if (c.id !== id) return c;
      if (c.builtIn) {
        // The built-in catalog is URL-locked and keeps its identity.
        const { url: _url, id: _id, builtIn: _builtIn, ...allowed } = updates;
        return { ...c, ...allowed };
      }
      return { ...c, ...updates };
    }),
  );
}

export function removeCatalog(id: string) {
  if (id === BUILT_IN_CATALOG_ID) return;
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
