// @vitest-environment node
/** Z-2: cube sink persistence — SQL shape and row mapping. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import {
  cubeLedgerSummary,
  deleteCubeSinksForCollection,
  deleteCubeSinksForCollectionTolerant,
  existingCollections,
  getCubeSink,
  isCubeCollection,
  referenceIngestSources,
  upsertCubeSink,
} from "@/lib/cubes/storage";

const mockQuery = vi.mocked(query);
const SINK_ID = "3a9f1c2e-0000-4000-8000-0000000000c1";
const config = { parser: "hdf5", append_dim: "t", variables: ["CMI"], loadable_variables: ["t"], asset_key: "cube", on_late: "skip" } as const;

const row = {
  id: SINK_ID, source_collection_id: "goes19-cmipc", cube_collection_id: "goes19-c13-cube",
  enabled: true, config, source_prefixes: [], last_snapshot_id: null, last_appended_at: null,
  last_maintained_at: null, last_maintenance: null, last_error: null, created_by: "user-1",
  created_at: new Date("2026-10-05T00:00:00Z"), updated_at: new Date("2026-10-05T01:00:00Z"),
};

const result = (rows: unknown[]) => ({ rows, rowCount: rows.length }) as never;

beforeEach(() => mockQuery.mockReset());

describe("cube sink storage", () => {
  it("maps timestamps to ISO strings", async () => {
    mockQuery.mockResolvedValueOnce(result([row]));
    const sink = await getCubeSink("goes19-c13-cube");
    expect(sink?.created_at).toBe("2026-10-05T00:00:00.000Z");
    expect(mockQuery.mock.calls[0][1]).toEqual(["goes19-c13-cube"]);
  });

  it("upserts on cube_collection_id and reports creation via xmax", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, created: true }]));
    const out = await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc",
      config: config as never, enabled: true, createdBy: "user-1",
    });
    expect(out.created).toBe(true);
    expect(out.sink).not.toHaveProperty("created");
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toContain("ON CONFLICT (cube_collection_id) DO UPDATE");
    expect(sql).toContain("(xmax = 0) AS created");
    expect(sql).not.toMatch(/created_by\s*=\s*EXCLUDED/); // the creator survives a replace
  });

  it("deletes sinks naming a collection as source OR cube", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 2 } as never);
    expect(await deleteCubeSinksForCollection("c1")).toBe(2);
    expect(mockQuery.mock.calls[0][0]).toMatch(/source_collection_id = \$1 OR cube_collection_id = \$1/);
  });

  it("the tolerant delete swallows and logs a DB error", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    mockQuery.mockRejectedValueOnce(new Error("db down"));
    await expect(deleteCubeSinksForCollectionTolerant("c1")).resolves.toBeUndefined();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("isCubeCollection checks any sink, enabled or not", async () => {
    mockQuery.mockResolvedValueOnce(result([{ exists: true }]));
    expect(await isCubeCollection("goes19-c13-cube")).toBe(true);
    expect(mockQuery.mock.calls[0][0]).not.toContain("enabled");
  });

  it("summarises the ledger with zero-filled counts and the last 20 rows", async () => {
    mockQuery
      .mockResolvedValueOnce(result([{ status: "appended", count: "3" }]))
      .mockResolvedValueOnce(result([]));
    const summary = await cubeLedgerSummary(SINK_ID);
    expect(summary.counts).toEqual({ pending: 0, appended: 3, skipped: 0, failed: 0 });
    expect(mockQuery.mock.calls[1][0]).toContain("LIMIT 20");
  });

  it("checks collection existence in pgstac in one query", async () => {
    mockQuery.mockResolvedValueOnce(result([{ id: "a" }]));
    expect(await existingCollections(["a", "b"])).toEqual(new Set(["a"]));
    expect(mockQuery.mock.calls[0][0]).toContain("pgstac.collections");
  });

  it("lists only enabled, live, reference-mode ingest associations with their anonymity", async () => {
    mockQuery.mockResolvedValueOnce(result([{ association_id: "a1", connection_id: "c1", anonymous: false }]));
    const out = await referenceIngestSources("goes19-cmipc");
    expect(out).toEqual([{ association_id: "a1", connection_id: "c1", anonymous: false }]);
    const sql = mockQuery.mock.calls[0][0] as string;
    for (const clause of ["cc.direction = 'ingest'", "cc.enabled", "cc.deleted_at IS NULL", "c.deleted_at IS NULL", "storage_mode' = 'reference'"]) {
      expect(sql).toContain(clause);
    }
  });
});
