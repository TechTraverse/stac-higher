/**
 * stacFetch BFF routing (ADR 0008): built-in-catalog WRITES go to
 * /api/catalog/*; reads and external catalogs keep their existing paths.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const EXTERNAL = {
  id: "user-1",
  name: "External API",
  url: "https://stac.example.com",
  isDefault: false,
};

async function importClient() {
  vi.resetModules();
  const engine: Record<string, string> = {
    "stac-catalogs": JSON.stringify([EXTERNAL]),
  };
  const { setPersistentEngine } = await import("@nanostores/persistent");
  setPersistentEngine(engine, {
    addEventListener() {},
    removeEventListener() {},
  });
  // The store module seeds/prepends the built-in catalog on import.
  await import("@/stores/catalogStore");
  return await import("@/lib/stac-api/client");
}

function okFetch() {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}

let fetchMock: ReturnType<typeof okFetch>;

beforeEach(() => {
  fetchMock = okFetch();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("stacFetch routing (ADR 0008)", () => {
  it("routes built-in-catalog writes through /api/catalog", async () => {
    const { stacFetch } = await importClient();
    await stacFetch("/collections", { method: "POST", body: { id: "c1" } });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/catalog/collections",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("routes built-in item updates through /api/catalog", async () => {
    const { stacFetch } = await importClient();
    await stacFetch("/collections/c1/items/i1", {
      method: "PUT",
      body: { id: "i1" },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/catalog/collections/c1/items/i1",
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it("leaves built-in-catalog reads on the direct path", async () => {
    const { stacFetch } = await importClient();
    await stacFetch("/collections");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8081/collections",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("REFUSES an external-catalog write instead of sending it", async () => {
    // UI-10 / I-89: external catalogs are browsed read-only. A write aimed at
    // one would leave the browser without passing the BFF — unaudited and
    // un-RBAC'd — so the client refuses rather than routes.
    const { stacFetch } = await importClient();
    await expect(
      stacFetch("/collections", {
        method: "POST",
        body: { id: "c1" },
        endpointUrl: EXTERNAL.url,
      }),
    ).rejects.toThrow(/only allowed against the built-in catalog/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses every write verb, and names the one it refused", async () => {
    const { stacFetch } = await importClient();
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      await expect(
        stacFetch("/collections/c1", { method, endpointUrl: EXTERNAL.url }),
      ).rejects.toThrow(new RegExp(`Refusing to ${method}`));
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still allows POST /search against an external catalog — a read that speaks POST", async () => {
    const { stacFetch } = await importClient();
    await stacFetch("/search", {
      method: "POST",
      body: { limit: 1 },
      endpointUrl: EXTERNAL.url,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://stac.example.com/search",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("does not route POST /search through the BFF for the built-in catalog either", async () => {
    const { stacFetch } = await importClient();
    await stacFetch("/search", { method: "POST", body: { limit: 1 } });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8081/search",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("routes built-in writes via endpointUrl override too", async () => {
    // An explicit built-in endpoint still goes through the BFF.
    const { stacFetch } = await importClient();
    await stacFetch("/collections/c1", {
      method: "DELETE",
      endpointUrl: "http://localhost:8081",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/catalog/collections/c1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
