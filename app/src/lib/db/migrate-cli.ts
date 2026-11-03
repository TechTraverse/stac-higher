/**
 * Command-line wrapper around `runMigrations()`: logs a redacted target,
 * applies the migrations and reports success (0) or failure (1). It never
 * calls `process.exit`; the entry points decide how to exit.
 */

/** Hide the userinfo (user and password) of a connection URL. */
export function redactDatabaseUrl(url: string): string {
  return url.replace(/:\/\/[^@]*@/, "://***@");
}

export async function runMigrateCli(deps: {
  migrate: () => Promise<void>;
  databaseUrl?: string;
  log?: (line: string) => void;
  error?: (err: unknown) => void;
}): Promise<0 | 1> {
  const log = deps.log ?? console.log;
  const error = deps.error ?? console.error;
  try {
    log(
      `Applying stac_higher migrations to ${
        deps.databaseUrl
          ? redactDatabaseUrl(deps.databaseUrl)
          : "(DATABASE_URL unset — using the dev default)"
      }`,
    );
    await deps.migrate();
    log("Migrations applied.");
    return 0;
  } catch (err) {
    error(err);
    return 1;
  }
}
