-- db-bootstrap (runs as the RDS master). Idempotent; safe on every upgrade.
-- Reads stac_higher_sql.{owner,api,reader,proxy,cube}_{role,password}, set by
-- run_sql.py. The runner hashes each password client-side into a SCRAM-SHA-256
-- verifier before it is sent, so the plaintext never reaches the server or its
-- logs; the *_password settings hold verifiers, and the plaintext still logs
-- in. Statements are built with format(%I / %L) inside DO blocks. A failure
-- while configuring a role is re-raised without the statement text, so even
-- the verifier stays out of the CONTEXT line. A missing setting raises
-- "unrecognized configuration parameter"; an empty or colliding one raises
-- below. No SUPERUSER, REPLICATION or BYPASSRLS: the RDS master cannot grant them.

DO $bootstrap_roles$
DECLARE
  spec record;
  rname text;
  rpass text;
  seen text[] := ARRAY[]::text[];
BEGIN
  FOR spec IN
    SELECT * FROM (VALUES
      ('owner',  true),
      ('api',    false),
      ('reader', false),
      ('proxy',  false),
      ('cube',   false)
    ) AS t(key, createrole)
  LOOP
    rname := current_setting('stac_higher_sql.' || spec.key || '_role');
    rpass := current_setting('stac_higher_sql.' || spec.key || '_password');
    IF coalesce(rname, '') = '' THEN
      RAISE EXCEPTION 'stac_higher_sql.%_role is empty', spec.key;
    END IF;
    IF coalesce(rpass, '') = '' THEN
      RAISE EXCEPTION 'stac_higher_sql.%_password is empty', spec.key;
    END IF;
    IF rname = current_user THEN
      RAISE EXCEPTION 'stac_higher_sql.%_role names the connecting role', spec.key;
    END IF;
    IF rname = ANY (seen) THEN
      RAISE EXCEPTION 'stac_higher_sql.%_role duplicates another role name', spec.key;
    END IF;
    seen := seen || rname;
  END LOOP;

  FOR spec IN
    SELECT * FROM (VALUES
      ('owner',  true),
      ('api',    false),
      ('reader', false),
      ('proxy',  false),
      ('cube',   false)
    ) AS t(key, createrole)
  LOOP
    rname := current_setting('stac_higher_sql.' || spec.key || '_role');
    rpass := current_setting('stac_higher_sql.' || spec.key || '_password');
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rname) THEN
        EXECUTE format('CREATE ROLE %I', rname);
      END IF;
      -- ALTER on every run so a rotated password is applied.
      EXECUTE format(
        'ALTER ROLE %I WITH LOGIN %s PASSWORD %L',
        rname,
        CASE WHEN spec.createrole THEN 'CREATEROLE' ELSE 'NOCREATEROLE' END,
        rpass
      );
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'configuring role % failed: %', rname, SQLERRM
        USING ERRCODE = SQLSTATE;
    END;
  END LOOP;
END
$bootstrap_roles$;

DO $bootstrap_owner$
BEGIN
  EXECUTE format(
    'GRANT CREATE, CONNECT ON DATABASE %I TO %I',
    current_database(),
    current_setting('stac_higher_sql.owner_role')
  );
END
$bootstrap_owner$;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS unaccent;

DO $bootstrap_cube$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stac_higher_cube_reader') THEN
    CREATE ROLE stac_higher_cube_reader NOLOGIN;
  END IF;
  EXECUTE format(
    'GRANT stac_higher_cube_reader TO %I',
    current_setting('stac_higher_sql.cube_role')
  );
END
$bootstrap_cube$;

DO $bootstrap_proxy$
BEGIN
  EXECUTE format(
    'ALTER ROLE %I SET default_transaction_read_only = on',
    current_setting('stac_higher_sql.proxy_role')
  );
END
$bootstrap_proxy$;
