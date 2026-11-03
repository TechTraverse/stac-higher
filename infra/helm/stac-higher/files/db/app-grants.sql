-- app-migrate, after the app's migrations (runs as the owner). Idempotent.
-- Reads stac_higher_sql.proxy_role. The columns are exactly what
-- services/proxy-policy/src/stac_higher_proxy_policy/db.py selects.
DO $app_grants$
DECLARE
  proxy text := current_setting('stac_higher_sql.proxy_role');
BEGIN
  EXECUTE format('GRANT USAGE ON SCHEMA stac_higher TO %I', proxy);
  EXECUTE format(
    'GRANT SELECT (collection_id, externally_writable, archived) '
    'ON stac_higher.collection_settings TO %I',
    proxy
  );
END
$app_grants$;
