import { runMigrations } from "../lib/db/migrate";
import { runMigrateCli } from "../lib/db/migrate-cli";

// process.exit is needed because connection.ts's module-level pool keeps the
// event loop alive (its idle sockets have no exported close).
runMigrateCli({
  migrate: runMigrations,
  databaseUrl: process.env.DATABASE_URL,
}).then((code) => process.exit(code));
