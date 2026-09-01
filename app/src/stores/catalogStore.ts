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

/**
 * Retired by UI-10: there is no global "active catalog" any more. Product
 * surfaces read `$builtInCatalog`; the catalog browser and search carry their
 * own selection (route param / local state). The key is dropped rather than
 * left inert so a returning browser does not keep dead state around forever.
 */
const RETIRED_ACTIVE_CATALOG_KEY = "stac-active-catalog";

function dropRetiredActiveCatalogKey(): void {
  try {
    localStorage.removeItem(RETIRED_ACTIVE_CATALOG_KEY);
  } catch {
    // No storage engine (non-browser runtime) — nothing to clean up.
  }
}

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

  dropRetiredActiveCatalogKey();
}

try {
  ensureBuiltInCatalog();
} catch {
  // Storage engine unavailable or read-only (non-browser runtime) — seeding
  // happens in the browser, the only place the catalog store is used.
}

/**
 * The platform's own catalog. Every product surface (home, /collections, items,
 * global search, the stack-status footer) reads THIS and nothing else — a
 * product is a built-in-catalog collection by definition, and its writes must
 * go through the ADR 0008 BFF. Browsing an arbitrary catalog is a separate
 * surface (`/catalogs/[catalogId]/collections`, `/search`) that carries its own
 * selection.
 */
export const $builtInCatalog = computed([$catalogs], (catalogs) => {
  return catalogs.find((c) => c.id === BUILT_IN_CATALOG_ID) ?? null;
});

/** Resolve a catalog by id — the catalog browser's route param. */
export function getCatalogById(id: string): StacCatalog | null {
  return $catalogs.get().find((c) => c.id === id) ?? null;
}

export function addCatalog(catalog: Omit<StacCatalog, "id">) {
  const id = crypto.randomUUID();
  const current = $catalogs.get();
  const isFirst = current.length === 0;
  $catalogs.set([
    ...current,
    { ...catalog, id, isDefault: isFirst || catalog.isDefault },
  ]);
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
  $catalogs.set(current.filter((c) => c.id !== id));
}
