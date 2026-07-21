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
  {
    // Phase 2 (ROADMAP §5 CONNECTIONS + §5.2): group-owned connections with
    // write-only encrypted credentials, and the app→pipeline test-connection
    // bridge table. Both tables follow the cross-runtime contract verbatim —
    // the Python pipeline codes against these shapes and NEVER creates them
    // (docs/decisions/0001-migration-ownership.md, 0004-app-pipeline-bridge.md).
    //
    // credentials is an encrypted envelope (bytea): 0x01 version byte ||
    // 12-byte nonce || AES-256-GCM ciphertext+tag. The app encrypts on write
    // (lib/connections/crypto.ts) and never decrypts; only the pipeline
    // decrypts at job execution time.
    //
    // updated_at is maintained APP-SIDE (lib/connections/storage.ts), not by
    // a trigger: the pipeline also UPDATEs these rows (status/last_checked_at
    // on every health sweep), and a trigger would bump updated_at every ~5
    // minutes, destroying its meaning as "when a user last edited this".
    name: "004_connections_and_checks",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.connections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        description text NOT NULL DEFAULT '',
        protocol text NOT NULL CHECK (protocol IN ('ssh','sftp','ftp','ftps','s3','stac-api')),
        config jsonb NOT NULL DEFAULT '{}'::jsonb,
        credentials bytea,
        host_key text,
        host_key_pinned_at timestamptz,
        group_id text NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        enabled boolean NOT NULL DEFAULT true,
        status text NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified','ok','error')),
        last_checked_at timestamptz,
        last_error text
      );

      CREATE INDEX IF NOT EXISTS connections_group_id_idx
        ON stac_higher.connections (group_id);

      CREATE TABLE IF NOT EXISTS stac_higher.connection_checks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        connection_id uuid NOT NULL REFERENCES stac_higher.connections(id) ON DELETE CASCADE,
        requested_by text NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT now(),
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
        result jsonb,
        finished_at timestamptz
      );

      CREATE INDEX IF NOT EXISTS connection_checks_connection_id_idx
        ON stac_higher.connection_checks (connection_id);

      -- The pipeline drain job claims work with
      -- "SELECT ... WHERE status = 'pending' ... FOR UPDATE SKIP LOCKED";
      -- this partial index keeps that scan cheap as done rows accumulate.
      CREATE INDEX IF NOT EXISTS connection_checks_pending_idx
        ON stac_higher.connection_checks (requested_at)
        WHERE status = 'pending';
    `,
  },
  {
    // Phase 4 (ROADMAP §5 COLLECTION_CONNECTIONS + INGEST_FILES, §6.1): the
    // collection↔connection associations that drive ingest/delivery, and the
    // per-file ingest ledger the pipeline maintains. Both follow the
    // cross-runtime contract — the Python pipeline reads collection_connections
    // and reads/writes ingest_files, and NEVER creates them
    // (docs/decisions/0001-migration-ownership.md). The app owns this DDL; the
    // app writes associations (this slice) and the pipeline writes the ledger
    // (Phase 4 pipeline slice).
    //
    // collection_id is TEXT (pgstac collection ids are strings, created
    // out-of-band), matching collection_settings. The direction CHECK admits
    // both 'ingest' and 'deliver' so Phase 5 delivery reuses this table without
    // a further migration; the app's association CRUD only writes 'ingest' rows
    // this phase. config is the §5.1 shape (validated app-side by the ingest
    // Zod schema, mirrored in the pipeline). flow_stats is PIPELINE-written
    // telemetry (files/bytes/last_activity_at/latency) — the app reads it for
    // the Data-flow UI but never writes it, so no trigger touches it.
    name: "005_ingest_associations_and_files",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.collection_connections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        collection_id text NOT NULL,
        connection_id uuid NOT NULL REFERENCES stac_higher.connections(id) ON DELETE CASCADE,
        direction text NOT NULL CHECK (direction IN ('ingest','deliver')),
        enabled boolean NOT NULL DEFAULT true,
        config jsonb NOT NULL DEFAULT '{}'::jsonb,
        expectation jsonb,
        flow_stats jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- One association per (collection, connection, direction): a source is
        -- either wired for ingest into a collection or not.
        UNIQUE (collection_id, connection_id, direction)
      );

      CREATE INDEX IF NOT EXISTS collection_connections_collection_idx
        ON stac_higher.collection_connections (collection_id);
      CREATE INDEX IF NOT EXISTS collection_connections_connection_idx
        ON stac_higher.collection_connections (connection_id);
      -- The pipeline poll scheduler scans enabled ingest associations.
      CREATE INDEX IF NOT EXISTS collection_connections_ingest_enabled_idx
        ON stac_higher.collection_connections (direction, enabled)
        WHERE direction = 'ingest' AND enabled;

      -- ingest_files is the per-source-file ledger (ROADMAP §5, §6.1). Every
      -- ingest stage is idempotent against it. version increments when a
      -- previously-itemized source file changes (re-ingest = new version of the
      -- same product, same item_id). Written by the pipeline FETCH/EXTRACT/
      -- ITEMIZE stages; the app only reads it (activity/latency in the UI).
      --
      -- Phase 6 hygiene (do NOT build now, mirrors the audit_log deferral in
      -- migration 003): this is an envelope-scale high-volume table — Phase 6
      -- time-partitions it on created_at and adds a partition-drop retention
      -- job. Kept a plain table here so the ingest slice can land first.
      CREATE TABLE IF NOT EXISTS stac_higher.ingest_files (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        association_id uuid NOT NULL REFERENCES stac_higher.collection_connections(id) ON DELETE CASCADE,
        source_path text NOT NULL,
        version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
        size bigint,
        fingerprint text,
        checksum text,
        status text NOT NULL DEFAULT 'seen'
          CHECK (status IN ('seen','settled','fetching','stored','itemized','failed')),
        item_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- The ledger is keyed by (association, source_path, version): the
        -- pipeline UPSERTs the current version and inserts a new version row on
        -- a fingerprint change.
        UNIQUE (association_id, source_path, version)
      );

      CREATE INDEX IF NOT EXISTS ingest_files_association_idx
        ON stac_higher.ingest_files (association_id);
      -- DISCOVER diffs the live listing against the ledger by source_path.
      CREATE INDEX IF NOT EXISTS ingest_files_association_path_idx
        ON stac_higher.ingest_files (association_id, source_path);
      -- The asset route's reference-mode branch (Phase 4 Slice C) and the
      -- Data-flow UI look files up by the itemized item_id.
      CREATE INDEX IF NOT EXISTS ingest_files_item_idx
        ON stac_higher.ingest_files (item_id)
        WHERE item_id IS NOT NULL;
    `,
  },
  {
    // Phase 4 Slice C: reference-mode assets keep their bytes at the source.
    // The pipeline records the stable source URL here at FETCH; the app's asset
    // route (resolveAssetTarget) 302s to it. Null ⇒ canonical (copy mode /
    // manual upload). ADR 0001: app owns this DDL, pipeline only writes rows.
    name: "006_ingest_files_source_href",
    sql: `
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS source_href text;
    `,
  },
  {
    // Phase 5 (ROADMAP §5.4, §6.4): the event outbox that bridges pgstac item
    // changes to the delivery dispatcher. ONE row per changed item into
    // stac_higher.item_events (durable — never a pg_notify payload, which caps
    // at ~8 KB and would abort bulk-upsert txns); a payload-less NOTIFY wakes
    // the dispatcher (Slice C). ADR 0007 licenses the app to attach this trigger
    // to pgstac.items (a table the app does not own) — the trigger writes ONLY
    // into stac_higher. Guarded on pgstac.items existing so a pgstac-less DB
    // (unit/CI) still migrates cleanly.
    //
    // Mechanism (ADR 0007 spike): a ROW-level trigger, not statement-level.
    // pgstac.items is partitioned by collection; PostgreSQL clones row-level
    // triggers onto every partition (present and future), so this fires whether
    // pgstac routes through the parent OR writes directly into a partition
    // (bulk/partition-targeted upserts) — the every-write-path guarantee §5.4
    // needs. The empty-payload NOTIFY coalesces per transaction, so a bulk
    // upsert of N rows yields one dispatcher wake, not N.
    //
    // Phase 6 hygiene (do NOT build now, mirrors audit_log/ingest_files): this
    // is an envelope-scale table — Phase 6 time-partitions it on occurred_at and
    // adds a partition-drop retention job.
    name: "007_item_events_outbox",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.item_events (
        id BIGSERIAL PRIMARY KEY,
        collection_id text NOT NULL,
        item_id text NOT NULL,
        op text NOT NULL CHECK (op IN ('insert','update','delete')),
        occurred_at timestamptz NOT NULL DEFAULT now(),
        processed_at timestamptz
      );

      -- The dispatcher claims pending rows in id order; this partial index keeps
      -- that scan cheap as processed rows accumulate (until Phase 6 partitions).
      CREATE INDEX IF NOT EXISTS item_events_pending_idx
        ON stac_higher.item_events (id)
        WHERE processed_at IS NULL;

      CREATE OR REPLACE FUNCTION stac_higher.item_events_capture()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (TG_OP = 'DELETE') THEN
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (OLD.collection, OLD.id, 'delete');
        ELSIF (TG_OP = 'UPDATE') THEN
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (NEW.collection, NEW.id, 'update');
        ELSE
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (NEW.collection, NEW.id, 'insert');
        END IF;
        -- Payload-less wake only — the payload is the outbox row, never NOTIFY.
        PERFORM pg_notify('item_events', '');
        RETURN NULL;
      END;
      $fn$;

      -- Attach only if pgstac.items exists (row-level cascades to partitions).
      DO $do$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM information_schema.tables
          WHERE table_schema = 'pgstac' AND table_name = 'items'
        ) THEN
          DROP TRIGGER IF EXISTS item_events_capture_trg ON pgstac.items;
          CREATE TRIGGER item_events_capture_trg
            AFTER INSERT OR UPDATE OR DELETE ON pgstac.items
            FOR EACH ROW EXECUTE FUNCTION stac_higher.item_events_capture();
        END IF;
      END;
      $do$;
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
