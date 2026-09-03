// @vitest-environment node
/** M2-F app-side asset_gc marking (ADR 0011). */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
}));

import { query } from "@/lib/db/connection";
import { getCollectionSettings } from "@/lib/collections/settings";
import { gcPrefix, markAssetGc, markAssetGcTolerant } from "@/lib/gc/marks";

const mockQuery = vi.mocked(query);

beforeEach(() => {
  mockQuery.mockReset().mockResolvedValue({ rows: [], rowCount: 1 } as never);
  vi.mocked(getCollectionSettings)
    .mockReset()
    .mockResolvedValue({
      collectionId: "c1",
      groupId: null,
      externallyWritable: false,
      retentionDays: null,
      retentionMaxItems: null,
      gcGraceDays: 7,
      archived: false,
      servingEnabled: false,
    });
});

describe("gcPrefix", () => {
  it("builds §5.3 prefixes for item and collection scope", () => {
    expect(gcPrefix("c1", "i1")).toBe("assets/c1/i1/");
    expect(gcPrefix("c1", null)).toBe("assets/c1/");
  });

  it("rejects traversal segments", () => {
    expect(() => gcPrefix("..", null)).toThrow();
    expect(() => gcPrefix("c1", "a/b")).toThrow();
  });
});

describe("markAssetGc", () => {
  it("inserts an open mark with the collection's grace window", async () => {
    await markAssetGc({ collectionId: "c1", itemId: "i1", reason: "item_delete" });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("INSERT INTO stac_higher.asset_gc");
    expect(sql).toContain("ON CONFLICT (object_key) WHERE collected_at IS NULL");
    expect(params).toEqual(["assets/c1/i1/", "c1", "i1", "item_delete", 7]);
  });

  it("tolerant wrapper swallows failures (the delete already happened)", async () => {
    mockQuery.mockRejectedValue(new Error("db down"));
    await expect(
      markAssetGcTolerant({
        collectionId: "c1",
        itemId: null,
        reason: "collection_delete",
      }),
    ).resolves.toBeUndefined();
  });
});
