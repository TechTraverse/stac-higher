// Postgres connection pool, built lazily from the environment.
//
// TLS (RDS): when PGSSLROOTCERT names a CA bundle, the pool verifies the server
// certificate against it. Node's TLS also checks the hostname (`servername` is
// the host), so this is libpq's `verify-full`. PGSSLROOTCERT is the libpq
// variable name, so the pipeline and the app share one setting (libpq clients also need
// `PGSSLMODE=verify-full`; node-postgres verifies whenever the bundle is set). An unreadable
// or empty bundle throws, naming the path; there is never a silent fallback to
// unverified or plaintext.
//
// node-postgres facts the deployment must know:
//  (a) pg merges a parsed connection string OVER the config object
//      (pg/lib/connection-parameters.js: `Object.assign({}, config,
//      parse(config.connectionString))`), so a DATABASE_URL carrying
//      `sslmode=`/`sslrootcert=` overrides the `ssl` object built here. Keep
//      TLS out of the URL and use PGSSLROOTCERT.
//  (b) With PGSSLROOTCERT unset the config is exactly `{ connectionString }`,
//      so pg's own PGSSLMODE handling is untouched (today's behaviour).
import { readFileSync } from "node:fs";
import pg from "pg";

export const DEFAULT_DATABASE_URL =
  "postgresql://username:password@localhost:5433/postgis";

export function poolConfigFromEnv(
  env: Record<string, string | undefined> = process.env,
  readFile: (path: string) => string = (p) => readFileSync(p, "utf8"),
): pg.PoolConfig {
  const config: pg.PoolConfig = {
    connectionString: env.DATABASE_URL ?? DEFAULT_DATABASE_URL,
  };
  const path = env.PGSSLROOTCERT?.trim();
  if (!path) return config;

  let ca: string;
  try {
    ca = readFile(path);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`PGSSLROOTCERT: could not read the CA bundle at ${path}: ${msg}`);
  }
  if (!ca.trim()) {
    throw new Error(`PGSSLROOTCERT: the CA bundle at ${path} is empty`);
  }
  config.ssl = { ca, rejectUnauthorized: true };
  return config;
}

let pool: pg.Pool | null = null;

// Built at first use, not module load: DATABASE_URL and PGSSLROOTCERT are read
// from the same process environment either way, which does not change between
// import and first query, so this is equivalent and lets bad TLS config fail
// the first query with a clear message instead of breaking module import.
function getPool(): pg.Pool {
  if (!pool) {
    pool = new pg.Pool(poolConfigFromEnv());
  }
  return pool;
}

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params?: unknown[],
): Promise<pg.QueryResult<T>> {
  return getPool().query<T>(text, params);
}

export async function getClient(): Promise<pg.PoolClient> {
  return getPool().connect();
}
