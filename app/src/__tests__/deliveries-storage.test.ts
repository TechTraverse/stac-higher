// @vitest-environment node
// (server-side db code — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import {
  listDeliveries,
  redeliverDeadRow,
} from "@/lib/associations/deliveries";

const mockQuery = vi.mocked(query);

const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const DELIVERY_ID = "3a9f1c2e-0000-4000-8000-0000000000d1";

const dbRow = {
  id: DELIVERY_ID,
  association_id: ASSOC_ID,
  item_id: "item-1",
  status: "failed" as const,
  attempts: 0,
  bytes: "2048",
  error: "connection refused",
  next_attempt_at: new Date("2026-07-25T02:00:00Z"),
  delivered_at: null,
  created_at: new Date("2026-07-25T00:00:00Z"),
  updated_at: new Date("2026-07-25T01:00:00Z"),
};

beforeEach(() => {
  mockQuery.mockReset();
});

describe("listDeliveries", () => {
  it("returns recent rows plus zero-filled per-status counts", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never)
      .mockResolvedValueOnce({
        rows: [
          { status: "delivered", count: "7" },
          { status: "dead", count: "2" },
        ],
        rowCount: 2,
      } as never);

    const listing = await listDeliveries(ASSOC_ID);

    expect(listing.counts).toEqual({
      pending: 0,
      delivering: 0,
      delivered: 7,
      failed: 0,
      dead: 2,
    });
    expect(listing.deliveries).toHaveLength(1);
    // bigint bytes come back as strings; the API shape is a number.
    expect(listing.deliveries[0].bytes).toBe(2048);
    expect(listing.deliveries[0].next_attempt_at).toBe(
      "2026-07-25T02:00:00.000Z",
    );

    const [listSql, listParams] = mockQuery.mock.calls[0];
    expect(listSql).toMatch(/ORDER BY updated_at DESC/);
    expect(listParams).toEqual([ASSOC_ID, 20]);
    const [countSql] = mockQuery.mock.calls[1];
    expect(countSql).toMatch(/GROUP BY status/);
  });
});

describe("redeliverDeadRow", () => {
  it("flips only a dead row back to failed with a due next_attempt_at and a fresh cycle", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);

    const result = await redeliverDeadRow(ASSOC_ID, DELIVERY_ID);

    expect(result?.attempts).toBe(0);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/SET status = 'failed', attempts = 0/);
    expect(sql).toMatch(/next_attempt_at = now\(\)/);
    // The status guard is what makes the flip concurrency-safe.
    expect(sql).toMatch(/AND status = 'dead'/);
    expect(params).toEqual([DELIVERY_ID, ASSOC_ID]);
  });

  it("returns null when the row is missing or not dead", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    expect(await redeliverDeadRow(ASSOC_ID, DELIVERY_ID)).toBeNull();
  });
});
