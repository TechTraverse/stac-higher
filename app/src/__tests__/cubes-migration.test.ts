// @vitest-environment node
/**
 * Migration 032 shape pins (Z-2, virtual cube spec §4). Read as text, like the
 * 030 pins; the status CHECK is compared against cube-append-status.json.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const STATUS = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/cube-append-status.json", import.meta.url)),
    "utf8",
  ),
) as { statuses: string[] };

const sql = migrationEntry("032_cube_sinks");

describe("migration 032 (cube sinks — Z-2)", () => {
  it("runs after 030 (and after 029/031 whenever those land, by name)", () => {
    expect(migrationSource.indexOf('"032_cube_sinks"')).toBeGreaterThan(
      migrationSource.indexOf('"030_container_images"'),
    );
  });

  it("creates both tables in stac_higher", () => {
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.cube_sinks (");
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.cube_appends (");
  });

  it("allows one sink per cube collection and forbids source = cube", () => {
    expect(sql).toMatch(/cube_collection_id\s+text NOT NULL UNIQUE/);
    expect(sql).toContain(
      "CONSTRAINT cube_sinks_distinct_collections_check CHECK (source_collection_id <> cube_collection_id)",
    );
  });

  it("has no FK to pgstac (collections live there)", () => {
    expect(sql).not.toMatch(/REFERENCES pgstac/);
  });

  it("constrains the ledger status to exactly the fixture vocabulary", () => {
    const match = sql.match(/cube_appends_status_check\s+CHECK \(status IN \(([^)]+)\)\)/);
    expect(match).not.toBeNull();
    expect(match![1].split(",").map((s) => s.trim().replace(/'/g, ""))).toEqual(STATUS.statuses);
  });

  it("cascades the ledger with its sink and keeps the insert idempotent", () => {
    expect(sql).toMatch(
      /cube_sink_id\s+uuid NOT NULL REFERENCES stac_higher\.cube_sinks\(id\) ON DELETE CASCADE/,
    );
    expect(sql).toContain("UNIQUE (cube_sink_id, item_id)");
  });

  it("indexes enabled sinks by source and pending rows by time", () => {
    expect(sql).toMatch(
      /cube_sinks_source_enabled_idx\s+ON stac_higher\.cube_sinks \(source_collection_id\) WHERE enabled/,
    );
    expect(sql).toMatch(
      /cube_appends_pending_idx\s+ON stac_higher\.cube_appends \(cube_sink_id, item_datetime\)\s+WHERE status = 'pending'/,
    );
  });
});
