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

function read(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
}

/** Collapse quoting/whitespace so TS template SQL and Python string-literal
 * SQL normalize to the same text. */
function normalize(source: string): string {
  return source.replace(/["\s]+/g, " ");
}

const migrate = read("../lib/db/migrate.ts");
const flowRepo = normalize(
  read("../../../services/pipeline/src/pipeline/flow/repo.py"),
);
const notifyRepo = normalize(
  read("../../../services/pipeline/src/pipeline/notify/repo.py"),
);

// The dedup identity's anchor legs, in index order (014 → 015 → 021).
const DEDUP_LEGS =
  "coalesce(connection_id::text, '')," +
  " coalesce(association_id::text, '')," +
  " coalesce(channel_id::text, '')," +
  " coalesce(collection_id, '')";

/** The text of ONE migration entry: from its name to the start of the next
 * entry (or the end of the array). Slicing to the end of MIGRATIONS instead
 * would silently widen these pins over every migration added after it. */
function migrationEntry(name: string): string {
  const start = migrate.indexOf(`"${name}"`);
  const next = migrate.slice(start).search(/\n\s*name: "\d{3}_/);
  const end = next === -1 ? migrate.indexOf("];", start) : start + next;
  return migrate.slice(start, end);
}

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
  it("flow/repo.py sync_alerts names the migration-021 index expressions", () => {
    expect(flowRepo).toContain(`ON CONFLICT (source, kind, ${DEDUP_LEGS})`);
  });

  it("notify/repo.py raise_alert names the migration-021 index expressions", () => {
    expect(notifyRepo).toContain(`ON CONFLICT (source, kind, ${DEDUP_LEGS})`);
  });
});
