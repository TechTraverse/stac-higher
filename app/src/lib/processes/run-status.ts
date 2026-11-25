/**
 * K-4 (process-compute spec §5.2): the run status vocabulary, pinned by
 * tests/contract-fixtures/process-run-status.json and migration 031's CHECK.
 * Client-safe (no server imports): the run row reads it, and
 * `storage.ts` re-exports it.
 */
/** `cancelled` is terminal, like `succeeded` and `dead`. */
export type ProcessRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "dead"
  | "cancelled";

/** What the cancel verb accepts (fixture order): `queued` and `failed`
 * (retry pending, K-4 R4) flip to `cancelled` at once; `running` is flagged
 * and the pipeline stops it. */
export const CANCELLABLE_RUN_STATUSES: readonly ProcessRunStatus[] = [
  "queued",
  "failed",
  "running",
];

/** The payload-less `pg_notify` channel the cancel statement fires; the
 * pipeline LISTENs on it (ledger.CANCEL_CHANNEL). */
export const PROCESS_RUN_CANCEL_CHANNEL = "process_run_cancel";

