// @vitest-environment node
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { runMigrateCli } from "@/lib/db/migrate-cli";

const BUNDLE = fileURLToPath(new URL("../../dist/server/migrate.mjs", import.meta.url));

describe("runMigrateCli", () => {
  it("returns 0 and logs a redacted URL when migrations apply", async () => {
    const log = vi.fn();
    const code = await runMigrateCli({ migrate: async () => {}, databaseUrl: "postgresql://owner:s3cret@db.example.org:5432/app", log });
    expect(code).toBe(0);
    const out = log.mock.calls.flat().join("\n");
    expect(out).toContain("postgresql://***@db.example.org:5432/app");
    expect(out).not.toContain("s3cret");
    expect(out).toContain("Migrations applied.");
  });
  it("names the dev default when DATABASE_URL is unset", async () => {
    const log = vi.fn();
    await runMigrateCli({ migrate: async () => {}, log });
    expect(log.mock.calls.flat().join("\n")).toContain("DATABASE_URL unset");
  });
  it("returns 1 and reports the error when migrations fail", async () => {
    const error = vi.fn();
    const code = await runMigrateCli({ migrate: async () => { throw new Error("boom"); }, log: () => {}, error });
    expect(code).toBe(1);
    expect(error).toHaveBeenCalledWith(expect.objectContaining({ message: "boom" }));
  });
  it.skipIf(!existsSync(BUNDLE))("the built bundle exits 1 against an unreachable database, without leaking the password", () => {
    const r = spawnSync(process.execPath, [BUNDLE], {
      env: { ...process.env, DATABASE_URL: "postgresql://owner:s3cret@127.0.0.1:1/none", PGSSLROOTCERT: "" },
      encoding: "utf8", timeout: 20_000,
    });
    expect(r.status).toBe(1);
    expect(`${r.stdout}${r.stderr}`).not.toContain("s3cret");
  }, 30_000);
});
