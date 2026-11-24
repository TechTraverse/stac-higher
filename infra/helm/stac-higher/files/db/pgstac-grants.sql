-- pgstac-migrate, after `pypgstac migrate` (runs as the owner). Idempotent.
-- Reads stac_higher_sql.{api,reader}_role.
DO $pgstac_grants$
BEGIN
  EXECUTE format('GRANT pgstac_ingest TO %I', current_setting('stac_higher_sql.api_role'));
  EXECUTE format('GRANT pgstac_read TO %I', current_setting('stac_higher_sql.reader_role'));
END
$pgstac_grants$;
