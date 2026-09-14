// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchTipgCollections } from "@/lib/serving/queries";
import { servingKeys } from "@/lib/query/keys";
import { tipgCollectionsUrl } from "@/lib/serving/urls";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("fetchTipgCollections", () => {
  it("lists tipg's collections without credentials", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        collections: [
          { id: "public.st_hexagongrid", title: "public.st_hexagongrid", links: [] },
          { id: "public.roads", title: "Roads", description: "OSM roads", links: [] },
        ],
      }),
    });
    const list = await fetchTipgCollections();
    expect(list).toEqual([
      { id: "public.st_hexagongrid", title: "public.st_hexagongrid", description: undefined },
      { id: "public.roads", title: "Roads", description: "OSM roads" },
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(tipgCollectionsUrl());
    expect(init).toMatchObject({ credentials: "omit" });
  });

  it("throws on a non-2xx so the query lands in error, quietly", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) });
    await expect(fetchTipgCollections()).rejects.toThrow(/503/);
  });

  it("throws when the body carries no collections array", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ nope: true }) });
    await expect(fetchTipgCollections()).rejects.toThrow(/collections/);
  });
});

describe("servingKeys.tipgCollections", () => {
  it("is namespaced under serving and catalog-agnostic", () => {
    expect(servingKeys.tipgCollections()).toEqual(["serving", "tipg-collections"]);
  });
});
