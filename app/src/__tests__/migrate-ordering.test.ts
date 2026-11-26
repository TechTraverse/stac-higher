// @vitest-environment node
/**
 * `runMigrations()` applies migrations by NAME, not by position or count
 * (K-3). Migration 029 landed after 030, 032 and 033 were already applied on
 * live databases; it sits between 028 and 030 in MIGRATIONS, and a database
 * that has recorded 001–028 and 030–033 must apply exactly 029 — nothing
 * already applied re-runs, nothing is skipped.
 *
 * The DB layer is mocked: the runner's own bookkeeping (the recorded-name
 * set, the per-migration INSERT) is what is under test, not Postgres.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const db = vi.hoisted(() => ({
  applied: [] as string[],
  executed: [] as string[],
  inserted: [] as string[],
}));

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(async () => ({ rows: [] })),
  getClient: vi.fn(async () => ({
    query: vi.fn(async (sql: string, params?: unknown[]) => {
      if (sql.startsWith("SELECT name FROM stac_higher.migrations")) {
        return { rows: db.applied.map((name) => ({ name })) };
      }
      if (sql.startsWith("INSERT INTO stac_higher.migrations")) {
        db.inserted.push(String(params?.[0]));
        return { rows: [] };
      }
      db.executed.push(sql);
      return { rows: [] };
    }),
    release: vi.fn(),
  })),
}));

/** A fresh module per run: `runMigrations` latches `migrated` after one call. */
async function migrateWith(applied: string[]): Promise<void> {
  db.applied = applied;
  db.executed = [];
  db.inserted = [];
  vi.resetModules();
  const { runMigrations } = await import("@/lib/db/migrate");
  await runMigrations();
}

describe("runMigrations applies by name", () => {
  let allNames: string[];

  beforeEach(async () => {
    await migrateWith([]);
    allNames = [...db.inserted];
  });

  it("a fresh database applies every migration once, 029 between 028 and 030", () => {
    expect(new Set(allNames).size).toBe(allNames.length);
    const at = allNames.indexOf("029_process_runs_executor_phase");
    expect(at).toBe(allNames.indexOf("028_builtin_processes") + 1);
    expect(allNames[at + 1]).toBe("030_container_images");
  });

  it("a database already at 033 (without 029) applies exactly 029", async () => {
    const atThirtyThree = allNames.filter((n) => n !== "029_process_runs_executor_phase");
    expect(atThirtyThree).toContain("033_cube_reader_role");

    await migrateWith(atThirtyThree);

    expect(db.inserted).toEqual(["029_process_runs_executor_phase"]);
    const migrationSql = db.executed.filter((sql) =>
      sql.includes("ADD COLUMN IF NOT EXISTS phase text"),
    );
    expect(migrationSql).toHaveLength(1);
    // Nothing already applied re-ran: 030's and 033's DDL are not executed.
    expect(db.executed.some((sql) => sql.includes("stac_higher.container_images ("))).toBe(false);
    expect(db.executed.some((sql) => sql.includes("stac_higher_cube_reader NOLOGIN"))).toBe(false);
  });

  it("a database already at 033 (with 029, without 031) applies exactly 031 (K-4)", async () => {
    const at = allNames.indexOf("031_process_runs_cancelled");
    expect(allNames[at - 1]).toBe("030_container_images");
    expect(allNames[at + 1]).toBe("032_cube_sinks");
    const withoutK4 = allNames.filter((n) => n !== "031_process_runs_cancelled");
    expect(withoutK4).toContain("029_process_runs_executor_phase");
    expect(withoutK4).toContain("033_cube_reader_role");

    await migrateWith(withoutK4);

    expect(db.inserted).toEqual(["031_process_runs_cancelled"]);
    expect(
      db.executed.filter((sql) => sql.includes("credentials_expire_at timestamptz")),
    ).toHaveLength(1);
    expect(db.executed.some((sql) => sql.includes("ADD COLUMN IF NOT EXISTS phase text"))).toBe(
      false,
    );
  });

  it("a database with 029 recorded applies nothing", async () => {
    await migrateWith(allNames);
    expect(db.inserted).toEqual([]);
  });
});
