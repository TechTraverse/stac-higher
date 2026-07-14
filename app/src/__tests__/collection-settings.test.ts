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
      gcGraceDays: 30,
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
          gc_grace_days: 7,
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
      gcGraceDays: 7,
    });
  });
});
