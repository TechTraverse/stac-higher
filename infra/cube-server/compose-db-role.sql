-- Local compose only (the `cube-db-role` one-shot): the cube-server's LOGIN
-- user, a member of migration 033's read-only group role. Idempotent, and
-- order-independent with the app's migrations: whichever runs first creates
-- the NOLOGIN group, and grants to it reach this member whenever they land.
-- Production creates its own login with its own secret (docs/backend.md).
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stac_higher_cube_reader') THEN
    CREATE ROLE stac_higher_cube_reader NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cube_server') THEN
    CREATE ROLE cube_server LOGIN PASSWORD 'cube_server';
  END IF;
END
$do$;
GRANT stac_higher_cube_reader TO cube_server;
