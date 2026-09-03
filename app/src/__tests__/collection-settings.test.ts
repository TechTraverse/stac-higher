// @vitest-environment node
// (server-side db code — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));

import { query } from "@/lib/db/connection";
import {
  defaultCollectionSettings,
  getCollectionSettings,
  upsertCollectionSettings,
} from "@/lib/collections/settings";

const mockQuery = vi.mocked(query);

beforeEach(() => {
  mockQuery.mockReset();
});

describe("collection settings defaults (ADR 0003)", () => {
  it("pre-existing / unconfigured collections are unowned and public", () => {
    expect(defaultCollectionSettings("landsat-c2l2")).toEqual({
      collectionId: "landsat-c2l2",
      groupId: null, // unowned → visible to all, mutable by any operator/admin
      externallyWritable: false,
      retentionDays: null, // keep forever
      retentionMaxItems: null, // no count cap (W-2)
      gcGraceDays: 30,
      servingEnabled: false,
      archived: false,
    });
  });

  it("applies the defaults on read when no row exists (sparse table)", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    const settings = await getCollectionSettings("landsat-c2l2");
    expect(settings).toEqual(defaultCollectionSettings("landsat-c2l2"));
  });

  it("returns the stored row when one exists", async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [
        {
          collection_id: "goes-abi",
          group_id: "weather",
          externally_writable: true,
          retention_days: 14,
          retention_max_items: 24,
          gc_grace_days: 7,
          archived: true,
          serving_enabled: false,
        },
      ],
      rowCount: 1,
    } as never);
    const settings = await getCollectionSettings("goes-abi");
    expect(settings).toEqual({
      collectionId: "goes-abi",
      groupId: "weather",
      externallyWritable: true,
      retentionDays: 14,
      retentionMaxItems: 24,
      gcGraceDays: 7,
      archived: true,
      servingEnabled: false,
    });
  });
});

describe("upsertCollectionSettings (M2-E)", () => {
  it("upserts the full document and re-reads the stored row", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [], rowCount: 1 } as never) // upsert
      .mockResolvedValueOnce({
        rows: [
          {
            collection_id: "goes-abi",
            group_id: "weather",
            externally_writable: false,
            retention_days: 30,
            retention_max_items: 1000,
            gc_grace_days: 7,
            archived: false,
          },
        ],
        rowCount: 1,
      } as never);

    const settings = await upsertCollectionSettings("goes-abi", {
      groupId: "weather",
      externallyWritable: false,
      retentionDays: 30,
      retentionMaxItems: 1000,
      gcGraceDays: 7,
      archived: false,
      servingEnabled: false,
    });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("ON CONFLICT (collection_id) DO UPDATE");
    expect(sql).toContain("retention_max_items = EXCLUDED.retention_max_items");
    expect(params).toEqual(["goes-abi", "weather", false, 30, 1000, 7, false, false]);
    expect(settings.retentionDays).toBe(30);
    expect(settings.retentionMaxItems).toBe(1000);
  });
});
