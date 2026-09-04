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
    // into stac_higher. This tracked migration creates ONLY the table + function
    // (no pgstac dependency, so a pgstac-less DB still migrates cleanly);
    // ATTACHING the trigger to pgstac.items is done by the idempotent
    // reconcileOutboxTrigger step that runs on EVERY runMigrations() call — a
    // once-recorded migration would be permanently skipped if it ran before
    // pgstac.items existed (deploy-ordering race), silently leaving the outbox
    // unpopulated and delivery a no-op forever.
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
    `,
  },
  {
    // Phase 5 Slice B-i (ROADMAP §5 DELIVERY_LOG, §6.4): the per-item delivery
    // record the delivery workers maintain. App owns this DDL; the pipeline only
    // writes rows (ADR 0001). One row per (association, item): a later event for
    // the same item UPSERTs it, so B-ii derives first-delivery-vs-redelivery from
    // this row's presence/status, never from the outbox op (an update surfaces as
    // delete+insert — ADR 0007).
    //
    // Phase 6 hygiene (do NOT build now, mirrors audit_log/ingest_files/item_events):
    // envelope-scale table — Phase 6 time-partitions it on created_at + a
    // partition-drop retention job. next_attempt_at (retry scheduling) is added
    // by the B-iii retry sweep, not here.
    name: "008_delivery_log",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.delivery_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        association_id uuid NOT NULL REFERENCES stac_higher.collection_connections(id) ON DELETE CASCADE,
        item_id text NOT NULL,
        status text NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','delivering','delivered','failed','dead')),
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        bytes bigint,
        error text,
        item_created_at timestamptz,
        delivered_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- One row per (association, item): the idempotency key. UPSERTed on
        -- redelivery; attempts/delivered_at update in place.
        UNIQUE (association_id, item_id)
      );

      CREATE INDEX IF NOT EXISTS delivery_log_association_idx
        ON stac_higher.delivery_log (association_id);
      -- Reserved for the B-iii retry sweep: cheaply find retryable rows.
      CREATE INDEX IF NOT EXISTS delivery_log_retry_idx
        ON stac_higher.delivery_log (updated_at)
        WHERE status = 'failed';
    `,
  },
  {
    // Phase 5 Slice B-ii (ROADMAP §6.4): per-asset delivered fingerprints — the
    // change-detection substrate for on_update (redeliver only changed assets)
    // and log-based overwrite. Shape: {asset_key: {fingerprint, size, filename}}.
    // fingerprint is "sha256:<hex>" (streamed) or "etag:<etag>/<size>"
    // (server-side copy); kinds compare unequal → worst case one redundant
    // redeliver (at-least-once, ISSUES I-43).
    name: "009_delivery_log_delivered_assets",
    sql: `
      ALTER TABLE stac_higher.delivery_log
        ADD COLUMN IF NOT EXISTS delivered_assets jsonb NOT NULL DEFAULT '{}'::jsonb;
    `,
  },
  {
    // ADR 0009 (deletion semantics), soft-delete half — ISSUES I-51.
    //
    // Connections and associations soft-delete: DELETE sets deleted_at (rows
    // leave all listings and API reads; credentials/host-key are scrubbed
    // app-side at delete time). History tables (ingest_files, delivery_log,
    // connection_checks) are passive records that survive their parents —
    // the FKs flip CASCADE → RESTRICT as belt-and-braces so a future
    // hard-delete path cannot silently destroy provenance either.
    //
    // ingest_files.reference_removed_at marks ledger rows whose reference-
    // backed items were removed from the catalog with their connection (ADR
    // 0009 §3): the asset route's reference resolution excludes marked rows,
    // while the rows themselves are retained as provenance.
    //
    // The hard UNIQUE(collection_id, connection_id, direction) becomes a
    // partial unique index on live rows so a replacement association can be
    // created after a soft delete. (Dropped via pg_constraint lookup — the
    // auto-generated name exceeds Postgres's 63-char identifier limit, so a
    // literal DROP CONSTRAINT would guess wrong.)
    name: "010_soft_delete_adr_0009",
    sql: `
      ALTER TABLE stac_higher.connections
        ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
      ALTER TABLE stac_higher.collection_connections
        ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS reference_removed_at timestamptz;

      ALTER TABLE stac_higher.connection_checks
        DROP CONSTRAINT IF EXISTS connection_checks_connection_id_fkey,
        ADD CONSTRAINT connection_checks_connection_id_fkey
          FOREIGN KEY (connection_id) REFERENCES stac_higher.connections(id)
          ON DELETE RESTRICT;
      ALTER TABLE stac_higher.collection_connections
        DROP CONSTRAINT IF EXISTS collection_connections_connection_id_fkey,
        ADD CONSTRAINT collection_connections_connection_id_fkey
          FOREIGN KEY (connection_id) REFERENCES stac_higher.connections(id)
          ON DELETE RESTRICT;
      ALTER TABLE stac_higher.ingest_files
        DROP CONSTRAINT IF EXISTS ingest_files_association_id_fkey,
        ADD CONSTRAINT ingest_files_association_id_fkey
          FOREIGN KEY (association_id) REFERENCES stac_higher.collection_connections(id)
          ON DELETE RESTRICT;
      ALTER TABLE stac_higher.delivery_log
        DROP CONSTRAINT IF EXISTS delivery_log_association_id_fkey,
        ADD CONSTRAINT delivery_log_association_id_fkey
          FOREIGN KEY (association_id) REFERENCES stac_higher.collection_connections(id)
          ON DELETE RESTRICT;

      DO $mig$
      DECLARE con text;
      BEGIN
        SELECT conname INTO con
          FROM pg_constraint
         WHERE conrelid = 'stac_higher.collection_connections'::regclass
           AND contype = 'u';
        IF con IS NOT NULL THEN
          EXECUTE format(
            'ALTER TABLE stac_higher.collection_connections DROP CONSTRAINT %I',
            con
          );
        END IF;
      END;
      $mig$;
      CREATE UNIQUE INDEX IF NOT EXISTS collection_connections_live_unique_idx
        ON stac_higher.collection_connections (collection_id, connection_id, direction)
        WHERE deleted_at IS NULL;
    `,
  },
  {
    // Phase 5 Slice B-iii (ROADMAP §6.4): retry/dead-letter + crash-recovery
    // substrate. App owns the DDL; the pipeline writes the columns (ADR 0001).
    //
    // delivery_log.next_attempt_at — when a 'failed' row becomes due for the
    //   pipeline's retry sweep; NULL for terminal rows ('dead' after
    //   retry.max_attempts, per-association §5.1 config).
    // item_events.claimed_at — atomic claim marker (ISSUES I-40): the
    //   dispatcher claims a batch by stamping claimed_at in the same statement
    //   that selects it (FOR UPDATE SKIP LOCKED), so overlapping dispatch runs
    //   cannot double-claim; a crash leaves claimed-but-unprocessed rows that
    //   are reclaimed after a stale window (crash direction stays safe).
    // ingest_files.retries — bounded-retry counter for 'failed' ledger rows
    //   (ISSUES I-52): the recovery sweep re-settles failed rows up to a cap.
    name: "011_retry_deadletter_and_claims",
    sql: `
      ALTER TABLE stac_higher.delivery_log
        ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
      CREATE INDEX IF NOT EXISTS delivery_log_next_attempt_idx
        ON stac_higher.delivery_log (next_attempt_at)
        WHERE status = 'failed';

      ALTER TABLE stac_higher.item_events
        ADD COLUMN IF NOT EXISTS claimed_at timestamptz;

      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS retries integer NOT NULL DEFAULT 0;
    `,
  },
  {
    // Phase 5 Slice C (ROADMAP §6.4): bounded I-38 visibility retry + the
    // user-initiated backfill bridge. App owns the DDL; the pipeline writes
    // the columns/rows (ADR 0001, bridge pattern per ADR 0004).
    //
    // item_events.dispatch_attempts / next_dispatch_at — when the dispatcher
    //   claims an event whose item is not yet visible (the I-38 race), it
    //   releases the claim and schedules a bounded retry instead of silently
    //   draining: attempts increments, next_dispatch_at pushes the row out of
    //   the claim window for a short cool-off. At the attempt cap the event is
    //   drained with a loud log (the old best-effort behavior).
    // delivery_backfills — one row per operator-initiated "backfill existing
    //   items" request on a deliver association (§6.4: late-added associations
    //   apply to new items only; backfill is explicit). The app INSERTs
    //   ('queued') and polls; the pipeline claims rows, pages the collection's
    //   items from pgstac (cursor_item_id), enqueues chunked bulk delivery
    //   jobs, and stamps progress/terminal status. FK is RESTRICT like the
    //   other history tables (migration 010): associations soft-delete, so
    //   backfill provenance survives.
    name: "012_dispatch_retry_and_backfills",
    sql: `
      ALTER TABLE stac_higher.item_events
        ADD COLUMN IF NOT EXISTS dispatch_attempts integer NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS next_dispatch_at timestamptz;

      CREATE TABLE IF NOT EXISTS stac_higher.delivery_backfills (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        association_id uuid NOT NULL
          REFERENCES stac_higher.collection_connections(id) ON DELETE RESTRICT,
        requested_by text NOT NULL,
        status text NOT NULL DEFAULT 'queued'
          CHECK (status IN ('queued','running','completed','failed')),
        items_enqueued integer NOT NULL DEFAULT 0 CHECK (items_enqueued >= 0),
        cursor_item_id text,
        error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        finished_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now()
      );

      -- The pipeline sweep scans for open work; keep that scan cheap as
      -- finished rows accumulate.
      CREATE INDEX IF NOT EXISTS delivery_backfills_open_idx
        ON stac_higher.delivery_backfills (created_at)
        WHERE status IN ('queued','running');
      CREATE INDEX IF NOT EXISTS delivery_backfills_association_idx
        ON stac_higher.delivery_backfills (association_id);
    `,
  },
  {
    // Slice C follow-up: enforce "at most one open backfill per association"
    // in the database instead of a route-level check-then-insert — the
    // partial unique index is the ON CONFLICT arbiter for insertBackfill, so
    // concurrent requests (or any future writer) cannot stack duplicate bulk
    // work. The route maps the conflict to its 409.
    name: "013_backfill_one_open_per_association",
    sql: `
      CREATE UNIQUE INDEX IF NOT EXISTS delivery_backfills_one_open_idx
        ON stac_higher.delivery_backfills (association_id)
        WHERE status IN ('queued','running');
    `,
  },
  {
    // M2-B (ROADMAP §5 ALERTS + NOTIFICATION_CHANNELS, §6.6; M2 spec §3-§4).
    // The spec numbered this migration 013, but the CI slice took that number
    // first (013_backfill_one_open_per_association) — the spec's 013/014/015
    // are therefore 014/015/016 on disk.
    //
    // alerts is written by the PIPELINE's flow_monitor job (raise, last_seen
    // re-fire, auto-resolve) and by the app's audited ack/resolve routes —
    // never DDL from the pipeline (ADR 0001). Lifecycle: firing →
    // acknowledged → resolved; `acknowledged` suppresses notification, not
    // detection, so last_seen keeps updating on an acknowledged row.
    //
    // Dedup (spec §3.3): at most ONE open (non-resolved) alert per condition
    // (source, kind, connection, association). Resolved rows are history — a
    // condition that re-fires after a resolve gets a NEW row (which is what
    // re-notifies). The partial unique index below is the ON CONFLICT arbiter
    // for the pipeline's raise-or-bump upsert; the coalesce() wrapping is
    // needed because a plain unique index treats NULLs as distinct.
    //
    // Group scoping is derived, not stored: alert → connection (directly or
    // through the association) → connections.group_id. No group_id column to
    // drift when a connection changes hands.
    name: "014_alerts_and_notification_channels",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.alerts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source text NOT NULL CHECK (source IN ('flow','health','job_failure')),
        kind text NOT NULL,
        connection_id uuid
          REFERENCES stac_higher.connections(id) ON DELETE CASCADE,
        association_id uuid
          REFERENCES stac_higher.collection_connections(id) ON DELETE CASCADE,
        state text NOT NULL DEFAULT 'firing'
          CHECK (state IN ('firing','acknowledged','resolved')),
        message text NOT NULL,
        first_seen timestamptz NOT NULL DEFAULT now(),
        last_seen timestamptz NOT NULL DEFAULT now(),
        acknowledged_at timestamptz,
        acknowledged_by text,
        resolved_at timestamptz,
        CHECK (connection_id IS NOT NULL OR association_id IS NOT NULL)
      );

      CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_dedup_idx
        ON stac_higher.alerts (
          source, kind,
          coalesce(connection_id::text, ''),
          coalesce(association_id::text, '')
        )
        WHERE state <> 'resolved';
      CREATE INDEX IF NOT EXISTS alerts_state_last_seen_idx
        ON stac_higher.alerts (state, last_seen DESC);
      CREATE INDEX IF NOT EXISTS alerts_association_idx
        ON stac_higher.alerts (association_id);
      CREATE INDEX IF NOT EXISTS alerts_connection_idx
        ON stac_higher.alerts (connection_id);

      -- Per-group notification channels (§5). DDL lands with the alerts table
      -- (one migration for the spec-§3/§4 pair); the channel CRUD + dispatch
      -- is M2-C. in_app needs no config; webhook carries url + optional
      -- signing secret in config (a cross-runtime contract fixed in M2-C).
      CREATE TABLE IF NOT EXISTS stac_higher.notification_channels (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        group_id text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('in_app','webhook')),
        config jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
      );

      CREATE INDEX IF NOT EXISTS notification_channels_group_idx
        ON stac_higher.notification_channels (group_id);
    `,
  },
  {
    // M2-C (M2 spec §4, ADR 0010): notification dispatch + in-app read state.
    //
    // - alerts.channel_id anchors channel-scoped alerts (a webhook channel
    //   that terminally fails to notify must itself be visible as a
    //   `job_failure`/`webhook_failed` alert). The anchor CHECK and the open
    //   dedup index grow the third leg; the pipeline's sync_alerts ON CONFLICT
    //   target must match the index expressions exactly, so both sides change
    //   together in this slice.
    // - alerts.notified_at is the fan-out watermark: the pipeline's notify
    //   sweep fans a firing, un-notified alert out to the owning group's
    //   webhook channels, then stamps it (crash between the two re-runs the
    //   idempotent fan-out — at-least-once by design).
    // - notification_deliveries is the per-(alert, channel) webhook ledger,
    //   deliberately shaped like delivery_log: the pipeline claims
    //   pending/failed rows, retries with backoff via its sweep, and
    //   dead-letters at the attempt cap. UNIQUE (alert_id, channel_id) makes
    //   fan-out idempotent.
    // - alert_reads is the per-user read watermark for the header bell
    //   (M2-D): unread = firing alerts whose first_seen is past the caller's
    //   watermark. A watermark (not per-alert rows) because "read" here means
    //   "the bell was opened", not per-row triage — ack/resolve is the
    //   per-row verb and lives on the alert itself.
    name: "015_notification_dispatch_and_read_state",
    sql: `
      ALTER TABLE stac_higher.alerts
        ADD COLUMN IF NOT EXISTS channel_id uuid
          REFERENCES stac_higher.notification_channels(id) ON DELETE CASCADE,
        ADD COLUMN IF NOT EXISTS notified_at timestamptz;

      -- The migration-014 anchor CHECK was unnamed; PostgreSQL auto-named it.
      ALTER TABLE stac_higher.alerts
        DROP CONSTRAINT IF EXISTS alerts_check;
      ALTER TABLE stac_higher.alerts
        DROP CONSTRAINT IF EXISTS alerts_anchor_check;
      ALTER TABLE stac_higher.alerts
        ADD CONSTRAINT alerts_anchor_check CHECK (
          connection_id IS NOT NULL
          OR association_id IS NOT NULL
          OR channel_id IS NOT NULL
        );

      DROP INDEX IF EXISTS stac_higher.alerts_open_dedup_idx;
      CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_dedup_idx
        ON stac_higher.alerts (
          source, kind,
          coalesce(connection_id::text, ''),
          coalesce(association_id::text, ''),
          coalesce(channel_id::text, '')
        )
        WHERE state <> 'resolved';

      CREATE TABLE IF NOT EXISTS stac_higher.notification_deliveries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        alert_id uuid NOT NULL
          REFERENCES stac_higher.alerts(id) ON DELETE CASCADE,
        channel_id uuid NOT NULL
          REFERENCES stac_higher.notification_channels(id) ON DELETE CASCADE,
        status text NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','delivering','delivered','failed','dead')),
        attempts integer NOT NULL DEFAULT 0,
        last_error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (alert_id, channel_id)
      );
      CREATE INDEX IF NOT EXISTS notification_deliveries_status_idx
        ON stac_higher.notification_deliveries (status, updated_at);

      CREATE TABLE IF NOT EXISTS stac_higher.alert_reads (
        user_sub text PRIMARY KEY,
        last_read_at timestamptz NOT NULL DEFAULT now()
      );
    `,
  },
  {
    // M2-E (M2 spec §7, ADR 0009's archived state). The spec bundled this
    // column with M2-F's asset_gc migration; it lands here instead so the
    // Settings tab exposes the full §7 field set in one slice. Until M2-F,
    // `archived` is DECLARATIVE-ONLY — nothing enforces it yet (the same
    // dead-wiring posture retention_days has had since migration 003).
    // M2-F's retention/GC job gives it teeth (archive → GC path).
    name: "016_collection_settings_archived",
    sql: `
      ALTER TABLE stac_higher.collection_settings
        ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false;
    `,
  },
  {
    // M2-F (M2 spec §5, ADR 0011): the single marked-then-collected queue for
    // EVERY path that schedules canonical bytes to die — retention expiry,
    // manual item delete, collection delete, archive. One place where "these
    // bytes are scheduled for deletion" is true, one sweep that makes it so.
    //
    // object_key is a KEY PREFIX under the platform bucket (§5.3 layout keys
    // nest per item: assets/{collection}/{item}/ — one row covers all of an
    // item's assets; a collection-scoped mark uses assets/{collection}/).
    // Written by BOTH runtimes (app: item/collection delete + archive intent;
    // pipeline: retention expiry) — rows only, DDL stays here (ADR 0001).
    // The partial unique index makes re-marking idempotent while letting a
    // key be marked again after a past collection completed (item re-created
    // then re-deleted).
    name: "017_asset_gc",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.asset_gc (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        object_key text NOT NULL,
        collection_id text NOT NULL,
        item_id text,
        reason text NOT NULL
          CHECK (reason IN ('retention','item_delete','collection_delete','archive')),
        marked_at timestamptz NOT NULL DEFAULT now(),
        collect_after timestamptz NOT NULL,
        collected_at timestamptz,
        error text
      );

      CREATE UNIQUE INDEX IF NOT EXISTS asset_gc_open_key_idx
        ON stac_higher.asset_gc (object_key)
        WHERE collected_at IS NULL;
      CREATE INDEX IF NOT EXISTS asset_gc_due_idx
        ON stac_higher.asset_gc (collect_after)
        WHERE collected_at IS NULL;
    `,
  },
  {
    // M2-G (M2 spec §6, ADR 0012): time-partition the two pure append-only
    // high-volume tables — item_events (outbox) and audit_log — via
    // ATTACH-DON'T-COPY: rename the existing table, create a partitioned
    // parent under the original name, attach the old table as a bounded
    // legacy partition covering (MINVALUE, start of next month). No data
    // copy, no long lock; correct whether the table holds 0 rows or 10M.
    // Monthly partitions from next month onward are created by
    // RECONCILE_PARTITIONS_SQL on every runMigrations() (two-month cushion),
    // so their ranges can never overlap the legacy bound.
    //
    // Notes the shape depends on:
    // - A partitioned PK must include the partition key → PK becomes
    //   (id, occurred_at) / (id, at). Nothing FKs these tables and both ids
    //   come from a sequence, so per-table id uniqueness is preserved in
    //   practice; the dispatcher's id-based claim/UPDATE works unchanged.
    // - The bigserial sequences were OWNED BY the old tables' columns; they
    //   are re-owned by the parents so a future legacy-partition DROP cannot
    //   take the sequence with it.
    // - Legacy indexes are renamed first so the parent's partitioned indexes
    //   can take the canonical names (ATTACH then adopts/creates per-child
    //   indexes automatically).
    // - audit_log's append-only row triggers are recreated ON THE PARENT
    //   (row triggers propagate to partitions); the TRUNCATE guard fires for
    //   parent-level TRUNCATE. Retention drops whole partitions via
    //   DETACH+DROP, which row triggers cannot veto — that is the deliberate
    //   escape hatch the migration-003 comment demanded (ADR 0012).
    //
    // delivery_log / ingest_files / connection_checks are deliberately NOT
    // partitioned: their natural UNIQUE keys ((association_id, item_id) /
    // (association_id, source_path, version)) cannot include a time
    // partition key without breaking the upsert model. They get pipeline
    // retention sweeps instead (ADR 0012; amends I-36/I-11, closes I-12).
    name: "018_partition_item_events_and_audit_log",
    sql: `
      DO $mig$
      DECLARE
        next_month timestamptz := date_trunc('month', now()) + interval '1 month';
      BEGIN
        -- ------------------------------------------------------------------
        -- item_events (outbox; migration 007)
        -- ------------------------------------------------------------------
        ALTER TABLE stac_higher.item_events RENAME TO item_events_legacy;
        ALTER INDEX stac_higher.item_events_pending_idx
          RENAME TO item_events_legacy_pending_idx;
        -- The parent's PK (id, occurred_at) propagates to partitions on
        -- ATTACH; the legacy PK(id) would collide — drop it (id uniqueness
        -- is sequence-guaranteed; the parent PK index replaces it).
        ALTER TABLE stac_higher.item_events_legacy
          DROP CONSTRAINT item_events_pkey;

        -- Full current shape: 007 base + the 011 claim marker + the 012
        -- visibility-retry columns. A column added to item_events later MUST
        -- be added here too if that migration can ever run before this one
        -- on a fresh DB (it cannot — order is fixed — but keep in sync).
        CREATE TABLE stac_higher.item_events (
          id bigint NOT NULL DEFAULT nextval('stac_higher.item_events_id_seq'),
          collection_id text NOT NULL,
          item_id text NOT NULL,
          -- Named to MATCH the legacy table's auto-named 007 constraint —
          -- ATTACH requires the child to carry the parent's checks by name.
          op text NOT NULL
            CONSTRAINT item_events_op_check
            CHECK (op IN ('insert','update','delete')),
          occurred_at timestamptz NOT NULL DEFAULT now(),
          processed_at timestamptz,
          claimed_at timestamptz,
          dispatch_attempts integer NOT NULL DEFAULT 0,
          next_dispatch_at timestamptz,
          PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at);
        ALTER SEQUENCE stac_higher.item_events_id_seq
          OWNED BY stac_higher.item_events.id;
        CREATE INDEX item_events_pending_idx
          ON stac_higher.item_events (id) WHERE processed_at IS NULL;

        EXECUTE format(
          'ALTER TABLE stac_higher.item_events'
          || ' ATTACH PARTITION stac_higher.item_events_legacy'
          || ' FOR VALUES FROM (MINVALUE) TO (%L)',
          next_month
        );

        -- ------------------------------------------------------------------
        -- audit_log (append-only; migration 003)
        -- ------------------------------------------------------------------
        ALTER TABLE stac_higher.audit_log RENAME TO audit_log_legacy;
        ALTER TABLE stac_higher.audit_log_legacy
          DROP CONSTRAINT audit_log_pkey;
        -- The parent's row trigger clones onto every partition at ATTACH;
        -- the legacy table's own 003 triggers would collide by name. The
        -- TRUNCATE guard is parent-level only (statement triggers don't
        -- propagate) — truncating/dropping a DETACHED partition is the
        -- deliberate retention escape hatch (ADR 0012).
        DROP TRIGGER audit_log_append_only ON stac_higher.audit_log_legacy;
        DROP TRIGGER audit_log_no_truncate ON stac_higher.audit_log_legacy;
        ALTER INDEX stac_higher.audit_log_at_idx RENAME TO audit_log_legacy_at_idx;
        ALTER INDEX stac_higher.audit_log_actor_groups_idx
          RENAME TO audit_log_legacy_actor_groups_idx;

        CREATE TABLE stac_higher.audit_log (
          id bigint NOT NULL DEFAULT nextval('stac_higher.audit_log_id_seq'),
          actor TEXT NOT NULL,
          actor_groups TEXT[] NOT NULL DEFAULT '{}',
          action TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          resource_id TEXT,
          detail JSONB NOT NULL DEFAULT '{}'::jsonb,
          at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (id, at)
        ) PARTITION BY RANGE (at);
        ALTER SEQUENCE stac_higher.audit_log_id_seq
          OWNED BY stac_higher.audit_log.id;
        CREATE INDEX audit_log_at_idx ON stac_higher.audit_log (at DESC);
        CREATE INDEX audit_log_actor_groups_idx
          ON stac_higher.audit_log USING gin (actor_groups);

        CREATE TRIGGER audit_log_append_only
          BEFORE UPDATE OR DELETE ON stac_higher.audit_log
          FOR EACH ROW EXECUTE FUNCTION stac_higher.audit_log_block_mutation();
        CREATE TRIGGER audit_log_no_truncate
          BEFORE TRUNCATE ON stac_higher.audit_log
          FOR EACH STATEMENT EXECUTE FUNCTION stac_higher.audit_log_block_mutation();

        EXECUTE format(
          'ALTER TABLE stac_higher.audit_log'
          || ' ATTACH PARTITION stac_higher.audit_log_legacy'
          || ' FOR VALUES FROM (MINVALUE) TO (%L)',
          next_month
        );
      END
      $mig$;
    `,
  },
  {
    // OGC serving exposure (pre-M5 hardening, pulled forward from Phase 8's
    // stretch). LINK-LEVEL only: the flag controls whether the collection
    // page ADVERTISES the local titiler-pgstac / tipg endpoints — it does
    // not gate requests to those services (that needs I-1's read-visibility
    // work). App-only column; the pipeline never reads it.
    name: "019_collection_settings_serving_enabled",
    sql: `
      ALTER TABLE stac_higher.collection_settings
        ADD COLUMN IF NOT EXISTS serving_enabled boolean NOT NULL DEFAULT false;
    `,
  },
  {
    // P7-C (Phase 7 push-ingest spec §4.1/§11): the staged-upload ledger.
    // One row per mint of `POST /api/uploads` in staged mode — the
    // authorization binding finalize checks (staged refs are only honored
    // for the collection their session was minted for) AND where a push
    // client polls the async verdict (`GET /api/uploads/{uploadId}`).
    //
    // Ownership split mirrors ingest_files: the APP inserts (`pending`) and
    // reads; the PIPELINE writes status/result/claimed_at/finalized_at
    // (finalize recorder + sweeps) and never DDL (ADR 0001). `id` doubles as
    // the `upload_id` in `staging://{upload_id}/{filename}` hrefs and the
    // `staging/{upload_id}/` key prefix. `prior_item` is the §4.3 brokered-PUT
    // snapshot (P7-D writes it; §6.3's restore point). Hygiene: NOT
    // partitioned (poll-verb target, terminal-row pruning via the
    // history_retention sweep — P7-H; ADR 0012 criteria).
    name: "020_staged_uploads",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.staged_uploads (
        id            uuid PRIMARY KEY,
        collection_id text NOT NULL,
        item_id       text,
        created_by    text NOT NULL,
        group_id      text,
        filenames     jsonb NOT NULL,
        status        text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','finalizing','finalized','rejected','expired')),
        result        jsonb,
        prior_item    jsonb,
        error         text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        claimed_at    timestamptz,
        finalized_at  timestamptz
      );

      CREATE INDEX IF NOT EXISTS staged_uploads_collection_status_idx
        ON stac_higher.staged_uploads (collection_id, status);
      CREATE INDEX IF NOT EXISTS staged_uploads_status_created_idx
        ON stac_higher.staged_uploads (status, created_at);
    `,
  },
  {
    // P7-H (Phase 7 push-ingest spec §8/§11): the collection anchor for
    // `push_rejected` alerts. Push has no connection / association / channel
    // to anchor on, so alerts grow a fourth — nullable — anchor:
    // `collection_id` as unconstrained text, NO FK, matching
    // collection_settings (collections live in pgstac, out of band).
    //
    // Three things move in LOCKSTEP with the column (the M2-C channel-leg
    // precedent, migration 015):
    // - the anchor CHECK gains the fourth leg (as shipped it would reject
    //   every push_rejected insert),
    // - the open-dedup index gains the fourth coalesce leg (text — no cast),
    // - the pipeline's INSERT conflict targets (flow/repo.py sync_alerts,
    //   notify/repo.py raise_alert) match the index expressions EXACTLY.
    // Recreating index + constraint is safe on a near-empty local table;
    // ordering matters at scale (the M2-G "partition while near-empty"
    // argument).
    //
    // Group scoping for collection-anchored alerts is DERIVED at read time
    // via collection_settings.group_id (lib/alerts/storage.ts) — an unowned
    // collection yields a null group, i.e. admin-only visibility.
    name: "021_alerts_collection_anchor",
    sql: `
      ALTER TABLE stac_higher.alerts
        ADD COLUMN IF NOT EXISTS collection_id text;

      ALTER TABLE stac_higher.alerts
        DROP CONSTRAINT IF EXISTS alerts_anchor_check;
      ALTER TABLE stac_higher.alerts
        ADD CONSTRAINT alerts_anchor_check CHECK (
          connection_id IS NOT NULL
          OR association_id IS NOT NULL
          OR channel_id IS NOT NULL
          OR collection_id IS NOT NULL
        );

      DROP INDEX IF EXISTS stac_higher.alerts_open_dedup_idx;
      CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_dedup_idx
        ON stac_higher.alerts (
          source, kind,
          coalesce(connection_id::text, ''),
          coalesce(association_id::text, ''),
          coalesce(channel_id::text, ''),
          coalesce(collection_id, '')
        )
        WHERE state <> 'resolved';

      CREATE INDEX IF NOT EXISTS alerts_collection_idx
        ON stac_higher.alerts (collection_id);
    `,
  },
  {
    // M5-0 (Phase 9 Processes spec §3; ROADMAP §5 PROCESS_* ER shapes): the
    // process entities. App owns this DDL (ADR 0001); the app writes
    // processes/revisions/sources/outputs and INSERTS process_runs' queued
    // rows only where the spec says so, while the PIPELINE writes run state
    // (status/attempts/output_items/log_ref/timestamps), source flow_stats,
    // and drains process_checks — never DDL. The split mirrors
    // collection_connections/ingest_files exactly.
    //
    // collection_id is TEXT with no FK throughout (pgstac collections are
    // created out of band — same stance as collection_settings and
    // collection_connections). Soft-delete per ADR 0009 lives on `processes`
    // only: sources/outputs/revisions are children of a process and die with
    // it; runs are history and never cascade (ADR 0009's
    // nothing-cascades-into-history stance), so process_runs' FKs are
    // RESTRICT like delivery_log's.
    //
    // Hygiene (§3, confirmed against ADR 0012's criteria): process_runs is
    // deliberately NOT partitioned — runs are verb targets (the audited
    // `rerun`), the delivery_log argument verbatim. Terminal rows age out
    // through the hourly history_retention sweep
    // (PROCESS_RUN_RETENTION_DAYS, default 90), which deletes the log object
    // BEFORE the row (§9) so no orphaned bytes are left behind.
    name: "022_processes",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.processes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        description text NOT NULL DEFAULT '',
        group_id text NOT NULL,
        -- Set by "deploy" (create a revision, then point here). Nullable
        -- because a process exists before its first revision; the FK is
        -- added after process_revisions below.
        current_revision uuid,
        enabled boolean NOT NULL DEFAULT true,
        -- §7 run-rate ceiling, enforced at enqueue. Operator-editable; the
        -- floor of 1 keeps "pause by ceiling" expressible without a zero
        -- that would read as "unlimited".
        max_runs_per_hour integer NOT NULL DEFAULT 60
          CHECK (max_runs_per_hour >= 1),
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz
      );

      CREATE INDEX IF NOT EXISTS processes_group_id_idx
        ON stac_higher.processes (group_id);
      -- Names are unique per owning group among LIVE processes only, so a
      -- soft-deleted process never blocks re-creating one by the same name
      -- (the collection_connections_live_unique_idx precedent, migration 010).
      CREATE UNIQUE INDEX IF NOT EXISTS processes_live_name_idx
        ON stac_higher.processes (group_id, name)
        WHERE deleted_at IS NULL;

      -- Immutable snapshots: "deploy" = INSERT a revision + repoint
      -- processes.current_revision. Every run pins the revision that
      -- executed it, so revisions are never updated or deleted while a run
      -- references them.
      --
      -- env is the §5.6 array envelope ([{name, value} | {name,
      -- secret_ref}]) — plaintext secrets never land here; secret_ref is a
      -- pointer into the §5.2 encrypted-credentials envelope, resolved
      -- pipeline-side at launch and only into the RUN's environment
      -- (ADR 0013's isolation invariant).
      CREATE TABLE IF NOT EXISTS stac_higher.process_revisions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        process_id uuid NOT NULL
          REFERENCES stac_higher.processes(id) ON DELETE RESTRICT,
        runtime jsonb NOT NULL DEFAULT '{}'::jsonb,
        code text,
        env jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      );

      CREATE INDEX IF NOT EXISTS process_revisions_process_idx
        ON stac_higher.process_revisions (process_id, created_at DESC);

      ALTER TABLE stac_higher.processes
        DROP CONSTRAINT IF EXISTS processes_current_revision_fkey;
      ALTER TABLE stac_higher.processes
        ADD CONSTRAINT processes_current_revision_fkey
          FOREIGN KEY (current_revision)
          REFERENCES stac_higher.process_revisions(id) ON DELETE RESTRICT;

      -- What triggers the process. trigger is the §5.6 shape
      -- (item_event | cron); expectation is {run_within_seconds} and lives
      -- PER SOURCE (I-63 decided in the spec §8), mirroring the association
      -- expectation model — the source id takes the association position in
      -- the ADR 0010 dedup key. flow_stats is PIPELINE-written telemetry
      -- (runs/output items/last_run_at/last_error_at/last_duration), read by
      -- the UI and never written app-side, exactly like
      -- collection_connections.flow_stats.
      CREATE TABLE IF NOT EXISTS stac_higher.process_sources (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        process_id uuid NOT NULL
          REFERENCES stac_higher.processes(id) ON DELETE RESTRICT,
        collection_id text NOT NULL,
        trigger jsonb NOT NULL DEFAULT '{}'::jsonb,
        expectation jsonb,
        flow_stats jsonb NOT NULL DEFAULT '{}'::jsonb,
        enabled boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- One source row per (process, collection): a second trigger on the
        -- same collection is an edit, not a new row — which keeps the
        -- cycle-check edge model (M5-D) a simple set of collection→process
        -- edges.
        UNIQUE (process_id, collection_id)
      );

      -- No standalone (process_id) index: UNIQUE (process_id, collection_id)
      -- above already leads with it, and this table takes a write per source
      -- edit. Same reasoning for process_outputs below.
      --
      -- The dispatcher's item_event leg matches landing items against
      -- enabled sources by collection.
      CREATE INDEX IF NOT EXISTS process_sources_collection_enabled_idx
        ON stac_higher.process_sources (collection_id)
        WHERE enabled;

      CREATE TABLE IF NOT EXISTS stac_higher.process_outputs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        process_id uuid NOT NULL
          REFERENCES stac_higher.processes(id) ON DELETE RESTRICT,
        collection_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (process_id, collection_id)
      );

      CREATE INDEX IF NOT EXISTS process_outputs_collection_idx
        ON stac_higher.process_outputs (collection_id);

      -- The run ledger (§6). queued -> running -> succeeded | failed -> dead
      -- on the runtime's RetrySpec budget, with a stall sweep for rows a
      -- crashed worker/executor stranded in 'running'
      -- (PROCESS_RUN_STALL_SECONDS, the M2-0 sweep pattern).
      --
      -- revision_id is pinned at enqueue and NEVER repointed — a re-run of a
      -- dead row re-executes the revision that failed, not whatever is
      -- current. source_id is nullable so a UI test run (process_checks) and
      -- a manual re-run can exist without a trigger source.
      CREATE TABLE IF NOT EXISTS stac_higher.process_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        process_id uuid NOT NULL
          REFERENCES stac_higher.processes(id) ON DELETE RESTRICT,
        revision_id uuid NOT NULL
          REFERENCES stac_higher.process_revisions(id) ON DELETE RESTRICT,
        source_id uuid
          REFERENCES stac_higher.process_sources(id) ON DELETE SET NULL,
        status text NOT NULL DEFAULT 'queued'
          CHECK (status IN ('queued','running','succeeded','failed','dead')),
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        -- One run = N trigger items (§6.7). Empty array for cron/test runs.
        input_items jsonb NOT NULL DEFAULT '[]'::jsonb,
        -- Authoritative record of what finalize upserted (ADR 0014).
        output_items jsonb NOT NULL DEFAULT '[]'::jsonb,
        -- logs/runs/{process_id}/{run_id}.log in the §5.3 layout — a logs/
        -- sibling of assets/ and staging/. Deleted BEFORE this row when the
        -- retention leg prunes it (§9): no orphaned bytes, and asset_gc is
        -- deliberately not involved (logs are platform bytes, not catalog
        -- assets).
        log_ref text,
        error text,
        -- §7: set when the ceiling defers this run. A deferred item_event run
        -- COALESCES — new matches are absorbed into input_items rather than
        -- queueing more runs — so the partial index below is the enqueue
        -- path's lookup.
        rate_deferred_until timestamptz,
        -- Test runs (the process_checks bridge) are ordinary runs, flagged so
        -- the UI and the rate ceiling can treat them apart from triggered work.
        is_test boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        finished_at timestamptz
      );

      -- Deliberately FIVE indexes, not eight. This is the highest-volume
      -- table in the milestone — one row per dispatch plus a non-HOT UPDATE
      -- per queued->running->terminal transition, since every indexed column
      -- below is one the pipeline writes — so each index costs write
      -- throughput at exactly the M3 rates §9 targets. Two candidates were
      -- dropped rather than carried: a (process_id, created_at) index for the
      -- §7 rate window, which the DESC index here already serves (a btree
      -- scans either direction, and direction only matters for mixed-order
      -- multi-column sorts), and a (source_id, created_at DESC) index, which
      -- has no reader — per-source telemetry is the process_sources
      -- flow_stats rollup, history is flow_stats_daily, and the coalescing
      -- lookup has its own partial unique index below. Add either back with
      -- the query that needs it, not before.
      CREATE INDEX IF NOT EXISTS process_runs_process_created_idx
        ON stac_higher.process_runs (process_id, created_at DESC);
      -- The stall sweep scans rows stuck in 'running'.
      CREATE INDEX IF NOT EXISTS process_runs_running_idx
        ON stac_higher.process_runs (started_at)
        WHERE status = 'running';
      -- At most ONE deferred run per source at a time: coalescing is the
      -- §7 requirement, and a unique index is what makes it true under
      -- concurrent dispatch rather than merely intended.
      CREATE UNIQUE INDEX IF NOT EXISTS process_runs_deferred_source_idx
        ON stac_higher.process_runs (source_id)
        WHERE rate_deferred_until IS NOT NULL AND status = 'queued'
          AND source_id IS NOT NULL;
      -- The retention leg prunes terminal rows by age.
      CREATE INDEX IF NOT EXISTS process_runs_terminal_age_idx
        ON stac_higher.process_runs (finished_at)
        WHERE status IN ('succeeded','dead');

      -- The ADR 0004 request-table bridge for UI test runs, shaped exactly
      -- like connection_checks: the app INSERTs 'pending', the pipeline
      -- claims it (FOR UPDATE SKIP LOCKED), turns it into a flagged run, and
      -- writes the result columns back. run_id links to the run it produced.
      CREATE TABLE IF NOT EXISTS stac_higher.process_checks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        process_id uuid NOT NULL
          REFERENCES stac_higher.processes(id) ON DELETE RESTRICT,
        revision_id uuid NOT NULL
          REFERENCES stac_higher.process_revisions(id) ON DELETE RESTRICT,
        requested_by text NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT now(),
        status text NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','running','done','failed')),
        run_id uuid REFERENCES stac_higher.process_runs(id) ON DELETE SET NULL,
        result jsonb,
        finished_at timestamptz
      );

      CREATE INDEX IF NOT EXISTS process_checks_process_idx
        ON stac_higher.process_checks (process_id, requested_at DESC);
      CREATE INDEX IF NOT EXISTS process_checks_pending_idx
        ON stac_higher.process_checks (requested_at)
        WHERE status = 'pending';
    `,
  },
  {
    // M5-0 (Phase 9 spec §10, P9-E decided): the daily flow-stats history
    // that backs the lineage panel's 30-day strip and the /processes
    // sparklines. A TABLE rather than a live GROUP BY because (a) M2-G's
    // history_retention prunes the ledgers those aggregates would read, so
    // derived history silently thins, and (b) at M3 rates a 30-day GROUP BY
    // per page view is the unbounded-aggregation shape M2-A removed from
    // listDeliveries.
    //
    // App owns the DDL; the PIPELINE's daily job UPSERTs one row per subject
    // per day (M5-E), and the hourly history_retention sweep prunes beyond
    // ~400 days — a bounded DELETE, no partitioning (the row count is
    // subjects x days). TODAY's partial bucket is derived live from
    // flow_stats and is deliberately NOT written here, so a mid-day read
    // never shows a half-filled row as final.
    //
    // subject_kind keeps associations and processes in ONE table so the
    // lineage strip is uniform (the P9-E open question, answered yes).
    // Columns are the union of both subjects' counters; a subject leaves the
    // ones it does not produce at zero/NULL.
    name: "023_flow_stats_daily",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.flow_stats_daily (
        subject_kind text NOT NULL CHECK (subject_kind IN ('association','process')),
        subject_id uuid NOT NULL,
        day date NOT NULL,
        files integer NOT NULL DEFAULT 0,
        items integer NOT NULL DEFAULT 0,
        bytes bigint NOT NULL DEFAULT 0,
        delivered integer NOT NULL DEFAULT 0,
        failed integer NOT NULL DEFAULT 0,
        dead integer NOT NULL DEFAULT 0,
        runs integer NOT NULL DEFAULT 0,
        latency_p50_seconds real,
        latency_max_seconds real,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (subject_kind, subject_id, day)
      );

      -- The lineage strip reads one subject's trailing window; the retention
      -- sweep deletes by day across subjects.
      CREATE INDEX IF NOT EXISTS flow_stats_daily_day_idx
        ON stac_higher.flow_stats_daily (day);
    `,
  },
  {
    // M5-E (Phase 9 spec §8): anchors for the three process alert kinds.
    // Alerts previously anchored on connection / association / channel /
    // collection (migrations 014/015/021); none of those fits a process.
    //
    // TWO anchors, because the kinds have genuinely different scopes:
    //   - process_id  — `process_failed` (a dead run belongs to the process;
    //     per-source would fragment one incident across its triggers) and
    //     `process_rate_limited` (the ceiling IS per-process).
    //   - source_id   — `process_stalled`, which is per-SOURCE by I-63:
    //     different sources carry different cron cadences and arrival
    //     profiles, so one stalled trigger must not silence another.
    //
    // The migration-021 lockstep applies, and it is the whole reason this is
    // one migration: the anchor CHECK, the open-dedup index, and BOTH
    // pipeline INSERT conflict targets (flow/repo.py sync_alerts,
    // notify/repo.py raise_alert) must name the same expressions or alert
    // dedup breaks at runtime with no type error anywhere.
    //
    // ON DELETE CASCADE: unlike collection-anchored alerts (collections live
    // in pgstac, out of band), a process row is ours — deleting one should
    // take its alerts with it rather than leave rows anchored to nothing.
    // Group scoping for these alerts derives from processes.group_id at read
    // time (lib/alerts/storage.ts), the same shape as the collection anchor.
    name: "024_alerts_process_anchors",
    sql: `
      ALTER TABLE stac_higher.alerts
        ADD COLUMN IF NOT EXISTS process_id uuid
          REFERENCES stac_higher.processes(id) ON DELETE CASCADE;
      ALTER TABLE stac_higher.alerts
        ADD COLUMN IF NOT EXISTS source_id uuid
          REFERENCES stac_higher.process_sources(id) ON DELETE CASCADE;

      ALTER TABLE stac_higher.alerts
        DROP CONSTRAINT IF EXISTS alerts_anchor_check;
      ALTER TABLE stac_higher.alerts
        ADD CONSTRAINT alerts_anchor_check CHECK (
          connection_id IS NOT NULL
          OR association_id IS NOT NULL
          OR channel_id IS NOT NULL
          OR collection_id IS NOT NULL
          OR process_id IS NOT NULL
          OR source_id IS NOT NULL
        );

      DROP INDEX IF EXISTS stac_higher.alerts_open_dedup_idx;
      CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_dedup_idx
        ON stac_higher.alerts (
          source, kind,
          coalesce(connection_id::text, ''),
          coalesce(association_id::text, ''),
          coalesce(channel_id::text, ''),
          coalesce(collection_id, ''),
          coalesce(process_id::text, ''),
          coalesce(source_id::text, '')
        )
        WHERE state <> 'resolved';

      CREATE INDEX IF NOT EXISTS alerts_process_idx
        ON stac_higher.alerts (process_id);
      CREATE INDEX IF NOT EXISTS alerts_source_idx
        ON stac_higher.alerts (source_id);
    `,
  },
  {
    // G-3 (GOES spec §5): coalescing used to cover only RATE-DEFERRED queued
    // runs (024's partial index on source_id). Once `trigger_run` dispatches
    // immediately, two arrivals milliseconds apart become two runs — the
    // 2026-09-02 live check saw exactly that — so the key widens to EVERY
    // queued run per (process, source). A claim moves the row to `running`,
    // which drops it out of this partial index, so a late arrival correctly
    // starts a NEW run rather than joining one already executing.
    //
    // Pre-existing duplicates are merged first (the earliest row absorbs the
    // others' input_items, the rest are deleted) — otherwise CREATE UNIQUE
    // INDEX would fail on any database that has them.
    name: "025_process_runs_queued_source_idx",
    sql: `
      WITH ranked AS (
        SELECT id, process_id, source_id, input_items,
               row_number() OVER (
                 PARTITION BY process_id, source_id ORDER BY created_at, id
               ) AS rn,
               first_value(id) OVER (
                 PARTITION BY process_id, source_id ORDER BY created_at, id
               ) AS keep_id
          FROM stac_higher.process_runs
         WHERE status = 'queued' AND source_id IS NOT NULL
      ),
      merged AS (
        SELECT keep_id, jsonb_agg(elem ORDER BY rn) AS items
          FROM ranked, jsonb_array_elements(ranked.input_items) AS elem
         WHERE rn > 1
         GROUP BY keep_id
      )
      UPDATE stac_higher.process_runs r
         SET input_items = r.input_items || m.items
        FROM merged m
       WHERE r.id = m.keep_id;

      DELETE FROM stac_higher.process_runs
       WHERE id IN (
         SELECT id FROM (
           SELECT id, row_number() OVER (
                    PARTITION BY process_id, source_id ORDER BY created_at, id
                  ) AS rn
             FROM stac_higher.process_runs
            WHERE status = 'queued' AND source_id IS NOT NULL
         ) d WHERE d.rn > 1
       );

      DROP INDEX IF EXISTS stac_higher.process_runs_deferred_source_idx;
      CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_source_idx
        ON stac_higher.process_runs (process_id, source_id)
        WHERE status = 'queued' AND source_id IS NOT NULL;
    `,
  },
  {
    // W-2 (spec §4): a COUNT cap beside the age cap. Both are retention, both
    // feed ADR 0011's single mark-then-collect queue, so this adds a rule to
    // an existing sweep rather than a new way to delete.
    //
    // Nullable = no cap. The CHECK floors it at 1 because "keep zero" is not
    // a retention policy — that is `archived`, which already exists and also
    // blocks writes.
    name: "026_collection_settings_retention_max_items",
    sql: `
      ALTER TABLE stac_higher.collection_settings
        ADD COLUMN IF NOT EXISTS retention_max_items integer;

      ALTER TABLE stac_higher.collection_settings
        DROP CONSTRAINT IF EXISTS collection_settings_retention_max_items_check;
      ALTER TABLE stac_higher.collection_settings
        ADD CONSTRAINT collection_settings_retention_max_items_check
        CHECK (retention_max_items IS NULL OR retention_max_items >= 1);
    `,
  },
  {
    // GOES spec §6 + §15 (G-6, extractors).
    //
    // processes.kind — a process is a `transform` (sources → outputs) or an
    // `extractor` (selected on an ingest association, fixes up the draft
    // item before it is catalogued). Immutable after create: the app never
    // puts it in the update path, the `current_revision` precedent.
    //
    // ingest_files — a new `extracting` status between `stored` and
    // `itemized`; `reason` carries the failure text §6.2 promises (the
    // ledger never had one); `extract_run_id` links a row to the run that
    // owns it, so the recovery sweep can tell a live run from a vanished
    // one; `source_mtime` is the listed object modified time DISCOVER used
    // to throw away (I-100) so `file_mtime` can stop meaning "settle time".
    //
    // process_runs.association_id — extractor runs coalesce per
    // (process, association): §6.4 keyed coalescing on source_id, but an
    // extractor has no process_sources rows by §6.1, and enqueue_run only
    // takes the ON CONFLICT path with a source. A second partial unique
    // index is the arbiter, the same shape as 025's.
    name: "027_extractors",
    sql: `
      ALTER TABLE stac_higher.processes
        ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'transform';
      ALTER TABLE stac_higher.processes
        DROP CONSTRAINT IF EXISTS processes_kind_check;
      ALTER TABLE stac_higher.processes
        ADD CONSTRAINT processes_kind_check
        CHECK (kind IN ('transform', 'extractor'));

      ALTER TABLE stac_higher.ingest_files
        DROP CONSTRAINT IF EXISTS ingest_files_status_check;
      ALTER TABLE stac_higher.ingest_files
        ADD CONSTRAINT ingest_files_status_check
        CHECK (status IN ('seen', 'settled', 'fetching', 'stored', 'extracting', 'itemized', 'failed'));
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS reason text;
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS extract_run_id uuid;
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS source_mtime timestamptz;

      ALTER TABLE stac_higher.process_runs
        ADD COLUMN IF NOT EXISTS association_id uuid
          REFERENCES stac_higher.collection_connections(id) ON DELETE SET NULL;
      CREATE INDEX IF NOT EXISTS process_runs_association_id_idx
        ON stac_higher.process_runs (association_id);
      CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_association_idx
        ON stac_higher.process_runs (process_id, association_id)
        WHERE status = 'queued' AND association_id IS NOT NULL;
    `,
  },
  {
    // X-4 (X-queue spec §7): a process created from the built-in extractor
    // registry carries the registry id it was instantiated from. NULL for
    // every hand-written process; the partial unique index is the
    // create-or-reuse arbiter — one LIVE built-in process per (group,
    // registry id), and a soft-deleted one never blocks re-creating it.
    // Migration number settled 2026-09-04 (X is worked first; K-3 takes 029).
    name: "028_builtin_processes",
    sql: `
      ALTER TABLE stac_higher.processes
        ADD COLUMN IF NOT EXISTS builtin_id text;
      CREATE UNIQUE INDEX IF NOT EXISTS processes_live_builtin_idx
        ON stac_higher.processes (group_id, builtin_id)
        WHERE builtin_id IS NOT NULL AND deleted_at IS NULL;
    `,
  },
];

// Idempotent reconcile: attach the outbox trigger to pgstac.items whenever that
// table exists. Runs on EVERY runMigrations() call (NOT a tracked once-only
// migration) so a pgstac created AFTER the app's first migration — the
// deploy-ordering race, or an app DB role that could not yet see pgstac.items —
// still gets the trigger on the next boot instead of being permanently skipped.
// Depends on stac_higher.item_events_capture() (migration 007), so it runs after
// the migration loop. A no-op when pgstac.items is absent (unit/CI DBs).
const RECONCILE_OUTBOX_TRIGGER_SQL = `
  DO $do$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'pgstac' AND table_name = 'items'
    ) AND EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'stac_higher' AND p.proname = 'item_events_capture'
    ) THEN
      DROP TRIGGER IF EXISTS item_events_capture_trg ON pgstac.items;
      CREATE TRIGGER item_events_capture_trg
        AFTER INSERT OR UPDATE OR DELETE ON pgstac.items
        FOR EACH ROW EXECUTE FUNCTION stac_higher.item_events_capture();
    END IF;
  END;
  $do$;
`;

// Idempotent reconcile (M2-G, ADR 0012): keep monthly partitions provisioned
// for item_events and audit_log — next month and the one after, on EVERY
// runMigrations() call. The migration-018 legacy partition covers everything
// through the END of its migration month, so the earliest month this can ever
// create is migration-month + 1: reconciled ranges can never overlap the
// legacy bound. Two months of cushion means inserts keep routing even if the
// app goes a full month between calls; a >2-month total outage would surface
// as insert errors on the outbox trigger — recovered by any app request.
const RECONCILE_PARTITIONS_SQL = `
  DO $do$
  DECLARE
    tbl text;
    m int;
    month_start timestamptz;
    part_name text;
  BEGIN
    FOREACH tbl IN ARRAY ARRAY['item_events', 'audit_log'] LOOP
      IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'stac_higher' AND c.relname = tbl AND c.relkind = 'p'
      ) THEN
        FOR m IN 1..2 LOOP
          month_start := date_trunc('month', now()) + make_interval(months => m);
          part_name := format('%s_y%sm%s', tbl,
            to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
          EXECUTE format(
            'CREATE TABLE IF NOT EXISTS stac_higher.%I'
            || ' PARTITION OF stac_higher.%I FOR VALUES FROM (%L) TO (%L)',
            part_name, tbl, month_start, month_start + interval '1 month'
          );
        END LOOP;
      END IF;
    END LOOP;
  END;
  $do$;
`;

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

    // One round-trip for the whole applied set rather than one per migration:
    // runMigrations() blocks the first API request while holding the advisory
    // lock, and that per-migration SELECT grew with every migration added.
    // The lock makes reading the set once equivalent to re-reading it.
    const applied = await client.query<{ name: string }>(
      `SELECT name FROM stac_higher.migrations`,
    );
    const done = new Set(applied.rows.map((row) => row.name));

    for (const migration of MIGRATIONS) {
      if (done.has(migration.name)) continue;
      await client.query(migration.sql);
      await client.query(
        `INSERT INTO stac_higher.migrations (name) VALUES ($1)`,
        [migration.name],
      );
    }

    // Runs every call (not tracked): (re)attaches the outbox trigger once
    // pgstac.items exists, even if migration 007 was recorded before it did.
    await client.query(RECONCILE_OUTBOX_TRIGGER_SQL);
    // Runs every call (not tracked): provision upcoming monthly partitions
    // for the migration-018 partitioned tables (M2-G, ADR 0012).
    await client.query(RECONCILE_PARTITIONS_SQL);

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }

  migrated = true;
}
