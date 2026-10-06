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
  last_maintenance, last_error, created_by, created_at, updated_at`;

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
 * everything the pipeline wrote. */
export async function upsertCubeSink(input: {
  cubeCollectionId: string;
  sourceCollectionId: string;
  config: CubeSinkConfig;
  enabled: boolean;
  createdBy: string;
}): Promise<{ sink: ApiCubeSink; created: boolean }> {
  await runMigrations();
  const result = await query<CubeSinkRow & { created: boolean }>(
    `INSERT INTO stac_higher.cube_sinks
       (cube_collection_id, source_collection_id, config, enabled, created_by)
     VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (cube_collection_id) DO UPDATE
       SET source_collection_id = EXCLUDED.source_collection_id,
           config = EXCLUDED.config,
           enabled = EXCLUDED.enabled,
           updated_at = now()
     RETURNING ${COLUMNS}, (xmax = 0) AS created`,
    [input.cubeCollectionId, input.sourceCollectionId, JSON.stringify(input.config), input.enabled, input.createdBy],
  );
  const { created, ...row } = result.rows[0];
  return { sink: toApi(row as CubeSinkRow), created };
}

export async function setCubeSinkEnabled(
  cubeCollectionId: string,
  enabled: boolean,
): Promise<ApiCubeSink | null> {
  await runMigrations();
  const result = await query<CubeSinkRow>(
    `UPDATE stac_higher.cube_sinks SET enabled = $2, updated_at = now()
      WHERE cube_collection_id = $1 RETURNING ${COLUMNS}`,
    [cubeCollectionId, enabled],
  );
  return result.rows[0] ? toApi(result.rows[0]) : null;
}

/** The repository stays until the collection is deleted (asset_gc), so a
 * delete is reversible by re-creating the sink while the window holds. */
export async function deleteCubeSink(cubeCollectionId: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.cube_sinks WHERE cube_collection_id = $1`,
    [cubeCollectionId],
  );
  return (result.rowCount ?? 0) > 0;
}

export async function deleteCubeSinksForCollection(collectionId: string): Promise<number> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.cube_sinks
      WHERE source_collection_id = $1 OR cube_collection_id = $1`,
    [collectionId],
  );
  return result.rowCount ?? 0;
}

/** Collection-delete hook: never fails a delete the catalog already applied. */
export async function deleteCubeSinksForCollectionTolerant(collectionId: string): Promise<void> {
  try {
    await deleteCubeSinksForCollection(collectionId);
  } catch (err) {
    console.error(
      `[cubes] failed to delete cube sinks for deleted collection ${collectionId}:`,
      err instanceof Error ? err.message : err,
    );
  }
}

/** Whether `_cube` is a reserved item id here: any sink, enabled or not. */
export async function isCubeCollection(collectionId: string): Promise<boolean> {
  await runMigrations();
  const result = await query<{ exists: boolean }>(
    `SELECT EXISTS (SELECT 1 FROM stac_higher.cube_sinks WHERE cube_collection_id = $1) AS exists`,
    [collectionId],
  );
  return result.rows[0]?.exists === true;
}

export async function cubeLedgerSummary(sinkId: string): Promise<CubeLedgerSummary> {
  await runMigrations();
  const counts = Object.fromEntries(CUBE_APPEND_STATUSES.map((s) => [s, 0])) as Record<CubeAppendStatus, number>;
  const grouped = await query<{ status: CubeAppendStatus; count: string }>(
    `SELECT status, count(*) AS count FROM stac_higher.cube_appends
      WHERE cube_sink_id = $1 GROUP BY status`,
    [sinkId],
  );
  for (const r of grouped.rows) counts[r.status] = Number(r.count);
  const recent = await query<Omit<CubeLedgerRow, "item_datetime" | "updated_at"> & { item_datetime: Date; updated_at: Date }>(
    `SELECT item_id, item_datetime, status, reason, snapshot_id, attempts, updated_at
       FROM stac_higher.cube_appends WHERE cube_sink_id = $1
      ORDER BY item_datetime DESC LIMIT 20`,
    [sinkId],
  );
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
