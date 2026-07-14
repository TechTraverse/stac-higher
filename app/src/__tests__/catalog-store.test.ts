import { describe, it, expect, afterEach, vi } from "vitest";
import type { StacCatalog } from "@/stores/catalogStore";

const CATALOGS_KEY = "stac-catalogs";
const ACTIVE_KEY = "stac-active-catalog";
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
  it("seeds the built-in catalog on fresh state and makes it active", async () => {
    const store = await importStore();
    const catalogs = store.$catalogs.get();

    expect(catalogs).toHaveLength(1);
    expect(catalogs[0]).toMatchObject({
      id: store.BUILT_IN_CATALOG_ID,
      url: DEFAULT_BUILT_IN_URL,
      builtIn: true,
      isDefault: true,
    });
    expect(store.$activeCatalogId.get()).toBe(store.BUILT_IN_CATALOG_ID);
    expect(store.$activeCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);
  });

  it("re-adds the built-in catalog when persisted state lacks it, keeping user catalogs and selection", async () => {
    const store = await importStore({
      ...persistedCatalogs([
        { id: "user-1", name: "My API", url: "https://stac.example.com", isDefault: true },
      ]),
      [ACTIVE_KEY]: "user-1",
    });
    const catalogs = store.$catalogs.get();

    expect(catalogs).toHaveLength(2);
    expect(catalogs[0].id).toBe(store.BUILT_IN_CATALOG_ID);
    expect(catalogs[1].id).toBe("user-1");
    // Persisted selection is untouched.
    expect(store.$activeCatalogId.get()).toBe("user-1");
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

  it("falls back to the built-in catalog when the active catalog is removed", async () => {
    const store = await importStore();
    const id = store.addCatalog({
      name: "Other",
      url: "https://stac.example.com",
      isDefault: true,
    });
    expect(store.$activeCatalogId.get()).toBe(id);

    store.removeCatalog(id);

    expect(store.$activeCatalog.get()?.id).toBe(store.BUILT_IN_CATALOG_ID);
  });
});
