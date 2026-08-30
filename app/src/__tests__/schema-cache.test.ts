// @vitest-environment node
// (server-side schema cache — no DOM involved)
/**
 * I-67: getOrFetchSchema serves a stale cached copy when the upstream schema
 * host is unreachable (offline) instead of failing the edit form.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/http/safe-fetch", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/http/safe-fetch")>();
  return { ...original, safeFetch: vi.fn() };
});

import { query } from "@/lib/db/connection";
import { safeFetch } from "@/lib/http/safe-fetch";
import { getOrFetchSchema } from "@/lib/extensions/schema-cache";

const mockQuery = vi.mocked(query);
const mockFetch = vi.mocked(safeFetch);

const URL = "https://stac-extensions.github.io/projection/v2.0.0/schema.json";
const SCHEMA = { title: "Projection Extension" };

beforeEach(() => {
  mockQuery.mockReset();
  mockFetch.mockReset();
});

function queryResult(rows: unknown[]) {
  return { rows, rowCount: rows.length } as never;
}

describe("getOrFetchSchema", () => {
  it("returns the fresh cached schema without fetching", async () => {
    mockQuery.mockResolvedValueOnce(queryResult([{ schema: SCHEMA }]));
    await expect(getOrFetchSchema(URL)).resolves.toEqual(SCHEMA);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("fetches, caches, and returns on a cache miss", async () => {
    mockQuery.mockResolvedValueOnce(queryResult([])); // fresh-cache miss
    mockFetch.mockResolvedValueOnce({
      status: 200,
      body: new TextEncoder().encode(JSON.stringify(SCHEMA)),
    } as never);
    mockQuery.mockResolvedValueOnce(queryResult([])); // upsert

    await expect(getOrFetchSchema(URL)).resolves.toEqual(SCHEMA);
    expect(mockQuery.mock.calls[1]?.[0]).toContain("INSERT INTO stac_higher.schema_cache");
  });

  it("falls back to an expired cache row when the fetch throws (offline)", async () => {
    mockQuery.mockResolvedValueOnce(queryResult([])); // fresh-cache miss
    mockFetch.mockRejectedValueOnce(new Error("network down"));
    mockQuery.mockResolvedValueOnce(queryResult([{ schema: SCHEMA }])); // stale row

    await expect(getOrFetchSchema(URL)).resolves.toEqual(SCHEMA);
    // The stale lookup must not filter on expires_at.
    expect(mockQuery.mock.calls[1]?.[0]).not.toContain("expires_at");
  });

  it("falls back to an expired cache row on an upstream error status", async () => {
    mockQuery.mockResolvedValueOnce(queryResult([])); // fresh-cache miss
    mockFetch.mockResolvedValueOnce({ status: 503, body: new Uint8Array() } as never);
    mockQuery.mockResolvedValueOnce(queryResult([{ schema: SCHEMA }])); // stale row

    await expect(getOrFetchSchema(URL)).resolves.toEqual(SCHEMA);
  });

  it("rethrows when the fetch fails and no cached copy exists at all", async () => {
    mockQuery.mockResolvedValueOnce(queryResult([])); // fresh-cache miss
    mockFetch.mockRejectedValueOnce(new Error("network down"));
    mockQuery.mockResolvedValueOnce(queryResult([])); // no stale row either

    await expect(getOrFetchSchema(URL)).rejects.toThrow("network down");
  });
});
