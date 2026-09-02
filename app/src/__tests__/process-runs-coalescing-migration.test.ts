// @vitest-environment node
/**
 * Migration 025 shape + the pipeline lockstep it creates (G-3).
 *
 * Coalescing used to apply only to RATE-DEFERRED queued runs (024's partial
 * index on `source_id`). Once a trigger dispatches its run immediately, two
 * arrivals milliseconds apart become two runs — so the coalescing key widens
 * to EVERY queued run per `(process_id, source_id)`.
 *
 * That index name and its predicate are a two-party contract: the migration
 * defines it and `PgProcessRepo.enqueue_run_detailed` names the same columns
 * and WHERE clause as its `ON CONFLICT` target. A drift breaks coalescing at
 * runtime with no type error anywhere, so both sides are pinned as text here.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import {
  migrationEntry,
  migrationSource,
  normalizeSql,
} from "./helpers/migration-source";

const sql = migrationEntry("025_process_runs_queued_source_idx");

const processRepo = normalizeSql(
  readFileSync(
    fileURLToPath(
      new URL(
        "../../../services/pipeline/src/pipeline/process/repo.py",
        import.meta.url,
      ),
    ),
    "utf8",
  ),
);

describe("migration 025 (queued-run coalescing)", () => {
  it("exists, after 024", () => {
    expect(
      migrationSource.indexOf('"025_process_runs_queued_source_idx"'),
    ).toBeGreaterThan(migrationSource.indexOf('"024_alerts_process_anchors"'));
  });

  it("replaces the deferred-only index with one over every queued run", () => {
    expect(sql).toContain(
      "DROP INDEX IF EXISTS stac_higher.process_runs_deferred_source_idx",
    );
    expect(sql).toContain(
      "CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_source_idx",
    );
    expect(normalizeSql(sql)).toContain(
      "ON stac_higher.process_runs (process_id, source_id)",
    );
    expect(normalizeSql(sql)).toContain(
      "WHERE status = 'queued' AND source_id IS NOT NULL",
    );
  });

  it("merges pre-existing duplicates BEFORE creating the unique index", () => {
    // Without this the CREATE UNIQUE INDEX fails on any database that already
    // has two queued runs for one source — which is exactly the state the
    // immediate-dispatch change produces.
    const merge = sql.indexOf("DELETE FROM stac_higher.process_runs");
    const create = sql.indexOf("CREATE UNIQUE INDEX");
    expect(merge).toBeGreaterThan(-1);
    expect(create).toBeGreaterThan(merge);
    // The surviving row keeps the others' work.
    expect(sql).toContain("input_items");
  });

  it("is the ON CONFLICT target the pipeline names verbatim", () => {
    expect(processRepo).toContain("ON CONFLICT (process_id, source_id)");
    expect(processRepo).toContain(
      "WHERE status = 'queued' AND source_id IS NOT NULL",
    );
  });
});
