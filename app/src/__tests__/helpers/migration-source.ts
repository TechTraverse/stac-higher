/**
 * Shared accessor for the migration source text, for the suites that pin DDL
 * decisions as text (migration 021's alert-anchor lockstep, migration 022/023's
 * process shapes).
 *
 * It exists because of a specific failure: those suites originally sliced from
 * their migration's name to the END of the MIGRATIONS array, so every pin
 * silently widened over each migration added afterwards — migration 022 tripped
 * migration 021's "no FK" assertion. Slicing to the NEXT entry fixes that, and
 * living here means the next migration-pin suite inherits the fix instead of
 * copying the original bug (the `helpers/settings-fixtures.ts` rationale).
 *
 * `migrationEntry` THROWS on an unknown name rather than returning empty text:
 * a renamed or renumbered migration must fail loudly, since every negative
 * assertion in these suites (`not.toMatch(/PARTITION BY/)`, …) would otherwise
 * pass vacuously against an empty string — the exact silent-widening class the
 * helper was written to kill.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const migrationSource = readFileSync(
  fileURLToPath(new URL("../../lib/db/migrate.ts", import.meta.url)),
  "utf8",
);

/** The text of ONE migration entry: from its name to the start of the next
 * entry, or to the end of the MIGRATIONS array for the last one. */
export function migrationEntry(name: string): string {
  const start = migrationSource.indexOf(`"${name}"`);
  if (start === -1) {
    throw new Error(
      `migration ${name} not found in migrate.ts — renamed, renumbered, or removed`,
    );
  }
  const next = migrationSource.slice(start).search(/\n\s*name: "\d{3}_/);
  const end =
    next === -1 ? migrationSource.indexOf("];", start) : start + next;
  return migrationSource.slice(start, end);
}

/** Collapse quoting/whitespace so TS template SQL and Python string-literal
 * SQL normalize to the same text. */
export function normalizeSql(source: string): string {
  return source.replace(/["\s]+/g, " ");
}
