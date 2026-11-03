/**
 * Command-line wrapper around `runMigrations()`: logs a redacted target,
 * applies the migrations and reports success (0) or failure (1). It never
 * calls `process.exit`; the entry points decide how to exit.
 */

/**
 * Hide the userinfo (user and password) of a connection URL. It parses with
 * `new URL`, so the output is the normalised form (`URL` conventions), and an
 * unparseable value is never echoed: a malformed URL may still hold a secret.
 */
export function redactDatabaseUrl(url: string): string {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "(unparseable DATABASE_URL)";
  }
  if (!parsed.username && !parsed.password) return parsed.toString();
  parsed.username = "";
  parsed.password = "";
  return `${parsed.protocol}//***@${parsed.host}${parsed.pathname}${parsed.search}${parsed.hash}`;
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
