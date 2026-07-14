import { getClient, query } from "./connection";

const ADVISORY_KEY = 0x5ac_a1ed;

const MIGRATIONS = [
  {
    name: "001_create_extensions_table",
    sql: `
      CREATE SCHEMA IF NOT EXISTS stac_higher;

      CREATE TABLE IF NOT EXISTS stac_higher.extensions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        version TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        schema JSONB NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('local', 'external')),
        source_url TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
      );

      CREATE TABLE IF NOT EXISTS stac_higher.migrations (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
      );
    `,
  },
  {
    name: "002_create_schema_cache_table",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.schema_cache (
        url TEXT PRIMARY KEY,
        schema JSONB NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
      );

      CREATE INDEX IF NOT EXISTS schema_cache_expires_at_idx
        ON stac_higher.schema_cache (expires_at);
    `,
  },
];

let migrated = false;

export async function runMigrations(): Promise<void> {
  if (migrated) return;

  await query(`CREATE SCHEMA IF NOT EXISTS stac_higher`);
  await query(`
    CREATE TABLE IF NOT EXISTS stac_higher.migrations (
      name TEXT PRIMARY KEY,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `);

  const client = await getClient();
  try {
    await client.query("BEGIN");
    await client.query("SELECT pg_advisory_xact_lock($1)", [ADVISORY_KEY]);

    for (const migration of MIGRATIONS) {
      const result = await client.query(
        `SELECT 1 FROM stac_higher.migrations WHERE name = $1`,
        [migration.name],
      );
      if (result.rowCount === 0) {
        await client.query(migration.sql);
        await client.query(
          `INSERT INTO stac_higher.migrations (name) VALUES ($1)`,
          [migration.name],
        );
      }
    }

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }

  migrated = true;
}
