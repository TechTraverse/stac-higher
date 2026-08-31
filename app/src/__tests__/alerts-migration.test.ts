// @vitest-environment node
/**
 * Migration 021 shape + cross-runtime lockstep pins (P7-H, Phase 7 §11).
 *
 * The alerts open-dedup index is a THREE-party contract: the migration
 * defines it, and both pipeline INSERT sites (flow/repo.py `sync_alerts`,
 * notify/repo.py `raise_alert`) name its expressions verbatim as their
 * ON CONFLICT target — a drift on any side breaks alert dedup at runtime
 * with no type error anywhere. These tests read the three sources as text
 * and pin the shared expression list, so the drift fails a suite instead.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import {
  migrationEntry,
  migrationSource,
  normalizeSql,
} from "./helpers/migration-source";

function read(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
}

const normalize = normalizeSql;

const migrate = migrationSource;
const flowRepo = normalize(
  read("../../../services/pipeline/src/pipeline/flow/repo.py"),
);
const notifyRepo = normalize(
  read("../../../services/pipeline/src/pipeline/notify/repo.py"),
);

// The dedup identity's anchor legs, in index order (014 → 015 → 021).
// Migration 021's own identity (four anchors). M5-E's migration 024 extends
// it to six — pinned separately below, against 024's SQL.
const DEDUP_LEGS =
  "coalesce(connection_id::text, '')," +
  " coalesce(association_id::text, '')," +
  " coalesce(channel_id::text, '')," +
  " coalesce(collection_id, '')";

/** The CURRENT identity, which both pipeline INSERT sites must name verbatim. */
const DEDUP_LEGS_CURRENT =
  DEDUP_LEGS +
  ", coalesce(process_id::text, '')," +
  " coalesce(source_id::text, '')";

describe("migration 021 (alerts collection anchor)", () => {
  const start = migrate.indexOf('"021_alerts_collection_anchor"');
  const sql = migrationEntry("021_alerts_collection_anchor");

  it("exists, after 020", () => {
    expect(start).toBeGreaterThan(migrate.indexOf('"020_staged_uploads"'));
  });

  it("adds the anchor as nullable unconstrained text (no FK)", () => {
    expect(sql).toMatch(/ADD COLUMN IF NOT EXISTS collection_id text;/);
    expect(sql).not.toMatch(/REFERENCES/);
  });

  it("recreates the anchor CHECK with the fourth leg", () => {
    expect(sql).toMatch(/DROP CONSTRAINT IF EXISTS alerts_anchor_check/);
    expect(normalize(sql)).toContain(
      "connection_id IS NOT NULL OR association_id IS NOT NULL" +
        " OR channel_id IS NOT NULL OR collection_id IS NOT NULL",
    );
  });

  it("rebuilds the open-dedup index with the collection leg", () => {
    expect(sql).toMatch(/DROP INDEX IF EXISTS stac_higher\.alerts_open_dedup_idx/);
    expect(normalize(sql)).toContain(DEDUP_LEGS);
    expect(sql).toMatch(/WHERE state <> 'resolved'/);
    expect(sql).toMatch(/alerts_collection_idx/);
  });
});

describe("pipeline ON CONFLICT targets move in lockstep", () => {
  it("flow/repo.py sync_alerts names the CURRENT index expressions", () => {
    expect(flowRepo).toContain(`ON CONFLICT (source, kind, ${DEDUP_LEGS_CURRENT})`);
  });

  it("flow/repo.py auto-resolve compares on the SAME identity", () => {
    // Not just the upsert: the auto-resolve NOT EXISTS must compare every
    // anchor too. With the process legs missing, one source recovering would
    // never clear its alert while a sibling source stayed stalled, because
    // the two conditions would look identical on the older anchors.
    expect(flowRepo).toContain("c.proc = coalesce(a.process_id::text, '')");
    expect(flowRepo).toContain("c.src = coalesce(a.source_id::text, '')");
  });

  it("notify/repo.py raise_alert names the CURRENT index expressions", () => {
    expect(notifyRepo).toContain(`ON CONFLICT (source, kind, ${DEDUP_LEGS_CURRENT})`);
  });
});

describe("migration 024 (alerts process anchors, M5-E)", () => {
  const sql = migrationEntry("024_alerts_process_anchors");

  it("runs after Phase 7's 021", () => {
    expect(migrationSource.indexOf('"024_alerts_process_anchors"')).toBeGreaterThan(
      migrationSource.indexOf('"021_alerts_collection_anchor"'),
    );
  });

  it("adds BOTH anchors — per-process and per-source (I-63)", () => {
    // process_id: process_failed + process_rate_limited (the ceiling is a
    // per-process setting). source_id: process_stalled, per-source, so one
    // stalled trigger cannot silence another.
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS process_id uuid");
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS source_id uuid");
  });

  it("recreates the anchor CHECK with all six legs", () => {
    expect(sql).toMatch(/DROP CONSTRAINT IF EXISTS alerts_anchor_check/);
    expect(normalize(sql)).toContain(
      "connection_id IS NOT NULL OR association_id IS NOT NULL" +
        " OR channel_id IS NOT NULL OR collection_id IS NOT NULL" +
        " OR process_id IS NOT NULL OR source_id IS NOT NULL",
    );
  });

  it("recreates the open-dedup index with the extended identity", () => {
    expect(sql).toMatch(/DROP INDEX IF EXISTS stac_higher.alerts_open_dedup_idx/);
    expect(normalize(sql)).toContain(DEDUP_LEGS_CURRENT);
  });

  it("keeps BOTH pipeline conflict targets in lockstep", () => {
    // The three-party contract migration 021 established. A drift here breaks
    // alert dedup at runtime with no type error anywhere.
    expect(flowRepo).toContain(DEDUP_LEGS_CURRENT);
    expect(notifyRepo).toContain(DEDUP_LEGS_CURRENT);
  });
});
