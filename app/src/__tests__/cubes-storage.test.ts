// @vitest-environment node
/** Z-2: cube sink persistence — SQL shape and row mapping. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import {
  collectionHasItem,
  cubeLedgerSummary,
  cubeSinksOnCollectionDelete,
  cubeSinksOnCollectionDeleteTolerant,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  referenceIngestSources,
  setCubeSinkEnabled,
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
    expect(mockQuery.mock.calls[0][1]?.[5]).toBe(false); // no source change: nothing reset
  });

  it("a source change resets prefixes, last_error and the ledger in the same statement", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, created: false }]));
    await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc-2",
      config: config as never, enabled: true, createdBy: "user-1", resetSourceState: true,
    });
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toMatch(/source_prefixes = CASE WHEN \$6::boolean THEN '\{\}'::text\[\]/);
    expect(sql).toMatch(/last_error = CASE WHEN \$6::boolean THEN NULL/);
    expect(sql).toMatch(/DELETE FROM stac_higher\.cube_appends a USING up\s+WHERE \$6::boolean AND a\.cube_sink_id = up\.id/);
    expect(mockQuery.mock.calls[0][1]?.[5]).toBe(true);
  });

  it("on collection delete: deletes the sink whose CUBE it was, disables those it SOURCED", async () => {
    mockQuery.mockResolvedValueOnce(result([{ deleted: "1", disabled: "2" }]));
    expect(await cubeSinksOnCollectionDelete("c1")).toEqual({ deleted: 1, disabled: 2 });
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toMatch(/DELETE FROM stac_higher\.cube_sinks WHERE cube_collection_id = \$1/);
    expect(sql).toMatch(/SET enabled = false, last_error = 'source_collection_deleted'/);
    expect(sql).toMatch(/WHERE source_collection_id = \$1/);
  });

  it("the tolerant hook swallows and logs a DB error", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    mockQuery.mockRejectedValueOnce(new Error("db down"));
    await expect(cubeSinksOnCollectionDeleteTolerant("c1")).resolves.toBeUndefined();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("deletes a sink only while no repository exists", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    expect(await deleteCubeSink("goes19-c13-cube")).toBe(false);
    expect(mockQuery.mock.calls[0][0]).toMatch(/AND last_snapshot_id IS NULL/);
  });

  it("enabling clears a stale last_error; disabling keeps it", async () => {
    mockQuery.mockResolvedValueOnce(result([row]));
    await setCubeSinkEnabled("goes19-c13-cube", true);
    expect(mockQuery.mock.calls[0][0]).toMatch(/last_error = CASE WHEN \$2 THEN NULL ELSE last_error END/);
  });

  it("summarises the ledger with zero-filled counts and the last 20 rows", async () => {
    mockQuery
      .mockResolvedValueOnce(result([{ status: "appended", count: "3" }]))
      .mockResolvedValueOnce(result([]));
    const summary = await cubeLedgerSummary(SINK_ID);
    expect(summary.counts).toEqual({ pending: 0, appended: 3, skipped: 0, failed: 0 });
    expect(mockQuery.mock.calls[1][0]).toContain("LIMIT 20");
  });

  it("checks for one item in pgstac by collection and id", async () => {
    mockQuery.mockResolvedValueOnce(result([{ exists: true }]));
    expect(await collectionHasItem("cube", "_cube")).toBe(true);
    expect(mockQuery.mock.calls[0][0]).toContain("pgstac.items WHERE collection = $1 AND id = $2");
    expect(mockQuery.mock.calls[0][1]).toEqual(["cube", "_cube"]);
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
