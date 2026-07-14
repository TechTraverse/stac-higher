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
  {
    // Phase 1 (ROADMAP §5, §7): collection ownership/exposure settings and
    // the append-only audit log.
    name: "003_collection_settings_and_audit_log",
    sql: `
      -- collection_settings is SPARSE: a collection with no row here uses the
      -- defaults below, applied on read (lib/collections/settings.ts). In
      -- particular, every collection that existed before Phase 1 is UNOWNED
      -- and PUBLIC by default: group_id NULL means visible to all users and
      -- mutable by operators of any group as well as admins. See
      -- docs/decisions/0003-preexisting-collections.md for the rationale
      -- (no backfill: collections are created in pgstac out-of-band, so a
      -- one-time backfill would race and still leave the no-row case).
      CREATE TABLE IF NOT EXISTS stac_higher.collection_settings (
        collection_id TEXT PRIMARY KEY,
        group_id TEXT,
        externally_writable BOOLEAN NOT NULL DEFAULT false,
        retention_days INTEGER CHECK (retention_days IS NULL OR retention_days > 0),
        gc_grace_days INTEGER NOT NULL DEFAULT 30 CHECK (gc_grace_days >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
      );

      CREATE INDEX IF NOT EXISTS collection_settings_group_id_idx
        ON stac_higher.collection_settings (group_id);

      -- audit_log is APPEND-ONLY: no code path updates or deletes rows, and
      -- the triggers below reject UPDATE/DELETE/TRUNCATE at the database
      -- level as defense in depth. detail must NEVER contain secrets — the
      -- write path (lib/audit/log.ts) redacts credential-shaped fields.
      -- actor_groups is TEXT[] (the ROADMAP ERD sketches it as text) so the
      -- own-group audit view can filter with && against a GIN index.
      --
      -- Phase 6 hygiene (do NOT build now): time-partition this table and add
      -- a compliance-driven retention job; partition maintenance will need to
      -- deliberately drop/re-create the append-only triggers per partition.
      CREATE TABLE IF NOT EXISTS stac_higher.audit_log (
        id BIGSERIAL PRIMARY KEY,
        actor TEXT NOT NULL,
        actor_groups TEXT[] NOT NULL DEFAULT '{}',
        action TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        resource_id TEXT,
        detail JSONB NOT NULL DEFAULT '{}'::jsonb,
        at TIMESTAMPTZ NOT NULL DEFAULT now()
      );

      CREATE INDEX IF NOT EXISTS audit_log_at_idx
        ON stac_higher.audit_log (at DESC);
      CREATE INDEX IF NOT EXISTS audit_log_actor_groups_idx
        ON stac_higher.audit_log USING gin (actor_groups);

      CREATE OR REPLACE FUNCTION stac_higher.audit_log_block_mutation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        RAISE EXCEPTION 'stac_higher.audit_log is append-only (% rejected)', TG_OP;
      END;
      $fn$;

      DROP TRIGGER IF EXISTS audit_log_append_only ON stac_higher.audit_log;
      CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON stac_higher.audit_log
        FOR EACH ROW EXECUTE FUNCTION stac_higher.audit_log_block_mutation();

      DROP TRIGGER IF EXISTS audit_log_no_truncate ON stac_higher.audit_log;
      CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON stac_higher.audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION stac_higher.audit_log_block_mutation();
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
