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

    // Runs every call (not tracked): (re)attaches the outbox trigger once
    // pgstac.items exists, even if migration 007 was recorded before it did.
    await client.query(RECONCILE_OUTBOX_TRIGGER_SQL);

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }

  migrated = true;
}
