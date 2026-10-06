/**
 * cube_sinks / cube_appends persistence (virtual cube spec §4, §7). The app
 * owns the rows' user-facing fields (config, source, enabled); the pipeline
 * writes source_prefixes, last_* and the ledger. Collections live in pgstac,
 * so existence is checked there and cleanup on collection delete is explicit.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "@/lib/associations/storage";
import type { CubeSinkConfig } from "./schemas";
import { CUBE_APPEND_STATUSES, type CubeAppendStatus } from "./status";

export interface ApiCubeSink {
  id: string;
  source_collection_id: string;
  cube_collection_id: string;
  enabled: boolean;
  config: CubeSinkConfig;
  source_prefixes: string[];
  last_snapshot_id: string | null;
  last_appended_at: string | null;
  last_maintained_at: string | null;
  last_maintenance: unknown;
  last_error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  /** Opaque row version for optimistic writes: `updated_at` at full
   * (microsecond) precision, which the ISO `updated_at` drops. Only the app
   * writes `updated_at` — pipeline writes must leave it alone. */
  version: string;
}

interface CubeSinkRow extends Omit<ApiCubeSink, "last_appended_at" | "last_maintained_at" | "created_at" | "updated_at"> {
  last_appended_at: Date | null;
  last_maintained_at: Date | null;
  created_at: Date;
  updated_at: Date;
}

export interface CubeLedgerRow {
  item_id: string;
  item_datetime: string;
  status: CubeAppendStatus;
  reason: string | null;
  snapshot_id: string | null;
  attempts: number;
  updated_at: string;
}

export interface CubeLedgerSummary {
  counts: Record<CubeAppendStatus, number>;
  recent: CubeLedgerRow[];
}

export interface ReferenceIngestSource {
  association_id: string;
  connection_id: string;
  anonymous: boolean;
}

const COLUMNS = `id, source_collection_id, cube_collection_id, enabled, config,
  source_prefixes, last_snapshot_id, last_appended_at, last_maintained_at,
  last_maintenance, last_error, created_by, created_at, updated_at,
  updated_at::text AS version`;

function toApi(row: CubeSinkRow): ApiCubeSink {
  return {
    id: row.id,
    source_collection_id: row.source_collection_id,
    cube_collection_id: row.cube_collection_id,
    enabled: row.enabled,
    config: row.config,
    source_prefixes: row.source_prefixes,
    last_snapshot_id: row.last_snapshot_id,
    last_appended_at: iso(row.last_appended_at),
    last_maintained_at: iso(row.last_maintained_at),
    last_maintenance: row.last_maintenance,
    last_error: row.last_error,
    created_by: row.created_by,
    created_at: iso(row.created_at),
    updated_at: iso(row.updated_at),
    version: row.version,
  };
}

export async function getCubeSink(cubeCollectionId: string): Promise<ApiCubeSink | null> {
  await runMigrations();
  const result = await query<CubeSinkRow>(
    `SELECT ${COLUMNS} FROM stac_higher.cube_sinks WHERE cube_collection_id = $1`,
    [cubeCollectionId],
  );
  return result.rows[0] ? toApi(result.rows[0]) : null;
}

/** Create or replace. A replace keeps the id, the creator, the ledger and
 * everything the pipeline wrote.
 *
 * Optimistic (#98): the write applies only if the row is still what the
 * caller read and ran its checks against —
 *   - `expected.version`: no other app write since (null = no row yet, so a
 *     concurrent create is caught too); and
 *   - `expected.hasRepository`: whether a repository existed. This flips
 *     once, on the first append; later appends move `last_snapshot_id` but
 *     must not refuse edits the layout lock allows.
 * Otherwise nothing is written or purged and this returns null. */
export async function upsertCubeSink(input: {
  cubeCollectionId: string;
  sourceCollectionId: string;
  config: CubeSinkConfig;
  enabled: boolean;
  createdBy: string;
  /** The source changed and no repository exists yet: clear the previous
   * source's prefixes, last error and ledger in the same statement. */
  resetSourceState?: boolean;
  expected: { version: string | null; hasRepository: boolean };
}): Promise<{ sink: ApiCubeSink; created: boolean } | null> {
  await runMigrations();
  const result = await query<CubeSinkRow & { created: boolean }>(
    `WITH up AS (
       INSERT INTO stac_higher.cube_sinks
         (cube_collection_id, source_collection_id, config, enabled, created_by)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (cube_collection_id) DO UPDATE
         SET source_collection_id = EXCLUDED.source_collection_id,
             config = EXCLUDED.config,
             enabled = EXCLUDED.enabled,
             source_prefixes = CASE WHEN $6::boolean THEN '{}'::text[] ELSE cube_sinks.source_prefixes END,
             last_error = CASE WHEN $6::boolean THEN NULL ELSE cube_sinks.last_error END,
             updated_at = now()
         WHERE cube_sinks.updated_at IS NOT DISTINCT FROM $7::timestamptz
           AND (cube_sinks.last_snapshot_id IS NOT NULL) = $8::boolean
       RETURNING ${COLUMNS}, (xmax = 0) AS created
     ), purge AS (
       DELETE FROM stac_higher.cube_appends a USING up
        WHERE $6::boolean AND a.cube_sink_id = up.id
     )
     SELECT * FROM up`,
    [
      input.cubeCollectionId,
      input.sourceCollectionId,
      JSON.stringify(input.config),
      input.enabled,
      input.createdBy,
      input.resetSourceState === true,
      input.expected.version,
      input.expected.hasRepository,
    ],
  );
  if (!result.rows[0]) return null;
  const { created, ...row } = result.rows[0];
  return { sink: toApi(row as CubeSinkRow), created };
}

/** Enabling clears a stale `last_error` (e.g. `source_collection_deleted`
 * after the source came back); disabling keeps it. */
export async function setCubeSinkEnabled(
  cubeCollectionId: string,
  enabled: boolean,
): Promise<ApiCubeSink | null> {
  await runMigrations();
  const result = await query<CubeSinkRow>(
    `UPDATE stac_higher.cube_sinks
        SET enabled = $2,
            last_error = CASE WHEN $2 THEN NULL ELSE last_error END,
            updated_at = now()
      WHERE cube_collection_id = $1 RETURNING ${COLUMNS}`,
    [cubeCollectionId, enabled],
  );
  return result.rows[0] ? toApi(result.rows[0]) : null;
}

/** Deletes only while no repository exists. Once one does, the row holds
 * its layout lock and ledger, so it goes only with the cube collection. */
export async function deleteCubeSink(cubeCollectionId: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.cube_sinks
      WHERE cube_collection_id = $1 AND last_snapshot_id IS NULL`,
    [cubeCollectionId],
  );
  return (result.rowCount ?? 0) > 0;
}

/** Collection delete: the sink whose CUBE it was goes (its repository rides
 * the collection's GC mark); sinks it SOURCED are disabled with
 * `last_error = source_collection_deleted`, keeping their lock and ledger. */
export async function cubeSinksOnCollectionDelete(
  collectionId: string,
): Promise<{ deleted: number; disabled: number }> {
  await runMigrations();
  const result = await query<{ deleted: string; disabled: string }>(
    `WITH gone AS (
       DELETE FROM stac_higher.cube_sinks WHERE cube_collection_id = $1 RETURNING id
     ), orphaned AS (
       UPDATE stac_higher.cube_sinks
          SET enabled = false, last_error = 'source_collection_deleted', updated_at = now()
        WHERE source_collection_id = $1
       RETURNING id
     )
     SELECT (SELECT count(*) FROM gone) AS deleted, (SELECT count(*) FROM orphaned) AS disabled`,
    [collectionId],
  );
  const row = result.rows[0];
  return { deleted: Number(row?.deleted ?? 0), disabled: Number(row?.disabled ?? 0) };
}

/** Never fails a collection delete the catalog already applied. */
export async function cubeSinksOnCollectionDeleteTolerant(collectionId: string): Promise<void> {
  try {
    await cubeSinksOnCollectionDelete(collectionId);
  } catch (err) {
    console.error(
      `[cubes] failed to update cube sinks for deleted collection ${collectionId}:`,
      err instanceof Error ? err.message : err,
    );
  }
}

export async function cubeLedgerSummary(sinkId: string): Promise<CubeLedgerSummary> {
  await runMigrations();
  const counts = Object.fromEntries(CUBE_APPEND_STATUSES.map((s) => [s, 0])) as Record<CubeAppendStatus, number>;
  const [grouped, recent] = await Promise.all([
    query<{ status: CubeAppendStatus; count: string }>(
      `SELECT status, count(*) AS count FROM stac_higher.cube_appends
        WHERE cube_sink_id = $1 GROUP BY status`,
      [sinkId],
    ),
    query<Omit<CubeLedgerRow, "item_datetime" | "updated_at"> & { item_datetime: Date; updated_at: Date }>(
      `SELECT item_id, item_datetime, status, reason, snapshot_id, attempts, updated_at
         FROM stac_higher.cube_appends WHERE cube_sink_id = $1
        ORDER BY item_datetime DESC LIMIT 20`,
      [sinkId],
    ),
  ]);
  for (const r of grouped.rows) counts[r.status] = Number(r.count);
  return {
    counts,
    recent: recent.rows.map((r) => ({ ...r, item_datetime: iso(r.item_datetime), updated_at: iso(r.updated_at) })),
  };
}

export async function collectionHasItem(collectionId: string, itemId: string): Promise<boolean> {
  const result = await query<{ exists: boolean }>(
    `SELECT EXISTS (SELECT 1 FROM pgstac.items WHERE collection = $1 AND id = $2) AS exists`,
    [collectionId, itemId],
  );
  return result.rows[0]?.exists === true;
}

export async function existingCollections(ids: string[]): Promise<Set<string>> {
  const result = await query<{ id: string }>(
    `SELECT id FROM pgstac.collections WHERE id = ANY($1::text[])`,
    [ids],
  );
  return new Set(result.rows.map((r) => r.id));
}

/** The source's enabled, live, reference-mode ingest associations and
 * whether each connection reads anonymously (spec §7, §13). */
export async function referenceIngestSources(collectionId: string): Promise<ReferenceIngestSource[]> {
  await runMigrations();
  const result = await query<ReferenceIngestSource>(
    `SELECT cc.id AS association_id, cc.connection_id,
            COALESCE(c.config->'anonymous' = 'true'::jsonb, false) AS anonymous
       FROM stac_higher.collection_connections cc
       JOIN stac_higher.connections c ON c.id = cc.connection_id AND c.deleted_at IS NULL
      WHERE cc.collection_id = $1
        AND cc.direction = 'ingest'
        AND cc.enabled
        AND cc.deleted_at IS NULL
        AND cc.config->>'storage_mode' = 'reference'`,
    [collectionId],
  );
  return result.rows;
}
