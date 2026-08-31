// @vitest-environment node
/**
 * Migrations 022/023 shape pins (M5-0, Phase 9 design spec §3).
 *
 * These are the DDL decisions the spec settled that nothing else would catch:
 * a table quietly gaining a partition clause, a collection reference growing
 * an FK into pgstac, or the §7 coalescing rule surviving only as a comment.
 * Read as text, like the migration-021 pins.
 */
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const migrate = migrationSource;

describe("migration 022 (processes)", () => {
  const sql = migrationEntry("022_processes");

  it("runs after Phase 7's 021", () => {
    expect(migrate.indexOf('"022_processes"')).toBeGreaterThan(
      migrate.indexOf('"021_alerts_collection_anchor"'),
    );
  });

  it("creates all six process tables", () => {
    for (const table of [
      "processes",
      "process_revisions",
      "process_sources",
      "process_outputs",
      "process_runs",
      "process_checks",
    ]) {
      expect(sql, table).toContain(`stac_higher.${table} (`);
    }
  });

  it("carries the §7 rate ceiling with a floor of 1", () => {
    expect(sql).toContain("max_runs_per_hour integer NOT NULL DEFAULT 60");
    expect(sql).toContain("CHECK (max_runs_per_hour >= 1)");
  });

  it("leaves process_runs unpartitioned (ADR 0012 criteria, spec §3)", () => {
    // Runs are verb targets (the audited `rerun`) — the delivery_log
    // argument. Terminal rows age out via the history_retention sweep.
    expect(sql).not.toMatch(/PARTITION BY/);
    expect(sql).toContain("process_runs_terminal_age_idx");
  });

  it("enforces at most one deferred run per source (§7 coalescing)", () => {
    // Coalescing is the requirement; a UNIQUE index is what makes it true
    // under concurrent dispatch rather than merely intended.
    expect(sql).toMatch(
      /CREATE UNIQUE INDEX IF NOT EXISTS process_runs_deferred_source_idx[\s\S]*?rate_deferred_until IS NOT NULL/,
    );
  });

  it("references collections as bare text — pgstac owns them, no FK", () => {
    expect(sql).toContain("collection_id text NOT NULL,");
    expect(sql).not.toMatch(/collection_id text[^,\n]*REFERENCES/);
  });

  it("soft-deletes processes only; history never cascades (ADR 0009)", () => {
    expect(sql).toContain("deleted_at timestamptz");
    // Every FK into a process/revision is RESTRICT so a delete can never
    // take run history with it.
    expect(sql).not.toMatch(/ON DELETE CASCADE/);
    expect(sql).toContain("processes_live_name_idx");
  });

  it("pins the run status enum the pipeline writes", () => {
    expect(sql).toContain(
      "CHECK (status IN ('queued','running','succeeded','failed','dead'))",
    );
  });

  it("gives process_checks the connection_checks bridge shape (ADR 0004)", () => {
    const checks = sql.slice(sql.indexOf("stac_higher.process_checks ("));
    expect(checks).toContain(
      "CHECK (status IN ('pending','running','done','failed'))",
    );
    expect(checks).toContain("process_checks_pending_idx");
  });
});

describe("migration 023 (flow_stats_daily)", () => {
  const sql = migrationEntry("023_flow_stats_daily");

  it("runs after 022", () => {
    expect(migrate.indexOf('"023_flow_stats_daily"')).toBeGreaterThan(
      migrate.indexOf('"022_processes"'),
    );
  });

  it("keys one row per subject per day, both subject kinds in one table", () => {
    expect(sql).toContain("PRIMARY KEY (subject_kind, subject_id, day)");
    expect(sql).toContain(
      "CHECK (subject_kind IN ('association','process'))",
    );
  });

  it("stays a plain table — the row count is subjects x days (P9-E)", () => {
    expect(sql).not.toMatch(/PARTITION BY/);
    expect(sql).toContain("flow_stats_daily_day_idx");
  });
});
