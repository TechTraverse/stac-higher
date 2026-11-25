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
  max_runs_per_hour, builtin_id, created_by, created_at, updated_at
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
  builtin_id: string | null;
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
  /** X-4: the built-in extractor registry id this process was created from,
   * or null for a hand-written process. A built-in process is read-only in
   * the editor — its revision moves only through "Update to current". */
  builtin_id: string | null;
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
    builtin_id: row.builtin_id ?? null,
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

/** The group's LIVE process for a registry id, or null (X-4 create-or-reuse). */
export async function findBuiltinProcess(
  groupId: string,
  builtinId: string,
): Promise<ApiProcess | null> {
  await runMigrations();
  const result = await query<ProcessRow>(
    `SELECT ${PROCESS_COLUMNS} FROM stac_higher.processes
      WHERE group_id = $1 AND builtin_id = $2 AND deleted_at IS NULL`,
    [groupId, builtinId],
  );
  return result.rows[0] ? toApiProcess(result.rows[0]) : null;
}

export interface CreateBuiltinProcessInput {
  name: string;
  description: string;
  groupId: string;
  builtinId: string;
  maxRunsPerHour: number;
  createdBy: string;
  /** Revision 1, from the registry template (X-queue spec §7). */
  revision: { runtime: Json; code: string; env: unknown[] };
}

/**
 * X-4 create-or-reuse, the CREATE half: insert the process (kind extractor,
 * marked with its registry id) and deploy revision 1 from the template in ONE
 * transaction — a built-in process must never exist without something to run,
 * since the picker stores its id the moment it is created. Returns null when
 * another request created the same (group, registry id) first (the partial
 * unique index), so the caller re-reads and reuses; a NAME collision with a
 * hand-written process surfaces as DuplicateProcessNameError.
 */
export async function createBuiltinProcess(
  input: CreateBuiltinProcessInput,
): Promise<ApiProcess | null> {
  await runMigrations();
  const client = await getClient();
  try {
    await client.query("BEGIN");
    const inserted = await client.query<ProcessRow>(
      `INSERT INTO stac_higher.processes
         (name, description, group_id, kind, enabled, max_runs_per_hour, builtin_id, created_by)
       VALUES ($1, $2, $3, 'extractor', true, $4, $5, $6)
       RETURNING ${PROCESS_COLUMNS}`,
      [
        input.name,
        input.description,
        input.groupId,
        input.maxRunsPerHour,
        input.builtinId,
        input.createdBy,
      ],
    );
    const process = inserted.rows[0];
    const revision = await client.query<{ id: string }>(
      `INSERT INTO stac_higher.process_revisions
         (process_id, runtime, code, env, created_by)
       VALUES ($1, $2::jsonb, $3, $4::jsonb, $5)
       RETURNING id`,
      [
        process.id,
        JSON.stringify(input.revision.runtime),
        input.revision.code,
        JSON.stringify(input.revision.env),
        input.createdBy,
      ],
    );
    const repointed = await client.query<ProcessRow>(
      `UPDATE stac_higher.processes
          SET current_revision = $1, updated_at = now()
        WHERE id = $2
        RETURNING ${PROCESS_COLUMNS}`,
      [revision.rows[0].id, process.id],
    );
    await client.query("COMMIT");
    return toApiProcess(repointed.rows[0]);
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    if (isUniqueViolation(err)) {
      const detail = String((err as { constraint?: string }).constraint ?? "");
      if (detail.includes("builtin")) return null;
      throw new DuplicateProcessNameError(input.name, input.groupId);
    }
    throw err;
  } finally {
    client.release();
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
  is_test, created_at, started_at, finished_at, phase, phase_detail,
  cancel_requested_at
`;

interface RunRow {
  id: string;
  process_id: string;
  revision_id: string;
  source_id: string | null;
  status: ProcessRunStatus;
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
  phase: string | null;
  phase_detail: string | null;
  cancel_requested_at: Date | string | null;
}

// K-4: the run status vocabulary lives in a client-safe module (the run row
// imports it); re-exported here for the server side.
import { PROCESS_RUN_CANCEL_CHANNEL, type ProcessRunStatus } from "./run-status";
export {
  CANCELLABLE_RUN_STATUSES,
  PROCESS_RUN_CANCEL_CHANNEL,
  type ProcessRunStatus,
} from "./run-status";

/** K-3 (process-compute spec §5.2): the sub-state of a queued/running run,
 * pinned by tests/contract-fixtures/process-run-phase.json. */
export type ProcessRunPhase = "pending_capacity" | "starting" | "running";

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
  /** K-3: `pending_capacity` while a queued run waits for its profile's
   * capacity on this host, `starting` once claimed; null on a plain queued
   * row and on terminal rows. */
  phase: ProcessRunPhase | null;
  /** The reason for the phase (the UI's tooltip), when there is one. */
  phase_detail: string | null;
  /** K-4: set once an operator asked to cancel; a `running` run with it set
   * is being stopped (the UI's *Cancelling…*). */
  cancel_requested_at: string | null;
}

export function toApiRun(row: RunRow): ApiProcessRun {
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
    phase: (row.phase as ProcessRunPhase | null) ?? null,
    phase_detail: row.phase_detail ?? null,
    cancel_requested_at: iso(row.cancel_requested_at ?? null),
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

export class RunAlreadyQueuedError extends Error {
  constructor() {
    super(
      "A run for this source is already queued. Re-run this one after it starts.",
    );
    this.name = "RunAlreadyQueuedError";
  }
}

export type CancelOutcome = "cancelled" | "cancel_requested";

/**
 * Cancel a run (K-4, spec §5.3; closes I-81) — ONE statement (plan I11/I12).
 *
 * - `queued` / `failed` (retry pending, R4): nothing is submitted, so the row
 *   flips to `cancelled` at once (`outcome: "cancelled"`).
 * - `running`: the app never touches the executor (ADR 0004). It sets
 *   `cancel_requested_at` and fires a payload-less `pg_notify` on
 *   `process_run_cancel` in the same transaction; the pipeline stops the run,
 *   keeps its log and lands it `cancelled` (`outcome: "cancel_requested"`).
 *
 * `prior_status` comes from the row the CTE locked `FOR UPDATE`, so a claim
 * that commits first is seen as `running` (READ COMMITTED re-checks a locked
 * row against its newest version), and a claim that waits behind the cancel
 * finds a `cancelled` row it cannot claim. Null when the run is unknown,
 * outside this process, or not cancellable.
 *
 * Keep in sync with services/pipeline/tests/test_integration_process_reconcile.py
 * (APP_CANCEL_SQL), which races this statement against a claim.
 */
export async function cancelRun(
  processId: string,
  runId: string,
): Promise<{ run: ApiProcessRun; outcome: CancelOutcome } | null> {
  await runMigrations();
  const columns = RUN_COLUMNS.split(",")
    .map((c) => `upd.${c.trim()}`)
    .join(", ");
  const result = await query<RunRow & { prior_status: ProcessRunStatus }>(
    `WITH target AS (
       SELECT id, status AS prior_status FROM stac_higher.process_runs
        WHERE id = $1 AND process_id = $2 AND status IN ('queued','failed','running')
        FOR UPDATE
     ), upd AS (
       UPDATE stac_higher.process_runs r
          SET cancel_requested_at = coalesce(r.cancel_requested_at, now()),
              status = CASE WHEN t.prior_status = 'running' THEN r.status ELSE 'cancelled' END,
              finished_at = CASE WHEN t.prior_status = 'running' THEN r.finished_at ELSE now() END,
              phase = CASE WHEN t.prior_status = 'running' THEN r.phase ELSE NULL END,
              phase_detail = CASE WHEN t.prior_status = 'running' THEN r.phase_detail ELSE NULL END,
              next_attempt_at = CASE WHEN t.prior_status = 'running'
                                     THEN r.next_attempt_at ELSE NULL END,
              rate_deferred_until = CASE WHEN t.prior_status = 'running'
                                         THEN r.rate_deferred_until ELSE NULL END,
              error = CASE WHEN t.prior_status = 'running' THEN r.error
                           ELSE 'cancelled by an operator' END
         FROM target t WHERE r.id = t.id
       RETURNING r.*, t.prior_status
     )
     SELECT ${columns}, upd.prior_status,
            pg_notify('${PROCESS_RUN_CANCEL_CHANNEL}', '')
       FROM upd`,
    [runId, processId],
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    run: toApiRun(row),
    outcome: row.prior_status === "running" ? "cancel_requested" : "cancelled",
  };
}

/**
 * Re-run a DEAD or CANCELLED run (the `redeliver` analog, spec §6; K-4).
 *
 * Resets the row to `queued` with a fresh attempt budget so the pipeline's
 * next run tick claims it. Two deliberate constraints:
 *
 * - Only `dead` and `cancelled` rows. A queued/running/failed row is
 *   already owed work, and re-running a succeeded one would re-publish its
 *   outputs. Every executor column of the earlier attempt is cleared, so the
 *   re-run is admitted and submitted afresh (K-3/K-4).
 * - The pinned `revision_id` is NOT touched. A re-run re-executes the
 *   revision that failed, not whatever is current — otherwise "re-run" would
 *   silently mean "run something else", and the ledger would misattribute the
 *   result.
 *
 * The queued-slot partial unique indexes (`process_runs_queued_source_idx`,
 * `process_runs_queued_association_idx`) allow one queued run per source, so
 * the UPDATE fails while another run for the same source waits to be claimed.
 * That surfaces as `RunAlreadyQueuedError`, never the raw constraint text.
 */
export async function rerunRun(
  processId: string,
  runId: string,
): Promise<ApiProcessRun | null> {
  await runMigrations();
  try {
    const result = await query<RunRow>(
      `UPDATE stac_higher.process_runs
          SET status = 'queued', attempts = 0, error = NULL,
              started_at = NULL, finished_at = NULL,
              rate_deferred_until = NULL,
              phase = NULL, phase_detail = NULL, next_attempt_at = NULL,
              executor_backend = NULL, executor_handle = NULL, submitted_at = NULL,
              cancel_requested_at = NULL, credentials_expire_at = NULL
        WHERE id = $1 AND process_id = $2 AND status IN ('dead','cancelled')
        RETURNING ${RUN_COLUMNS}`,
      [runId, processId],
    );
    return result.rows[0] ? toApiRun(result.rows[0]) : null;
  } catch (err) {
    if (isUniqueViolation(err)) throw new RunAlreadyQueuedError();
    throw err;
  }
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
