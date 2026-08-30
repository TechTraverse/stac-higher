/**
 * staged_uploads ledger access — the app's half of the Phase 7 push-ingest
 * bridge (spec §4.1–4.2, migration 020).
 *
 * The APP inserts the `pending` binding row at mint time and reads rows for
 * the poll route; the PIPELINE writes status/result/claimed_at/finalized_at
 * (finalize recorder + sweeps) and never DDL — the ingest_files ownership
 * split, verbatim (ADR 0001/0004).
 *
 * Route-local `runMigrations()` (the gc/marks pattern): this module is called
 * from routes the middleware's migration prefix list may not cover, so every
 * entry point reconciles the schema itself. `runMigrations()` is a cheap
 * no-op after the first call.
 */
import { randomUUID } from "node:crypto";
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "@/lib/associations/storage";
import { getStagingTtlSeconds } from "./config";
import type { StagedUploadStatus } from "./schemas";

interface StagedUploadDbRow {
  id: string;
  collection_id: string;
  item_id: string | null;
  created_by: string;
  group_id: string | null;
  filenames: string[];
  status: StagedUploadStatus;
  result: unknown;
  error: string | null;
  created_at: Date | string;
  finalized_at: Date | string | null;
}

/** A ledger row as the app serves it (poll route / P7-D pre-validation). */
export interface StagedUpload {
  id: string;
  collectionId: string;
  itemId: string | null;
  createdBy: string;
  groupId: string | null;
  filenames: string[];
  status: StagedUploadStatus;
  result: unknown;
  error: string | null;
  createdAt: string;
  /** created_at + STAGING_TTL_SECONDS — THE §4.1 governing clock. */
  expiresAt: string;
  finalizedAt: string | null;
}

/** created_at + STAGING_TTL_SECONDS, as an ISO instant. */
export function stagedUploadExpiresAt(createdAt: Date | string): string {
  const created =
    createdAt instanceof Date ? createdAt : new Date(createdAt);
  return new Date(
    created.getTime() + getStagingTtlSeconds() * 1000,
  ).toISOString();
}

function toStagedUpload(row: StagedUploadDbRow): StagedUpload {
  return {
    id: row.id,
    collectionId: row.collection_id,
    itemId: row.item_id,
    createdBy: row.created_by,
    groupId: row.group_id,
    filenames: row.filenames,
    status: row.status,
    result: row.result ?? null,
    error: row.error,
    createdAt: iso(row.created_at),
    expiresAt: stagedUploadExpiresAt(row.created_at),
    finalizedAt: iso(row.finalized_at),
  };
}

export interface CreateStagedUploadInput {
  collectionId: string;
  createdBy: string;
  /** The collection's owning group at mint time (null = unowned, ADR 0003). */
  groupId: string | null;
  /** Sanitized filenames — exactly the staging keys' final segments. */
  filenames: string[];
}

/**
 * Insert one `pending` binding row (§4.1). The generated uuid is the
 * `upload_id` in every `staging://` href and staging key of the session.
 */
export async function createStagedUpload(
  input: CreateStagedUploadInput,
): Promise<StagedUpload> {
  await runMigrations();
  const id = randomUUID();
  const result = await query<StagedUploadDbRow>(
    `INSERT INTO stac_higher.staged_uploads
       (id, collection_id, created_by, group_id, filenames)
     VALUES ($1, $2, $3, $4, $5::jsonb)
     RETURNING id, collection_id, item_id, created_by, group_id, filenames,
               status, result, error, created_at, finalized_at`,
    [
      id,
      input.collectionId,
      input.createdBy,
      input.groupId,
      JSON.stringify(input.filenames),
    ],
  );
  return toStagedUpload(result.rows[0]);
}

/**
 * Record the §4.3 brokered-PUT snapshot on the session's ledger row — the
 * §6.3 restore point. FIRST WRITE WINS by construction (`prior_item IS
 * NULL` in the predicate): a second brokered PUT in the same session never
 * overwrites an existing snapshot, so a rejection can never "restore" the
 * first PUT's own staged document (review residual R2). Idempotently a
 * no-op once a snapshot exists.
 */
export async function bindPriorItemSnapshot(
  id: string,
  item: unknown,
): Promise<void> {
  await runMigrations();
  await query(
    `UPDATE stac_higher.staged_uploads
        SET prior_item = $2::jsonb
      WHERE id = $1
        AND prior_item IS NULL`,
    [id, JSON.stringify(item)],
  );
}

/** Read one ledger row (poll route). Null for an unknown id. */
export async function getStagedUpload(
  id: string,
): Promise<StagedUpload | null> {
  await runMigrations();
  const result = await query<StagedUploadDbRow>(
    `SELECT id, collection_id, item_id, created_by, group_id, filenames,
            status, result, error, created_at, finalized_at
       FROM stac_higher.staged_uploads
      WHERE id = $1`,
    [id],
  );
  const row = result.rows[0];
  return row ? toStagedUpload(row) : null;
}
