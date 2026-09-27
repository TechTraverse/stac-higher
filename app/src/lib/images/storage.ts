/**
 * `container_images` reads for the deploy gate (C-1). C-3 grows this module
 * with the registry CRUD. The app owns the DDL (migration 030); the
 * pipeline writes statuses, verdicts and scan times.
 */
import { getClient, query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { isImageStale } from "./stale";
import type { ImageScanKind, ImageStatus } from "./status";

export interface ImageGateRow {
  id: string;
  reference: string;
  digest: string | null;
  status: ImageStatus;
  last_scanned_at: Date | null;
  registry_connection_id: string | null;
  /** NULL when the row carries no credential OR its connection is soft-deleted. */
  registry_connection_group_id: string | null;
}

interface GateQueryRow extends Omit<ImageGateRow, "last_scanned_at"> {
  last_scanned_at: Date | string | null;
}

export async function getImageForGate(id: string): Promise<ImageGateRow | null> {
  await runMigrations();
  const result = await query<GateQueryRow>(
    `SELECT i.id, i.reference, i.digest, i.status, i.last_scanned_at,
            i.registry_connection_id, c.group_id AS registry_connection_group_id
       FROM stac_higher.container_images i
       LEFT JOIN stac_higher.connections c
         ON c.id = i.registry_connection_id AND c.deleted_at IS NULL
      WHERE i.id = $1`,
    [id],
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    ...row,
    last_scanned_at: row.last_scanned_at === null ? null : new Date(row.last_scanned_at),
  };
}

// ---------------------------------------------------------------------------
// C-3: the registry surface (container-images spec §9.1). The app INSERTs
// `pending` rows with their `image_scans` admission requests (ADR 0004), and
// applies the two human verdicts (exception, revoke). Everything a scan
// decides (digest, verdict, status transitions, last_scan_*) is the
// pipeline's to write (C-2). The app never sets `last_scan_id`.
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

export interface ApiImageRegistryConnection {
  id: string;
  /** The connection's label. Never its config secrets or credentials. */
  name: string;
  group_id: string;
  /** Soft-deleted: the image can no longer be deployed (`image_group_mismatch`). */
  deleted: boolean;
}

export interface ApiImageException {
  reason: string;
  by: string;
  at: string;
  expires_at: string;
}

export interface ApiImage {
  id: string;
  reference: string;
  tag_at_add: string;
  digest: string | null;
  status: ImageStatus;
  /** The latest policy evaluation as the pipeline stored it (`verdict.ts` reads it). */
  verdict: Json | null;
  size_bytes: number | null;
  config: Json | null;
  last_scan_id: string | null;
  last_scanned_at: string | null;
  /** Computed, never stored (spec §4.3); null when the policy is unreadable. */
  stale: boolean | null;
  /** The Grype DB build date of the latest completed scan (`result.scanner`). */
  db_built_at: string | null;
  added_by: string;
  created_at: string;
  updated_at: string;
  exception: ApiImageException | null;
  tag_current_digest: string | null;
  tag_checked_at: string | null;
  /** The tag now points at another digest than the one that runs (§8.2, informational). */
  drifted: boolean;
  registry_connection: ApiImageRegistryConnection | null;
  /** Live processes whose CURRENT revision snapshots this image. */
  in_use_by: number;
}

interface ImageRow {
  id: string;
  reference: string;
  tag_at_add: string;
  digest: string | null;
  status: ImageStatus;
  verdict: Json | null;
  size_bytes: string | number | null;
  config: Json | null;
  last_scan_id: string | null;
  last_scanned_at: Date | string | null;
  added_by: string;
  created_at: Date | string;
  updated_at: Date | string;
  exception_reason: string | null;
  exception_by: string | null;
  exception_at: Date | string | null;
  exception_expires_at: Date | string | null;
  tag_current_digest: string | null;
  tag_checked_at: Date | string | null;
  registry_connection_id: string | null;
  registry_connection_name: string | null;
  registry_connection_group_id: string | null;
  registry_connection_deleted: boolean | null;
  db_built_at: string | null;
  in_use_by: number | string;
}

/**
 * One row per image, everything the dashboard shows. The connection join
 * deliberately INCLUDES soft-deleted connections (the gate's join excludes
 * them): the dashboard must be able to say "credential deleted" instead of
 * presenting the image as public. The FK is ON DELETE RESTRICT and
 * connections are only soft-deleted, so the joined row always exists.
 * The in-use count rides `process_revisions_image_id_idx` (migration 030).
 */
const IMAGE_ROWS_SQL = `
  SELECT i.id, i.reference, i.tag_at_add, i.digest, i.status, i.verdict,
         i.size_bytes, i.config, i.last_scan_id, i.last_scanned_at,
         i.added_by, i.created_at, i.updated_at,
         i.exception_reason, i.exception_by, i.exception_at, i.exception_expires_at,
         i.tag_current_digest, i.tag_checked_at,
         i.registry_connection_id,
         c.name AS registry_connection_name,
         c.group_id AS registry_connection_group_id,
         (c.deleted_at IS NOT NULL) AS registry_connection_deleted,
         s.result->'scanner'->>'db_built_at' AS db_built_at,
         (SELECT count(*)
            FROM stac_higher.process_revisions r
            JOIN stac_higher.processes p
              ON p.current_revision = r.id AND p.deleted_at IS NULL
           WHERE r.runtime->'image'->>'id' = i.id::text)::int AS in_use_by
    FROM stac_higher.container_images i
    LEFT JOIN stac_higher.connections c ON c.id = i.registry_connection_id
    LEFT JOIN stac_higher.image_scans s ON s.id = i.last_scan_id
`;

/** What the reader needs to derive computed fields (staleness). */
export interface ImageView {
  now: Date;
  scanWindowDays: number | null;
}

function toApiImage(row: ImageRow, view: ImageView): ApiImage {
  return {
    id: row.id,
    reference: row.reference,
    tag_at_add: row.tag_at_add,
    digest: row.digest,
    status: row.status,
    verdict: row.verdict,
    size_bytes: row.size_bytes === null ? null : Number(row.size_bytes),
    config: row.config,
    last_scan_id: row.last_scan_id,
    last_scanned_at: iso(row.last_scanned_at),
    stale: isImageStale(row.status, row.last_scanned_at, view.scanWindowDays, view.now),
    db_built_at: row.db_built_at,
    added_by: row.added_by,
    created_at: iso(row.created_at) as string,
    updated_at: iso(row.updated_at) as string,
    exception:
      row.exception_at === null
        ? null
        : {
            reason: row.exception_reason ?? "",
            by: row.exception_by ?? "",
            at: iso(row.exception_at) as string,
            expires_at: iso(row.exception_expires_at) as string,
          },
    tag_current_digest: row.tag_current_digest,
    tag_checked_at: iso(row.tag_checked_at),
    drifted:
      row.tag_current_digest !== null &&
      row.digest !== null &&
      row.tag_current_digest !== row.digest,
    registry_connection:
      row.registry_connection_id === null
        ? null
        : {
            id: row.registry_connection_id,
            name: row.registry_connection_name ?? "",
            group_id: row.registry_connection_group_id ?? "",
            deleted: row.registry_connection_deleted === true,
          },
    in_use_by: Number(row.in_use_by),
  };
}

export interface ImageListFilters {
  status?: ImageStatus;
  q?: string;
  inUse?: boolean;
}

/** The registry is small (one row per scanned digest); a hard cap keeps a
 * runaway list bounded until the dashboard needs paging. */
export const IMAGE_LIST_LIMIT = 500;

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, (c) => `\\${c}`);
}

export async function listImages(filters: ImageListFilters, view: ImageView): Promise<ApiImage[]> {
  await runMigrations();
  const where: string[] = [];
  const params: unknown[] = [];
  if (filters.status) {
    params.push(filters.status);
    where.push(`status = $${params.length}`);
  }
  if (filters.q) {
    params.push(`%${escapeLike(filters.q)}%`);
    where.push(`(reference || ':' || tag_at_add) ILIKE $${params.length} ESCAPE '\\'`);
  }
  if (filters.inUse === true) where.push("in_use_by > 0");
  if (filters.inUse === false) where.push("in_use_by = 0");
  params.push(IMAGE_LIST_LIMIT);
  const result = await query<ImageRow>(
    `WITH rows AS (${IMAGE_ROWS_SQL})
     SELECT * FROM rows
     ${where.length > 0 ? `WHERE ${where.join(" AND ")}` : ""}
     ORDER BY created_at DESC
     LIMIT $${params.length}`,
    params,
  );
  return result.rows.map((row) => toApiImage(row, view));
}

export async function getImage(id: string, view: ImageView): Promise<ApiImage | null> {
  await runMigrations();
  const result = await query<ImageRow>(
    `WITH rows AS (${IMAGE_ROWS_SQL}) SELECT * FROM rows WHERE id = $1`,
    [id],
  );
  return result.rows[0] ? toApiImage(result.rows[0], view) : null;
}

// --- image_scans -----------------------------------------------------------

export interface ApiImageScan {
  id: string;
  image_id: string;
  kind: ImageScanKind;
  status: "pending" | "running" | "done" | "failed";
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** The §6.4 document with `verdict` and `diff` beside it (C-2 writes it), or `{error}`. */
  result: Json | null;
  findings_ref: string | null;
  log_ref: string | null;
}

interface ScanRow {
  id: string;
  image_id: string;
  kind: ImageScanKind;
  status: ApiImageScan["status"];
  requested_by: string;
  requested_at: Date | string;
  started_at: Date | string | null;
  finished_at: Date | string | null;
  result: Json | null;
  findings_ref: string | null;
  log_ref: string | null;
}

const SCAN_COLUMNS = `
  s.id, s.image_id, s.kind, s.status, s.requested_by, s.requested_at,
  s.started_at, s.finished_at, s.result, s.findings_ref, s.log_ref
`;

function toApiScan(row: ScanRow): ApiImageScan {
  return {
    id: row.id,
    image_id: row.image_id,
    kind: row.kind,
    status: row.status,
    requested_by: row.requested_by,
    requested_at: iso(row.requested_at) as string,
    started_at: iso(row.started_at),
    finished_at: iso(row.finished_at),
    result: row.result,
    findings_ref: row.findings_ref,
    log_ref: row.log_ref,
  };
}

export async function listImageScans(imageId: string, limit = 10): Promise<ApiImageScan[]> {
  await runMigrations();
  const result = await query<ScanRow>(
    `SELECT ${SCAN_COLUMNS} FROM stac_higher.image_scans s
      WHERE s.image_id = $1
      ORDER BY s.requested_at DESC
      LIMIT $2`,
    [imageId, Math.min(Math.max(limit, 1), 50)],
  );
  return result.rows.map(toApiScan);
}

/**
 * A scan by the ids the 202 handed out. The drain may DE-DUPLICATE (spec
 * §9.1): it re-points the scan at the existing image with the same digest
 * and deletes the provisional row. So the scan is also reachable through its
 * old image id once that image no longer exists. It stays unreachable
 * through any OTHER live image's id.
 */
export async function getImageScan(imageId: string, scanId: string): Promise<ApiImageScan | null> {
  await runMigrations();
  const result = await query<ScanRow>(
    `SELECT ${SCAN_COLUMNS} FROM stac_higher.image_scans s
      WHERE s.id = $2
        AND (s.image_id = $1
             OR NOT EXISTS (SELECT 1 FROM stac_higher.container_images WHERE id = $1))`,
    [imageId, scanId],
  );
  return result.rows[0] ? toApiScan(result.rows[0]) : null;
}

// --- who uses an image -----------------------------------------------------

export interface ApiImageUser {
  process_id: string;
  name: string;
  group_id: string;
}

export async function listImageUsers(imageId: string): Promise<ApiImageUser[]> {
  await runMigrations();
  const result = await query<ApiImageUser>(
    `SELECT p.id AS process_id, p.name, p.group_id
       FROM stac_higher.processes p
       JOIN stac_higher.process_revisions r ON r.id = p.current_revision
      WHERE p.deleted_at IS NULL
        AND r.runtime->'image'->>'id' = $1
      ORDER BY p.name`,
    [imageId],
  );
  return result.rows;
}

// --- add (admission) -------------------------------------------------------

export interface AddImageInput {
  reference: string;
  tag: string;
  registryConnectionId: string | null;
  addedBy: string;
}

export interface AddedImage {
  image_id: string;
  scan_id: string;
  /** True when an open admission for the same reference/tag/credential was reused. */
  deduplicated: boolean;
}

/** Shared by the lock-free fast path (`findOpenAdmission`) and the
 * in-transaction re-check (`insertImageWithAdmission`): an admission still
 * in flight for the same reference, tag and credential. */
const OPEN_ADMISSION_SQL = `SELECT i.id AS image_id, s.id AS scan_id
       FROM stac_higher.container_images i
       JOIN stac_higher.image_scans s
         ON s.image_id = i.id AND s.kind = 'admission' AND s.status IN ('pending','running')
      WHERE i.reference = $1
        AND i.tag_at_add = $2
        AND i.registry_connection_id IS NOT DISTINCT FROM $3::uuid
        AND i.status IN ('pending','scanning')
      ORDER BY s.requested_at DESC
      LIMIT 1`;

/** An admission still in flight for the same reference, tag and credential.
 * A second "Add" (a double-click, a second operator) reuses it instead of
 * queueing a second provisional row the drain would only merge again. This
 * runs on the pool: it is a fast, lock-free pre-check for the common case
 * (an earlier Add already has an open admission). It cannot by itself
 * prevent two CONCURRENT Adds from both missing and both inserting —
 * `insertImageWithAdmission` closes that race with an in-transaction lock
 * and re-check. */
export async function findOpenAdmission(
  input: Omit<AddImageInput, "addedBy">,
): Promise<AddedImage | null> {
  await runMigrations();
  const result = await query<{ image_id: string; scan_id: string }>(OPEN_ADMISSION_SQL, [
    input.reference,
    input.tag,
    input.registryConnectionId,
  ]);
  const row = result.rows[0];
  return row ? { ...row, deduplicated: true } : null;
}

/** Serializes concurrent `insertImageWithAdmission` calls for the same
 * reference/tag/credential (Review Focus #4: two concurrent Adds must never
 * create two provisional rows). `UNIQUE (reference, digest)` cannot catch
 * this: both digests are NULL before a scan resolves one, and NULLs are
 * distinct. `pg_advisory_xact_lock` is transaction-scoped (auto-releases on
 * COMMIT/ROLLBACK) and free when uncontested. */
const ADMISSION_LOCK_SQL = `SELECT pg_advisory_xact_lock(hashtextextended($1 || E'\n' || $2 || E'\n' || coalesce($3::text,''), 0))`;

/** A `pending` row (digest NULL: the app never touches a registry) and its
 * `admission` scan request, in ONE transaction, so there is never an image
 * nobody will scan. Takes the advisory lock FIRST, then re-checks for an
 * open admission on the SAME client before inserting: a second concurrent
 * caller blocks on the lock until the first commits, then its re-check
 * finds the first's row and returns it deduplicated instead of inserting a
 * second one. */
export async function insertImageWithAdmission(input: AddImageInput): Promise<AddedImage> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    await client.query(ADMISSION_LOCK_SQL, [
      input.reference,
      input.tag,
      input.registryConnectionId,
    ]);
    const existing = await client.query<{ image_id: string; scan_id: string }>(
      OPEN_ADMISSION_SQL,
      [input.reference, input.tag, input.registryConnectionId],
    );
    if (existing.rows[0]) {
      await client.query("COMMIT");
      return { ...existing.rows[0], deduplicated: true };
    }
    const image = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.container_images
         (reference, tag_at_add, registry_connection_id, added_by)
       VALUES ($1, $2, $3, $4)
       RETURNING id`,
      [input.reference, input.tag, input.registryConnectionId, input.addedBy],
    );
    const imageId = image.rows[0].id;
    const scan = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)
       VALUES ($1, 'admission', $2)
       RETURNING id`,
      [imageId, input.addedBy],
    );
    await client.query("COMMIT");
    return { image_id: imageId, scan_id: scan.rows[0].id, deduplicated: false };
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// --- rescan ----------------------------------------------------------------

export type ScanRequestOutcome =
  | { outcome: "requested"; scan_id: string; kind: ImageScanKind }
  | { outcome: "not_found" }
  | { outcome: "revoked" }
  | { outcome: "already_pending"; scan_id: string };

/**
 * "Rescan now" (spec §9.1). A rescan matches the STORED SBOM (spec §6.3), so
 * an image that never scanned successfully (`sbom_ref` NULL: `pending` with
 * a lost request, or `scan_failed`) gets a new ADMISSION instead, which is
 * spec §4.3's "retry by re-requesting". `scan_failed` goes back to
 * `pending` so the dashboard stops saying "failed" while the retry waits.
 * One open scan per image; revoked is terminal.
 */
export async function requestImageScan(
  imageId: string,
  requestedBy: string,
): Promise<ScanRequestOutcome> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    const image = await client.query<{ status: ImageStatus; sbom_ref: string | null }>(
      `SELECT status, sbom_ref FROM stac_higher.container_images WHERE id = $1 FOR UPDATE`,
      [imageId],
    );
    const row = image.rows[0];
    if (!row) {
      await client.query("ROLLBACK");
      return { outcome: "not_found" };
    }
    if (row.status === "revoked") {
      await client.query("ROLLBACK");
      return { outcome: "revoked" };
    }
    const open = await client.query<{ id: string }>(
      `SELECT id FROM stac_higher.image_scans
        WHERE image_id = $1 AND status IN ('pending','running')
        LIMIT 1`,
      [imageId],
    );
    if (open.rows[0]) {
      await client.query("ROLLBACK");
      return { outcome: "already_pending", scan_id: open.rows[0].id };
    }
    const kind: ImageScanKind = row.sbom_ref === null ? "admission" : "rescan";
    if (kind === "admission") {
      await client.query(
        `UPDATE stac_higher.container_images
            SET status = 'pending', updated_at = now()
          WHERE id = $1 AND status = 'scan_failed'`,
        [imageId],
      );
    }
    const scan = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)
       VALUES ($1, $2, $3)
       RETURNING id`,
      [imageId, kind, requestedBy],
    );
    await client.query("COMMIT");
    return { outcome: "requested", scan_id: scan.rows[0].id, kind };
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// --- exception / revoke (admin verdicts, spec §4.4) ------------------------

export type ExceptionOutcome =
  | { outcome: "granted" }
  | { outcome: "not_found" }
  | { outcome: "wrong_status"; status: ImageStatus };

/** `rejected`/`flagged` -> `approved` with the one live exception (spec §4.4).
 * Conditional UPDATE, so a concurrent drain transition cannot be overwritten
 * by a stale read. */
export async function grantImageException(input: {
  imageId: string;
  reason: string;
  by: string;
  expiresAt: Date;
}): Promise<ExceptionOutcome> {
  await runMigrations();
  const updated = await query<{ id: string }>(
    `UPDATE stac_higher.container_images
        SET status = 'approved',
            exception_reason = $2,
            exception_by = $3,
            exception_at = now(),
            exception_expires_at = $4,
            updated_at = now()
      WHERE id = $1 AND status IN ('rejected','flagged')
      RETURNING id`,
    [input.imageId, input.reason, input.by, input.expiresAt.toISOString()],
  );
  if (updated.rows[0]) return { outcome: "granted" };
  const current = await query<{ status: ImageStatus }>(
    `SELECT status FROM stac_higher.container_images WHERE id = $1`,
    [input.imageId],
  );
  return current.rows[0]
    ? { outcome: "wrong_status", status: current.rows[0].status }
    : { outcome: "not_found" };
}

export type RevokeOutcome =
  | { outcome: "revoked" }
  | { outcome: "not_found" }
  | { outcome: "already_revoked" };

/** Any status -> `revoked` (terminal, spec §4.3). Revoking also ends a live
 * exception (spec §4.4: "revoking an exception early = image.revoke"); the
 * grant stays in the audit log. */
export async function revokeImage(imageId: string): Promise<RevokeOutcome> {
  await runMigrations();
  const updated = await query<{ id: string }>(
    `UPDATE stac_higher.container_images
        SET status = 'revoked',
            exception_reason = NULL,
            exception_by = NULL,
            exception_at = NULL,
            exception_expires_at = NULL,
            updated_at = now()
      WHERE id = $1 AND status <> 'revoked'
      RETURNING id`,
    [imageId],
  );
  if (updated.rows[0]) return { outcome: "revoked" };
  const current = await query<{ status: ImageStatus }>(
    `SELECT status FROM stac_higher.container_images WHERE id = $1`,
    [imageId],
  );
  return current.rows[0] ? { outcome: "already_revoked" } : { outcome: "not_found" };
}

// --- the cluster admission probe (spec §12, wired in K-6) -------------------

/** Could a run on this digest LAUNCH (spec §4.3: approved or flagged, and
 * scanned inside the window)? The same boundary as the gate: exactly the
 * window ago is fresh. A row approved via an exception that has since
 * expired does not count (controller ruling, C-3): the exception grant is
 * time-boxed, and an expired one must fall back to "not approved" here just
 * as it does everywhere else the policy is enforced. */
export async function isDigestLaunchable(digest: string, scanWindowDays: number): Promise<boolean> {
  await runMigrations();
  const result = await query<{ ok: boolean }>(
    `SELECT EXISTS (
       SELECT 1 FROM stac_higher.container_images
        WHERE digest = $1
          AND status IN ('approved','flagged')
          AND last_scanned_at >= now() - make_interval(days => $2::int)
          AND (exception_expires_at IS NULL OR exception_expires_at > now())
     ) AS ok`,
    [digest, scanWindowDays],
  );
  return result.rows[0]?.ok === true;
}
