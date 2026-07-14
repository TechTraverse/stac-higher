import { query } from "@/lib/db/connection";
import { SafeFetchError, safeFetch } from "@/lib/http/safe-fetch";

const TTL_MS = 5 * 60 * 1000;

interface SchemaCacheRow {
  schema: unknown;
}

export async function getCachedSchema(url: string): Promise<unknown | null> {
  const result = await query<SchemaCacheRow>(
    `SELECT schema FROM stac_higher.schema_cache
     WHERE url = $1 AND expires_at > now()`,
    [url],
  );
  return result.rows[0]?.schema ?? null;
}

export async function setCachedSchema(
  url: string,
  schema: unknown,
): Promise<void> {
  const expiresAt = new Date(Date.now() + TTL_MS);
  await query(
    `INSERT INTO stac_higher.schema_cache (url, schema, fetched_at, expires_at)
     VALUES ($1, $2::jsonb, now(), $3)
     ON CONFLICT (url) DO UPDATE
       SET schema = EXCLUDED.schema,
           fetched_at = EXCLUDED.fetched_at,
           expires_at = EXCLUDED.expires_at`,
    [url, JSON.stringify(schema), expiresAt],
  );
}

export async function getOrFetchSchema(url: string): Promise<unknown> {
  const cached = await getCachedSchema(url);
  if (cached !== null) return cached;

  const result = await safeFetch(url);
  if (result.status < 200 || result.status >= 300) {
    throw new SafeFetchError(
      `Failed to fetch schema from ${url}: ${result.status}`,
      "upstream",
      502,
    );
  }
  const text = new TextDecoder().decode(result.body);
  const schema = JSON.parse(text);
  await setCachedSchema(url, schema);
  return schema;
}
