/**
 * Process persistence (ROADMAP §5 `PROCESS_*`, Phase 9 design spec §3).
 *
 * Ownership split, per ADR 0001 and mirroring
 * `collection_connections`/`ingest_files`: the APP writes processes,
 * revisions, sources and outputs, and INSERTs `process_checks` requests; the
 * PIPELINE writes run state, `process_sources.flow_stats`, and the check
 * result columns. Nothing here writes DDL.
 *
 * Invariants this module holds:
 *   - **Revisions are immutable.** There is no update path — a deploy INSERTs
 *     a snapshot and repoints `processes.current_revision`, in one
 *     transaction, so a process never points at a revision that does not
 *     exist and a run always pins something real.
 *   - **`flow_stats` is never written here.** It is pipeline telemetry the UI
 *     only reads, exactly like the association rollup.
 *   - **`updated_at` is app-maintained**, deliberately not a DB trigger: the
 *     pipeline touches these rows and must not bump "when a user last edited
 *     this" (the connections precedent).
 *   - Soft-delete per ADR 0009 lives on `processes`; every read filters it,
 *     and children are reached only through their parent.
 */
import { getClient, query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import type { ProcessKind } from "./schemas";

const PROCESS_COLUMNS = `
  id, name, description, group_id, kind, current_revision, enabled,
  max_runs_per_hour, created_by, created_at, updated_at
`;

const REVISION_COLUMNS = `
  id, process_id, runtime, code, env, created_by, created_at
`;

const SOURCE_COLUMNS = `
  id, process_id, collection_id, trigger, expectation, flow_stats,
  enabled, created_at, updated_at
`;

const OUTPUT_COLUMNS = `id, process_id, collection_id, created_at`;

const CHECK_COLUMNS = `
  id, process_id, revision_id, requested_by, requested_at, status,
  run_id, result, finished_at
`;

type Json = Record<string, unknown>;

function iso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

// ---------------------------------------------------------------------------
// processes
// ---------------------------------------------------------------------------

interface ProcessRow {
  id: string;
  name: string;
  description: string;
  group_id: string;
  kind: ProcessKind;
  current_revision: string | null;
  enabled: boolean;
  max_runs_per_hour: number;
  created_by: string;
  created_at: Date | string;
  updated_at: Date | string;
}

export interface ApiProcess {
  id: string;
  name: string;
  description: string;
  group_id: string;
  kind: ProcessKind;
  current_revision: string | null;
  enabled: boolean;
  max_runs_per_hour: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

function toApiProcess(row: ProcessRow): ApiProcess {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    group_id: row.group_id,
    kind: row.kind,
    current_revision: row.current_revision,
    enabled: row.enabled,
    max_runs_per_hour: row.max_runs_per_hour,
    created_by: row.created_by,
    created_at: iso(row.created_at) as string,
    updated_at: iso(row.updated_at) as string,
  };
}

/** `groups: null` = admin (all rows); otherwise rows in the caller's groups
 * (an empty array matches nothing). The connections precedent. */
export async function listProcesses(
  groups: string[] | null,
): Promise<ApiProcess[]> {
  await runMigrations();
  const where =
    groups === null
      ? "WHERE deleted_at IS NULL"
      : "WHERE deleted_at IS NULL AND group_id = ANY($1::text[])";
  const result = await query<ProcessRow>(
    `SELECT ${PROCESS_COLUMNS} FROM stac_higher.processes
       ${where} ORDER BY created_at DESC`,
    groups === null ? [] : [groups],
  );
  return result.rows.map(toApiProcess);
}

export async function getProcess(id: string): Promise<ApiProcess | null> {
  await runMigrations();
  const result = await query<ProcessRow>(
    `SELECT ${PROCESS_COLUMNS} FROM stac_higher.processes
      WHERE id = $1 AND deleted_at IS NULL`,
    [id],
  );
  return result.rows[0] ? toApiProcess(result.rows[0]) : null;
}

export class DuplicateProcessNameError extends Error {
  constructor(name: string, groupId: string) {
    super(`A process named '${name}' already exists in group '${groupId}'`);
    this.name = "DuplicateProcessNameError";
  }
}

/** Postgres unique-violation. The live-name index is partial, so this fires
 * only against another LIVE process — a soft-deleted one never blocks a
 * re-create. */
function isUniqueViolation(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { code?: string }).code === "23505"
  );
}

export interface CreateProcessInput {
  name: string;
  description: string;
  groupId: string;
  kind: ProcessKind;
  enabled: boolean;
  maxRunsPerHour: number;
  createdBy: string;
}

export async function createProcess(
  input: CreateProcessInput,
): Promise<ApiProcess> {
  await runMigrations();
  try {
    const result = await query<ProcessRow>(
      `INSERT INTO stac_higher.processes
         (name, description, group_id, kind, enabled, max_runs_per_hour, created_by)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING ${PROCESS_COLUMNS}`,
      [
        input.name,
        input.description,
        input.groupId,
        input.kind,
        input.enabled,
        input.maxRunsPerHour,
        input.createdBy,
      ],
    );
    return toApiProcess(result.rows[0]);
  } catch (err) {
    if (isUniqueViolation(err)) {
      throw new DuplicateProcessNameError(input.name, input.groupId);
    }
    throw err;
  }
}

export interface UpdateProcessInput {
  name?: string;
  description?: string;
  groupId?: string;
  enabled?: boolean;
  maxRunsPerHour?: number;
}

export async function updateProcess(
  id: string,
  patch: UpdateProcessInput,
): Promise<ApiProcess | null> {
  await runMigrations();
  const sets: string[] = [];
  const params: unknown[] = [];
  const set = (fragment: string, value: unknown) => {
    params.push(value);
    sets.push(fragment.replace("?", `$${params.length}`));
  };

  if (patch.name !== undefined) set("name = ?", patch.name);
  if (patch.description !== undefined) set("description = ?", patch.description);
  if (patch.groupId !== undefined) set("group_id = ?", patch.groupId);
  if (patch.enabled !== undefined) set("enabled = ?", patch.enabled);
  if (patch.maxRunsPerHour !== undefined) {
    set("max_runs_per_hour = ?", patch.maxRunsPerHour);
  }
  if (sets.length === 0) return getProcess(id);
  sets.push("updated_at = now()");

  params.push(id);
  try {
    const result = await query<ProcessRow>(
      `UPDATE stac_higher.processes SET ${sets.join(", ")}
        WHERE id = $${params.length} AND deleted_at IS NULL
        RETURNING ${PROCESS_COLUMNS}`,
      params,
    );
    return result.rows[0] ? toApiProcess(result.rows[0]) : null;
  } catch (err) {
    if (isUniqueViolation(err)) {
      throw new DuplicateProcessNameError(patch.name ?? "", patch.groupId ?? "");
    }
    throw err;
  }
}

/**
 * Soft-delete (ADR 0009). Run history survives — every `process_runs` FK is
 * RESTRICT, and nothing cascades into history — so a deleted process's runs
 * stay readable and its name becomes reusable (the live-only unique index).
 */
export async function softDeleteProcess(id: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `UPDATE stac_higher.processes SET deleted_at = now(), updated_at = now()
      WHERE id = $1 AND deleted_at IS NULL`,
    [id],
  );
  return (result.rowCount ?? 0) > 0;
}

// ---------------------------------------------------------------------------
// process_revisions — immutable snapshots; "deploy" = insert + repoint
// ---------------------------------------------------------------------------

interface RevisionRow {
  id: string;
  process_id: string;
  runtime: Json;
  code: string | null;
  env: unknown;
  created_by: string;
  created_at: Date | string;
}

export interface ApiProcessRevision {
  id: string;
  process_id: string;
  runtime: Json;
  code: string | null;
  /** The §5.6 envelope as stored. `secret_ref` entries are POINTERS — no
   * plaintext secret is in this column, so returning it leaks nothing. */
  env: unknown[];
  created_by: string;
  created_at: string;
}

function toApiRevision(row: RevisionRow): ApiProcessRevision {
  return {
    id: row.id,
    process_id: row.process_id,
    runtime: row.runtime,
    code: row.code,
    env: Array.isArray(row.env) ? row.env : [],
    created_by: row.created_by,
    created_at: iso(row.created_at) as string,
  };
}

export async function listRevisions(
  processId: string,
): Promise<ApiProcessRevision[]> {
  await runMigrations();
  const result = await query<RevisionRow>(
    `SELECT ${REVISION_COLUMNS} FROM stac_higher.process_revisions
      WHERE process_id = $1 ORDER BY created_at DESC`,
    [processId],
  );
  return result.rows.map(toApiRevision);
}

export async function getRevision(
  processId: string,
  revisionId: string,
): Promise<ApiProcessRevision | null> {
  await runMigrations();
  const result = await query<RevisionRow>(
    `SELECT ${REVISION_COLUMNS} FROM stac_higher.process_revisions
      WHERE id = $1 AND process_id = $2`,
    [revisionId, processId],
  );
  return result.rows[0] ? toApiRevision(result.rows[0]) : null;
}

export interface DeployRevisionInput {
  processId: string;
  runtime: Json;
  code: string | null;
  env: unknown[];
  createdBy: string;
}

/**
 * Deploy: INSERT an immutable revision and point the process at it, in ONE
 * transaction. Two statements outside a transaction could leave a process
 * pointing at its old revision while a new one exists — indistinguishable, on
 * read, from a deploy that never happened.
 */
export async function deployRevision(
  input: DeployRevisionInput,
): Promise<ApiProcessRevision | null> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    const inserted = await client.query<RevisionRow>(
      `INSERT INTO stac_higher.process_revisions
         (process_id, runtime, code, env, created_by)
       SELECT $1, $2::jsonb, $3, $4::jsonb, $5
         FROM stac_higher.processes
        WHERE id = $1 AND deleted_at IS NULL
       RETURNING ${REVISION_COLUMNS}`,
      [
        input.processId,
        JSON.stringify(input.runtime),
        input.code,
        JSON.stringify(input.env),
        input.createdBy,
      ],
    );
    // The INSERT ... SELECT above yields no row when the process is missing or
    // soft-deleted, which is the same not-found the caller already handles.
    if (!inserted.rows[0]) {
      await client.query("ROLLBACK");
      return null;
    }
    await client.query(
      `UPDATE stac_higher.processes
          SET current_revision = $1, updated_at = now()
        WHERE id = $2 AND deleted_at IS NULL`,
      [inserted.rows[0].id, input.processId],
    );
    await client.query("COMMIT");
    return toApiRevision(inserted.rows[0]);
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// ---------------------------------------------------------------------------
// process_sources / process_outputs — the trigger and publish edges
// ---------------------------------------------------------------------------

interface SourceRow {
  id: string;
  process_id: string;
  collection_id: string;
  trigger: Json;
  expectation: Json | null;
  flow_stats: Json;
  enabled: boolean;
  created_at: Date | string;
  updated_at: Date | string;
}

export interface ApiProcessSource {
  id: string;
  process_id: string;
  collection_id: string;
  trigger: Json;
  expectation: Json | null;
  /** Pipeline-written telemetry (runs / output items / last_run_at / …). */
  flow_stats: Json;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

function toApiSource(row: SourceRow): ApiProcessSource {
  return {
    id: row.id,
    process_id: row.process_id,
    collection_id: row.collection_id,
    trigger: row.trigger,
    expectation: row.expectation,
    flow_stats: row.flow_stats ?? {},
    enabled: row.enabled,
    created_at: iso(row.created_at) as string,
    updated_at: iso(row.updated_at) as string,
  };
}

export class DuplicateSourceError extends Error {
  constructor(collectionId: string) {
    super(`This process already has a source on collection '${collectionId}'`);
    this.name = "DuplicateSourceError";
  }
}

export class DuplicateOutputError extends Error {
  constructor(collectionId: string) {
    super(`This process already outputs to collection '${collectionId}'`);
    this.name = "DuplicateOutputError";
  }
}

export async function listSources(
  processId: string,
): Promise<ApiProcessSource[]> {
  await runMigrations();
  const result = await query<SourceRow>(
    `SELECT ${SOURCE_COLUMNS} FROM stac_higher.process_sources
      WHERE process_id = $1 ORDER BY created_at`,
    [processId],
  );
  return result.rows.map(toApiSource);
}

export async function getSource(
  processId: string,
  sourceId: string,
): Promise<ApiProcessSource | null> {
  await runMigrations();
  const result = await query<SourceRow>(
    `SELECT ${SOURCE_COLUMNS} FROM stac_higher.process_sources
      WHERE id = $1 AND process_id = $2`,
    [sourceId, processId],
  );
  return result.rows[0] ? toApiSource(result.rows[0]) : null;
}

export interface CreateSourceInput {
  processId: string;
  collectionId: string;
  trigger: Json;
  expectation: Json | null;
  enabled: boolean;
}

export async function createSource(
  input: CreateSourceInput,
): Promise<ApiProcessSource> {
  await runMigrations();
  try {
    const result = await query<SourceRow>(
      `INSERT INTO stac_higher.process_sources
         (process_id, collection_id, trigger, expectation, enabled)
       VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
       RETURNING ${SOURCE_COLUMNS}`,
      [
        input.processId,
        input.collectionId,
        JSON.stringify(input.trigger),
        input.expectation === null ? null : JSON.stringify(input.expectation),
        input.enabled,
      ],
    );
    return toApiSource(result.rows[0]);
  } catch (err) {
    if (isUniqueViolation(err)) {
      throw new DuplicateSourceError(input.collectionId);
    }
    throw err;
  }
}

export interface UpdateSourceInput {
  trigger?: Json;
  expectation?: Json | null;
  enabled?: boolean;
}

export async function updateSource(
  processId: string,
  sourceId: string,
  patch: UpdateSourceInput,
): Promise<ApiProcessSource | null> {
  await runMigrations();
  const sets: string[] = [];
  const params: unknown[] = [];
  const set = (fragment: string, value: unknown) => {
    params.push(value);
    sets.push(fragment.replace("?", `$${params.length}`));
  };

  if (patch.trigger !== undefined) {
    set("trigger = ?::jsonb", JSON.stringify(patch.trigger));
  }
  if (patch.expectation !== undefined) {
    set(
      "expectation = ?::jsonb",
      patch.expectation === null ? null : JSON.stringify(patch.expectation),
    );
  }
  if (patch.enabled !== undefined) set("enabled = ?", patch.enabled);
  if (sets.length === 0) return getSource(processId, sourceId);
  sets.push("updated_at = now()");

  params.push(sourceId, processId);
  const result = await query<SourceRow>(
    `UPDATE stac_higher.process_sources SET ${sets.join(", ")}
      WHERE id = $${params.length - 1} AND process_id = $${params.length}
      RETURNING ${SOURCE_COLUMNS}`,
    params,
  );
  return result.rows[0] ? toApiSource(result.rows[0]) : null;
}

export async function deleteSource(
  processId: string,
  sourceId: string,
): Promise<boolean> {
  await runMigrations();
  // Hard delete: a source is configuration, not history. Runs keep their own
  // pinned revision and their source_id goes NULL (ON DELETE SET NULL), so
  // deleting a trigger never removes the record that it ran.
  const result = await query(
    `DELETE FROM stac_higher.process_sources
      WHERE id = $1 AND process_id = $2`,
    [sourceId, processId],
  );
  return (result.rowCount ?? 0) > 0;
}

interface OutputRow {
  id: string;
  process_id: string;
  collection_id: string;
  created_at: Date | string;
}

export interface ApiProcessOutput {
  id: string;
  process_id: string;
  collection_id: string;
  created_at: string;
}

function toApiOutput(row: OutputRow): ApiProcessOutput {
  return {
    id: row.id,
    process_id: row.process_id,
    collection_id: row.collection_id,
    created_at: iso(row.created_at) as string,
  };
}

export async function listOutputs(
  processId: string,
): Promise<ApiProcessOutput[]> {
  await runMigrations();
  const result = await query<OutputRow>(
    `SELECT ${OUTPUT_COLUMNS} FROM stac_higher.process_outputs
      WHERE process_id = $1 ORDER BY created_at`,
    [processId],
  );
  return result.rows.map(toApiOutput);
}

export async function createOutput(
  processId: string,
  collectionId: string,
): Promise<ApiProcessOutput> {
  await runMigrations();
  try {
    const result = await query<OutputRow>(
      `INSERT INTO stac_higher.process_outputs (process_id, collection_id)
       VALUES ($1, $2) RETURNING ${OUTPUT_COLUMNS}`,
      [processId, collectionId],
    );
    return toApiOutput(result.rows[0]);
  } catch (err) {
    if (isUniqueViolation(err)) throw new DuplicateOutputError(collectionId);
    throw err;
  }
}

export async function deleteOutput(
  processId: string,
  outputId: string,
): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.process_outputs
      WHERE id = $1 AND process_id = $2`,
    [outputId, processId],
  );
  return (result.rowCount ?? 0) > 0;
}

// ---------------------------------------------------------------------------
// process_runs — READ + the re-run verb (M5-C)
//
// The pipeline owns run STATE: it claims, executes, and writes the outcome.
// The app reads the ledger for the UI and has exactly one write — the audited
// re-run, which flips a dead row back into the retry path (the `redeliver`
// analog). It deliberately does NOT execute anything itself.
// ---------------------------------------------------------------------------

const RUN_COLUMNS = `
  id, process_id, revision_id, source_id, status, attempts,
  input_items, output_items, log_ref, error, rate_deferred_until,
  is_test, created_at, started_at, finished_at
`;

interface RunRow {
  id: string;
  process_id: string;
  revision_id: string;
  source_id: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "dead";
  attempts: number;
  input_items: unknown;
  output_items: unknown;
  log_ref: string | null;
  error: string | null;
  rate_deferred_until: Date | string | null;
  is_test: boolean;
  created_at: Date | string;
  started_at: Date | string | null;
  finished_at: Date | string | null;
}

export interface ApiProcessRun {
  id: string;
  process_id: string;
  revision_id: string;
  source_id: string | null;
  status: RunRow["status"];
  attempts: number;
  /** Trigger items — one run = N items (§6.7). */
  input_items: unknown[];
  /** What finalize published (M5-D writes this; empty until then). */
  output_items: unknown[];
  log_ref: string | null;
  error: string | null;
  /** Set while the §7 ceiling is holding this run back. */
  rate_deferred_until: string | null;
  is_test: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

function toApiRun(row: RunRow): ApiProcessRun {
  return {
    id: row.id,
    process_id: row.process_id,
    revision_id: row.revision_id,
    source_id: row.source_id,
    status: row.status,
    attempts: row.attempts,
    input_items: Array.isArray(row.input_items) ? row.input_items : [],
    output_items: Array.isArray(row.output_items) ? row.output_items : [],
    log_ref: row.log_ref,
    error: row.error,
    rate_deferred_until: iso(row.rate_deferred_until),
    is_test: row.is_test,
    created_at: iso(row.created_at) as string,
    started_at: iso(row.started_at),
    finished_at: iso(row.finished_at),
  };
}

export async function listRuns(
  processId: string,
  limit = 50,
): Promise<ApiProcessRun[]> {
  await runMigrations();
  const result = await query<RunRow>(
    `SELECT ${RUN_COLUMNS} FROM stac_higher.process_runs
      WHERE process_id = $1
      ORDER BY created_at DESC
      LIMIT $2`,
    [processId, Math.min(Math.max(limit, 1), 200)],
  );
  return result.rows.map(toApiRun);
}

export async function getRun(
  processId: string,
  runId: string,
): Promise<ApiProcessRun | null> {
  await runMigrations();
  const result = await query<RunRow>(
    `SELECT ${RUN_COLUMNS} FROM stac_higher.process_runs
      WHERE id = $1 AND process_id = $2`,
    [runId, processId],
  );
  return result.rows[0] ? toApiRun(result.rows[0]) : null;
}

/**
 * Re-run a DEAD run (the `redeliver` analog, spec §6).
 *
 * Resets the row to `queued` with a fresh attempt budget so the pipeline's
 * next run tick claims it. Two deliberate constraints:
 *
 * - Only `dead` rows. A queued/running row is already owed work, and
 *   re-running a succeeded one would re-publish its outputs.
 * - The pinned `revision_id` is NOT touched. A re-run re-executes the
 *   revision that failed, not whatever is current — otherwise "re-run" would
 *   silently mean "run something else", and the ledger would misattribute the
 *   result.
 */
export async function rerunRun(
  processId: string,
  runId: string,
): Promise<ApiProcessRun | null> {
  await runMigrations();
  const result = await query<RunRow>(
    `UPDATE stac_higher.process_runs
        SET status = 'queued', attempts = 0, error = NULL,
            started_at = NULL, finished_at = NULL,
            rate_deferred_until = NULL
      WHERE id = $1 AND process_id = $2 AND status = 'dead'
      RETURNING ${RUN_COLUMNS}`,
    [runId, processId],
  );
  return result.rows[0] ? toApiRun(result.rows[0]) : null;
}

// ---------------------------------------------------------------------------
// process_checks — the app half of the ADR 0004 bridge (UI test runs)
// ---------------------------------------------------------------------------

interface CheckRow {
  id: string;
  process_id: string;
  revision_id: string;
  requested_by: string;
  requested_at: Date | string;
  status: "pending" | "running" | "done" | "failed";
  run_id: string | null;
  result: Json | null;
  finished_at: Date | string | null;
}

export interface ApiProcessCheck {
  id: string;
  process_id: string;
  revision_id: string;
  requested_by: string;
  requested_at: string;
  status: "pending" | "running" | "done" | "failed";
  /** The run the pipeline turned this request into, once it claims it. */
  run_id: string | null;
  result: Json | null;
  finished_at: string | null;
}

function toApiCheck(row: CheckRow): ApiProcessCheck {
  return {
    id: row.id,
    process_id: row.process_id,
    revision_id: row.revision_id,
    requested_by: row.requested_by,
    requested_at: iso(row.requested_at) as string,
    status: row.status,
    run_id: row.run_id,
    result: row.result,
    finished_at: iso(row.finished_at),
  };
}

/**
 * Request a test run. The app only INSERTs `pending` and polls; the pipeline
 * claims it (FOR UPDATE SKIP LOCKED), turns it into a flagged run, and writes
 * the result columns back — the `connection_checks` contract, reused.
 */
export async function insertProcessCheck(
  processId: string,
  revisionId: string,
  requestedBy: string,
): Promise<ApiProcessCheck> {
  await runMigrations();
  const result = await query<CheckRow>(
    `INSERT INTO stac_higher.process_checks
       (process_id, revision_id, requested_by)
     VALUES ($1, $2, $3)
     RETURNING ${CHECK_COLUMNS}`,
    [processId, revisionId, requestedBy],
  );
  return toApiCheck(result.rows[0]);
}

export async function getProcessCheck(
  processId: string,
  checkId: string,
): Promise<ApiProcessCheck | null> {
  await runMigrations();
  const result = await query<CheckRow>(
    `SELECT ${CHECK_COLUMNS} FROM stac_higher.process_checks
      WHERE id = $1 AND process_id = $2`,
    [checkId, processId],
  );
  return result.rows[0] ? toApiCheck(result.rows[0]) : null;
}
