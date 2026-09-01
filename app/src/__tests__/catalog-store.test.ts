import { describe, it, expect, afterEach, vi } from "vitest";
import type { StacCatalog } from "@/stores/catalogStore";

const CATALOGS_KEY = "stac-catalogs";
/** Retired by UI-10 — kept here only to assert the store drops it. */
const RETIRED_ACTIVE_KEY = "stac-active-catalog";
const DEFAULT_BUILT_IN_URL = "http://localhost:8081";

/**
 * The store seeds the built-in catalog at module init, so each test resets
 * modules and re-imports it after arranging persisted / env state.
 *
 * The test environment's localStorage is non-functional (a proxy that rejects
 * writes), so we install an in-memory engine via setPersistentEngine — the
 * mechanism @nanostores/persistent provides for exactly this — before the
 * store module loads. `persisted` simulates pre-existing localStorage state.
 */
let engine: Record<string, string>;

async function importStore(persisted: Record<string, string> = {}) {
  vi.resetModules();
  engine = { ...persisted };
  const { setPersistentEngine } = await import("@nanostores/persistent");
  setPersistentEngine(engine, {
    addEventListener() {},
    removeEventListener() {},
  });
  return await import("@/stores/catalogStore");
}

function persistedCatalogs(catalogs: StacCatalog[]): Record<string, string> {
  return { [CATALOGS_KEY]: JSON.stringify(catalogs) };
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("catalogStore built-in seeding", () => {
  it("seeds the built-in catalog on fresh state", async () => {
    const store = await importStore();
    const catalogs = store.$catalogs.get();

    expect(catalogs).toHaveLength(1);
    expect(catalogs[0]).toMatchObject({
      id: store.BUILT_IN_CATALOG_ID,
      url: DEFAULT_BUILT_IN_URL,
      builtIn: true,
      isDefault: true,
    });
    expect(store.$builtInCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);
  });

  it("re-adds the built-in catalog when persisted state lacks it, keeping user catalogs", async () => {
    const store = await importStore({
      ...persistedCatalogs([
        { id: "user-1", name: "My API", url: "https://stac.example.com", isDefault: true },
      ]),
      [RETIRED_ACTIVE_KEY]: "user-1",
    });
    const catalogs = store.$catalogs.get();

    expect(catalogs).toHaveLength(2);
    expect(catalogs[0].id).toBe(store.BUILT_IN_CATALOG_ID);
    expect(catalogs[1].id).toBe("user-1");
    // A user catalog is never promoted to a product surface (UI-10).
    expect(store.$builtInCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);
  });

  it("updates a persisted built-in entry with a stale URL to the current default", async () => {
    const store = await importStore(
      persistedCatalogs([
        {
          id: "built-in",
          name: "Built-in Catalog",
          url: "http://localhost:8082",
          isDefault: true,
          builtIn: true,
        },
      ]),
    );
    const builtIn = store.$catalogs.get().find((c) => c.builtIn);

    expect(builtIn?.url).toBe(DEFAULT_BUILT_IN_URL);
    expect(store.$catalogs.get()).toHaveLength(1);
  });

  it("uses PUBLIC_BUILTIN_CATALOG_URL when set", async () => {
    vi.stubEnv("PUBLIC_BUILTIN_CATALOG_URL", "http://example.test:9999");

    const store = await importStore();
    const builtIn = store.$catalogs.get().find((c) => c.builtIn);

    expect(builtIn?.url).toBe("http://example.test:9999");
  });

  it("repairs a persisted built-in entry that lost its builtIn flag", async () => {
    const store = await importStore(
      persistedCatalogs([
        {
          id: "built-in",
          name: "Built-in Catalog",
          url: DEFAULT_BUILT_IN_URL,
          isDefault: true,
        },
      ]),
    );
    const builtIn = store.$catalogs.get()[0];

    expect(builtIn.builtIn).toBe(true);
  });
});

describe("catalogStore built-in protection", () => {
  it("removeCatalog is a no-op for the built-in catalog", async () => {
    const store = await importStore();

    store.removeCatalog(store.BUILT_IN_CATALOG_ID);

    expect(
      store.$catalogs.get().some((c) => c.id === store.BUILT_IN_CATALOG_ID),
    ).toBe(true);
  });

  it("updateCatalog cannot change the built-in catalog's url or id, but may rename it", async () => {
    const store = await importStore();

    store.updateCatalog(store.BUILT_IN_CATALOG_ID, {
      name: "Renamed",
      url: "https://evil.example.com",
      id: "other",
      builtIn: false,
    });

    const builtIn = store
      .$catalogs.get()
      .find((c) => c.id === store.BUILT_IN_CATALOG_ID);
    expect(builtIn).toBeDefined();
    expect(builtIn?.name).toBe("Renamed");
    expect(builtIn?.url).toBe(DEFAULT_BUILT_IN_URL);
    expect(builtIn?.builtIn).toBe(true);
  });

  it("still removes and updates regular catalogs", async () => {
    const store = await importStore();
    const id = store.addCatalog({
      name: "Other",
      url: "https://stac.example.com",
      isDefault: false,
    });

    store.updateCatalog(id, { url: "https://stac2.example.com" });
    expect(store.$catalogs.get().find((c) => c.id === id)?.url).toBe(
      "https://stac2.example.com",
    );

    store.removeCatalog(id);
    expect(store.$catalogs.get().some((c) => c.id === id)).toBe(false);
  });

  it("adding or removing a user catalog never moves the product surface", async () => {
    const store = await importStore();
    const id = store.addCatalog({
      name: "Other",
      url: "https://stac.example.com",
      isDefault: true,
    });

    // Even flagged isDefault, a user catalog is not what products read.
    expect(store.$builtInCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);

    store.removeCatalog(id);

    expect(store.$builtInCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);
  });

  it("getCatalogById resolves a browse route's catalog, or null", async () => {
    const store = await importStore();
    const id = store.addCatalog({
      name: "Other",
      url: "https://stac.example.com",
      isDefault: false,
    });

    expect(store.getCatalogById(id)?.name).toBe("Other");
    expect(store.getCatalogById("nope")).toBeNull();
  });
});

describe("catalogStore retired active-catalog key", () => {
  it("drops the orphaned stac-active-catalog key on init (UI-10)", async () => {
    // The cleanup goes through localStorage directly, not the persistent
    // engine, so stub the real thing for this test.
    const backing: Record<string, string> = { [RETIRED_ACTIVE_KEY]: "user-1" };
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => backing[k] ?? null,
      setItem: (k: string, v: string) => {
        backing[k] = v;
      },
      removeItem: (k: string) => {
        delete backing[k];
      },
    });

    await importStore();

    expect(backing[RETIRED_ACTIVE_KEY]).toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("no longer exports a global active-catalog selection", async () => {
    const store = await importStore();

    expect(store).not.toHaveProperty("$activeCatalogId");
    expect(store).not.toHaveProperty("$activeCatalog");
    expect(store).not.toHaveProperty("setActiveCatalog");
  });
});
