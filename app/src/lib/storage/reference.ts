/**
 * Reference-mode asset lookup (ROADMAP Phase 4 Slice C).
 *
 * `storage_mode: reference` associations catalog items whose bytes stay at the
 * source; the pipeline records the stable source URL in `ingest_files.source_href`
 * at FETCH. This resolves that URL for (collection, item, filename) so the asset
 * route can 302 to the source instead of presigning a canonical object. Returns
 * null for copy-mode / manually-uploaded assets (no referenced row) — the caller
 * then presigns canonical storage.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";

export async function lookupReferenceHref(
  collection: string,
  itemId: string,
  filename: string,
): Promise<string | null> {
  await runMigrations();
  const result = await query<{ source_href: string }>(
    `SELECT f.source_href
       FROM stac_higher.ingest_files f
       JOIN stac_higher.collection_connections cc ON cc.id = f.association_id
      WHERE cc.collection_id = $1
        AND f.item_id = $2
        AND f.source_href IS NOT NULL
        AND regexp_replace(f.source_path, '^.*/', '') = $3
      ORDER BY f.version DESC
      LIMIT 1`,
    [collection, itemId, filename],
  );
  return result.rows[0]?.source_href ?? null;
}
