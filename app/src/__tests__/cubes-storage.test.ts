// @vitest-environment node
/** Z-2: cube sink persistence — SQL shape and row mapping. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { query } from "@/lib/db/connection";
import {
  collectionHasItem,
  cubeLedgerSummary,
  cubeSinksOnCollectionDelete,
  cubeSinksOnCollectionDeleteTolerant,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  listReferenceSourceCollections,
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
  version: "2026-10-05 01:00:00.123456+00",
};
const NEW = { version: null, hasRepository: false } as const;

const result = (rows: unknown[]) => ({ rows, rowCount: rows.length }) as never;

beforeEach(() => mockQuery.mockReset());

const maintenanceDocs = (
  JSON.parse(
    readFileSync(
      fileURLToPath(new URL("../../../tests/contract-fixtures/cube-maintenance-summary.json", import.meta.url)),
      "utf8",
    ),
  ) as { documents: Record<string, { sizes: Record<string, { objects: number; bytes: number }> }> }
).documents;

describe("cube sink storage", () => {
  it("types last_maintenance from the stored summary", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, last_maintenance: maintenanceDocs.windowed }]));
    const sink = await getCubeSink("goes19-c13-cube");
    expect(sink!.last_maintenance?.sizes?.chunks).toEqual(maintenanceDocs.windowed.sizes.chunks);
  });

  it("an unreadable stored summary is null, never a 500", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      mockQuery.mockResolvedValueOnce(result([{ ...row, last_maintenance: { status: "degraded" } }]));
      expect((await getCubeSink("goes19-c13-cube"))!.last_maintenance).toBeNull();
      expect(warn).toHaveBeenCalledTimes(1);

      warn.mockClear();
      mockQuery.mockResolvedValueOnce(result([{ ...row, last_maintenance: null }]));
      expect((await getCubeSink("goes19-c13-cube"))!.last_maintenance).toBeNull();
      expect(warn).not.toHaveBeenCalled();
    } finally {
      warn.mockRestore();
    }
  });

  it("maps timestamps to ISO strings and carries the full-precision row version", async () => {
    mockQuery.mockResolvedValueOnce(result([row]));
    const sink = await getCubeSink("goes19-c13-cube");
    expect(sink?.created_at).toBe("2026-10-05T00:00:00.000Z");
    // The ISO updated_at drops microseconds; the version must not.
    expect(sink?.version).toBe("2026-10-05 01:00:00.123456+00");
    expect(mockQuery.mock.calls[0][0]).toContain("updated_at::text AS version");
    expect(mockQuery.mock.calls[0][1]).toEqual(["goes19-c13-cube"]);
  });

  it("upserts on cube_collection_id and reports creation via xmax", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, created: true }]));
    const out = await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc",
      config: config as never, enabled: true, createdBy: "user-1", expected: NEW,
    });
    expect(out?.created).toBe(true);
    expect(out?.sink).not.toHaveProperty("created");
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
      expected: { version: row.version, hasRepository: false },
    });
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toMatch(/source_prefixes = CASE WHEN \$6::boolean THEN '\{\}'::text\[\]/);
    expect(sql).toMatch(/last_error = CASE WHEN \$6::boolean THEN NULL/);
    expect(sql).toMatch(/DELETE FROM stac_higher\.cube_appends a USING up\s+WHERE \$6::boolean AND a\.cube_sink_id = up\.id/);
    expect(mockQuery.mock.calls[0][1]?.[5]).toBe(true);
  });

  it("guards the replace on the row version AND on whether a repository exists (#98)", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, created: false }]));
    await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc",
      config: config as never, enabled: true, createdBy: "user-1",
      expected: { version: row.version, hasRepository: true },
    });
    const sql = mockQuery.mock.calls[0][0] as string;
    // Not the snapshot id: appends move it every few minutes, but the lock
    // only cares whether a repository exists, which flips once.
    expect(sql).not.toMatch(/last_snapshot_id IS NOT DISTINCT FROM/);
    expect(sql).toMatch(
      /WHERE cube_sinks\.updated_at IS NOT DISTINCT FROM \$7::timestamptz\s+AND \(cube_sinks\.last_snapshot_id IS NOT NULL\) = \$8::boolean/,
    );
    expect(mockQuery.mock.calls[0][1]?.slice(6)).toEqual([row.version, true]);
  });

  it("returns null when the guard refused, so nothing was written or purged", async () => {
    // DO UPDATE … WHERE false → no row from `up`, and `purge` joins on `up`.
    mockQuery.mockResolvedValueOnce(result([]));
    const out = await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc-2",
      config: config as never, enabled: true, createdBy: "user-1",
      resetSourceState: true, expected: NEW,
    });
    expect(out).toBeNull();
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
    await setCubeSinkEnabled("goes19-c13-cube", true, "2026-10-05 01:00:00.123456+00");
    expect(mockQuery.mock.calls[0][0]).toMatch(/last_error = CASE WHEN \$2 THEN NULL ELSE last_error END/);
  });

  it("the toggle applies only at the version it read", async () => {
    mockQuery.mockResolvedValueOnce(result([row]));
    await setCubeSinkEnabled("goes19-c13-cube", true, "2026-10-05 01:00:00.123456+00");
    expect(mockQuery.mock.calls[0][0]).toContain("AND updated_at = $3::timestamptz");
    expect(mockQuery.mock.calls[0][1]).toEqual(["goes19-c13-cube", true, "2026-10-05 01:00:00.123456+00"]);
    mockQuery.mockResolvedValueOnce(result([]));
    expect(await setCubeSinkEnabled("goes19-c13-cube", false, "stale")).toBeNull();
  });

  it("orders recent ledger rows by activity", async () => {
    mockQuery.mockResolvedValueOnce(result([])).mockResolvedValueOnce(result([]));
    await cubeLedgerSummary(SINK_ID);
    const sql = mockQuery.mock.calls[1][0] as string;
    expect(sql).toContain("ORDER BY updated_at DESC, id DESC LIMIT 20");
    expect(sql).not.toContain("ORDER BY item_datetime");
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
    mockQuery.mockResolvedValueOnce(result([{ association_id: "a1", connection_id: "c1", protocol: "s3", anonymous: false }]));
    const out = await referenceIngestSources("goes19-cmipc");
    expect(out).toEqual([{ association_id: "a1", connection_id: "c1", protocol: "s3", anonymous: false }]);
    const sql = mockQuery.mock.calls[0][0] as string;
    for (const clause of [
      "c.protocol",
      "c.protocol = 's3' AND COALESCE(c.config->'anonymous' = 'true'::jsonb, false)","cc.direction = 'ingest'", "cc.enabled", "cc.deleted_at IS NULL", "c.deleted_at IS NULL", "storage_mode' = 'reference'"]) {
      expect(sql).toContain(clause);
    }
  });
});

describe("listReferenceSourceCollections", () => {
  const assoc = (collection_id: string, title: string | null, id: string, protocol: string, anonymous: boolean) => ({
    collection_id, title, association_id: id, connection_id: `c-${id}`, protocol, anonymous,
  });

  it("filters by the caller's groups, keeping unowned collections", async () => {
    mockQuery.mockResolvedValue(result([]));
    await listReferenceSourceCollections(["earth-observation"]);
    await listReferenceSourceCollections(null);
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toContain("LEFT JOIN stac_higher.collection_settings s ON s.collection_id = cc.collection_id");
    expect(sql).toContain("($1::text[] IS NULL OR s.group_id IS NULL OR s.group_id = ANY($1::text[]))");
    expect(mockQuery.mock.calls[0][1]).toEqual([["earth-observation"]]);
    expect(mockQuery.mock.calls[1][1]).toEqual([null]);
  });

  it("joins live pgstac collections for the title and reuses the reference clauses", async () => {
    mockQuery.mockResolvedValueOnce(result([]));
    await listReferenceSourceCollections(null);
    const sql = mockQuery.mock.calls[0][0] as string;
    for (const clause of [
      "JOIN pgstac.collections pc ON pc.id = cc.collection_id",
      "pc.content->>'title'",
      "cc.direction = 'ingest'",
      "cc.enabled",
      "cc.deleted_at IS NULL",
      "c.deleted_at IS NULL",
      "storage_mode' = 'reference'",
      "c.protocol",
    ]) {
      expect(sql).toContain(clause);
    }
    expect(sql).toMatch(/ORDER BY cc\.collection_id/);
  });

  it("groups association rows per collection, in order", async () => {
    mockQuery.mockResolvedValueOnce(
      result([
        assoc("a", "Alpha", "1", "s3", true),
        assoc("a", "Alpha", "2", "sftp", false),
        assoc("b", null, "3", "s3", true),
      ]),
    );
    const out = await listReferenceSourceCollections(null);
    expect(out.map((c) => c.collection_id)).toEqual(["a", "b"]);
    expect(out[0].title).toBe("Alpha");
    expect(out[0].associations).toHaveLength(2);
    expect(out[0].associations[1]).toEqual({ association_id: "2", connection_id: "c-2", protocol: "sftp", anonymous: false });
    expect(out[1].title).toBeNull();
  });
});
