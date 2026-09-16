/**
 * Apply the app's `stac_higher` schema to the database in DATABASE_URL.
 *
 * Exists for CI. ADR 0001 gives the app sole ownership of every `stac_higher.*`
 * DDL — the pipeline reads and writes rows but never issues DDL — and in
 * production that schema is created by `runMigrations()` on the first API
 * request. The pipeline's own CI job never starts the app, so its pgstac
 * service container had pgstac's tables and nothing else, and
 * `test_a_repo_call_does_not_open_a_new_session` (which makes a real
 * `PgIngestRepo` call) failed on
 * `relation "stac_higher.collection_connections" does not exist` — issue #24.
 *
 * Running the app's real migration list here, rather than hand-maintaining a
 * second copy of the schema in a pytest fixture, is the point: a forked copy
 * would drift from the app's, which is exactly the failure mode
 * tests/contract-fixtures/ exists to prevent.
 */
import { runMigrations } from "../app/src/lib/db/migrate.ts";

async function main(): Promise<void> {
  const url = process.env.DATABASE_URL;
  console.log(
    `Applying stac_higher migrations to ${
      url
        ? url.replace(/:\/\/[^@]*@/, "://***@")
        : "(DATABASE_URL unset — using the dev default)"
    }`,
  );

  await runMigrations();
  console.log("Migrations applied.");
}

// Wrapped in main() rather than using top-level await: the root package.json
// has no "type": "module", so this file transpiles to CJS.
main().then(
  () => {
    // `connection.ts` owns a module-level pg.Pool with no exported close, so
    // its idle sockets would keep the event loop alive forever. Nothing else
    // is in flight here, so exiting explicitly is the whole cleanup.
    process.exit(0);
  },
  (err: unknown) => {
    console.error(err);
    process.exit(1);
  },
);
