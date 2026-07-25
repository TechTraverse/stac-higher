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

import { getClient, query } from "@/lib/db/connection";
import {
  connectionDeleteImpact,
  softDeleteConnection,
} from "@/lib/connections/deletion";

const mockQuery = vi.mocked(query);
const mockGetClient = vi.mocked(getClient);
const CONN_ID = "3a9f1c2e-0000-4000-8000-000000000001";

function mockClient() {
  const client = {
    query: vi.fn(async (sql: string) =>
      sql.startsWith("UPDATE stac_higher.connections")
        ? { rowCount: 1, rows: [] }
        : { rowCount: 0, rows: [] },
    ),
    release: vi.fn(),
  };
  mockGetClient.mockResolvedValue(client as never);
  return client;
}

beforeEach(() => {
  mockQuery.mockReset();
  mockGetClient.mockReset();
});

describe("connectionDeleteImpact", () => {
  it("maps the counted blast radius from the three queries", async () => {
    mockQuery.mockImplementation(async (sql: string) => {
      if (sql.includes("GROUP BY direction")) {
        return { rows: [{ direction: "ingest", count: "2" }], rowCount: 1 } as never;
      }
      if (sql.includes("GROUP BY cc.collection_id")) {
        return {
          rows: [{ collection_id: "goes-west", items: "3" }],
          rowCount: 1,
        } as never;
      }
      return {
        rows: [{ ingest_files: "12", delivery_log: "4", connection_checks: "2" }],
        rowCount: 1,
      } as never;
    });
    const impact = await connectionDeleteImpact(CONN_ID);
    expect(impact).toEqual({
      associations: { ingest: 2, deliver: 0 },
      reference_items: [{ collection_id: "goes-west", items: 3 }],
      history: { ingest_files: 12, delivery_log: 4, connection_checks: 2 },
    });
  });
});

describe("softDeleteConnection", () => {
  it("removes reference items, then marks + soft-deletes in one transaction", async () => {
    mockQuery.mockImplementation(async (sql: string) => {
      if (sql.includes("SELECT DISTINCT")) {
        return {
          rows: [
            { collection_id: "goes-west", item_id: "item-1" },
            { collection_id: "goes-west", item_id: "item-2" },
          ],
          rowCount: 2,
        } as never;
      }
      return { rows: [], rowCount: 0 } as never;
    });
    const client = mockClient();

    const deleted = await softDeleteConnection(CONN_ID);
    expect(deleted).toBe(true);

    const itemDeletes = mockQuery.mock.calls.filter(([sql]) =>
      String(sql).includes("pgstac.delete_item"),
    );
    expect(itemDeletes.map((c) => c[1])).toEqual([
      ["item-1", "goes-west"],
      ["item-2", "goes-west"],
    ]);

    const txSql = client.query.mock.calls.map(([sql]) => String(sql));
    expect(txSql[0]).toBe("BEGIN");
    expect(txSql.at(-1)).toBe("COMMIT");
    const scrub = txSql.find((sql) =>
      sql.includes("UPDATE stac_higher.connections"),
    );
    // The soft delete must scrub every secret and leave listings via deleted_at.
    expect(scrub).toContain("deleted_at = now()");
    expect(scrub).toContain("credentials = NULL");
    expect(scrub).toContain("host_key = NULL");
    expect(scrub).toContain("host_key_pinned_at = NULL");
    expect(
      txSql.some((sql) => sql.includes("reference_removed_at = now()")),
    ).toBe(true);
    expect(
      txSql.some((sql) =>
        sql.includes("UPDATE stac_higher.collection_connections"),
      ),
    ).toBe(true);
    expect(client.release).toHaveBeenCalled();
  });

  it("tolerates a failing pgstac.delete_item (item already gone)", async () => {
    mockQuery.mockImplementation(async (sql: string) => {
      if (sql.includes("SELECT DISTINCT")) {
        return {
          rows: [{ collection_id: "goes-west", item_id: "gone" }],
          rowCount: 1,
        } as never;
      }
      if (String(sql).includes("pgstac.delete_item")) {
        throw new Error("not found");
      }
      return { rows: [], rowCount: 0 } as never;
    });
    mockClient();
    await expect(softDeleteConnection(CONN_ID)).resolves.toBe(true);
  });

  it("returns false when no live connection matched", async () => {
    mockQuery.mockResolvedValue({ rows: [], rowCount: 0 } as never);
    const client = mockClient();
    client.query.mockImplementation(async (sql: string) =>
      String(sql).startsWith("UPDATE stac_higher.connections")
        ? { rowCount: 0, rows: [] }
        : { rowCount: 0, rows: [] },
    );
    await expect(softDeleteConnection(CONN_ID)).resolves.toBe(false);
  });
});
