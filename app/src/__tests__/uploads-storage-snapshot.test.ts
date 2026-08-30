// @vitest-environment node
/**
 * `bindPriorItemSnapshot` — the ledger half of the §4.3 prior_item snapshot.
 * First-write-wins is enforced IN SQL (`… AND prior_item IS NULL`), so it
 * holds even across concurrent brokered PUTs; this suite pins that predicate
 * and the jsonb serialization (the R2 first-write-wins guard's mechanism).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import { bindPriorItemSnapshot } from "@/lib/uploads/storage";

const mockQuery = vi.mocked(query);

beforeEach(() => {
  mockQuery.mockReset().mockResolvedValue({ rows: [], rowCount: 0 } as never);
});

describe("bindPriorItemSnapshot", () => {
  it("updates prior_item only when it is still NULL (first write wins)", async () => {
    const item = { type: "Feature", id: "i1" };
    await bindPriorItemSnapshot("up-1", item);
    expect(mockQuery).toHaveBeenCalledTimes(1);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/UPDATE stac_higher\.staged_uploads/);
    expect(sql).toMatch(/SET prior_item = \$2::jsonb/);
    expect(sql).toMatch(/prior_item IS NULL/);
    expect(params).toEqual(["up-1", JSON.stringify(item)]);
  });

  it("is a silent no-op when a snapshot already exists (rowCount 0)", async () => {
    // The zero-rowCount outcome must not throw — a second PUT in the same
    // session proceeds; the FIRST snapshot stays the restore point.
    await expect(
      bindPriorItemSnapshot("up-1", { id: "i1" }),
    ).resolves.toBeUndefined();
  });
});
