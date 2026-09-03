// @vitest-environment node
/**
 * Migration 026 shape (W-2).
 *
 * The column is nullable with a >= 1 CHECK because "keep at most zero items"
 * is not a retention policy, it is `archived` — which already exists and has
 * different semantics (it expires everything and blocks writes).
 */
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const sql = migrationEntry("026_collection_settings_retention_max_items");

describe("migration 026 (retention count cap)", () => {
  it("exists, after 025", () => {
    expect(
      migrationSource.indexOf('"026_collection_settings_retention_max_items"'),
    ).toBeGreaterThan(
      migrationSource.indexOf('"025_process_runs_queued_source_idx"'),
    );
  });

  it("adds a nullable column to collection_settings", () => {
    expect(sql).toContain("ALTER TABLE stac_higher.collection_settings");
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS retention_max_items integer");
    expect(sql).not.toContain("NOT NULL");
  });

  it("refuses a cap below one", () => {
    expect(sql).toMatch(
      /CHECK\s*\(\s*retention_max_items IS NULL OR retention_max_items >= 1\s*\)/,
    );
  });
});
