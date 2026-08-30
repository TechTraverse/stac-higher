/**
 * The push-upload-status cross-runtime contract (Phase 7 spec §10) — the
 * asymmetric write-gate/lenient-reader convention INVERTED relative to the
 * §5.1 configs: here the PIPELINE is the strict writer (finalize recorder +
 * sweeps stamp only these statuses and reason strings) and the APP is the
 * lenient Zod reader on the poll response (`GET /api/uploads/{uploadId}`).
 *
 * Lenient means: `status` stays a closed enum (an unknown status is a
 * contract break, not a tolerable drift), but `result` contents tolerate
 * unknown keys (stripped) and unknown future `reason` strings, so a newer
 * pipeline never 500s an older app's poll route.
 *
 * Golden fixture: tests/contract-fixtures/push-upload-status.json
 * (status-contract style) — change anything here and the fixture (plus the
 * pipeline's writer, P7-E) must move with it.
 */
import { z } from "zod";

/** Ledger lifecycle: pending → finalizing → finalized | rejected | expired. */
export const STAGED_UPLOAD_STATUSES = [
  "pending",
  "finalizing",
  "finalized",
  "rejected",
  "expired",
] as const;

export type StagedUploadStatus = (typeof STAGED_UPLOAD_STATUSES)[number];

export const TERMINAL_STAGED_UPLOAD_STATUSES = [
  "finalized",
  "rejected",
  "expired",
] as const;

/**
 * The closed `result.rejected[].reason` set the pipeline writes (spec §4.2,
 * §6.1, §6.3, §12). The app reads these for display only — the reader schema
 * deliberately accepts strings outside the set (see module doc).
 *
 *   multi_session        item's staged hrefs reference more than one session
 *   session_terminal     session already finalized/rejected/expired
 *   unknown_session      staged href names no ledger row
 *   wrong_collection     session was minted for a different collection
 *   bound_to_other_item  session already bound to a different item_id
 *   gc_pending           open asset_gc mark covers the canonical prefix
 *   collection_archived  collection archived between mint and finalize
 *   missing_bytes        referenced staged object absent (never PUT / swept)
 *   checksum_mismatch    staging→canonical copy verification failed
 *   invalid_item         stac-pydantic validation failed post-rewrite
 */
export const PUSH_REJECTION_REASONS = [
  "multi_session",
  "session_terminal",
  "unknown_session",
  "wrong_collection",
  "bound_to_other_item",
  "gc_pending",
  "collection_archived",
  "missing_bytes",
  "checksum_mismatch",
  "invalid_item",
] as const;

export type PushRejectionReason = (typeof PUSH_REJECTION_REASONS)[number];

export const stagedUploadStatusSchema = z.enum(STAGED_UPLOAD_STATUSES);

/** One rejected entry — reason is required but open-ended for the reader. */
const rejectedEntrySchema = z.object({
  reason: z.string().min(1),
  item_ref: z.unknown().optional(),
});

/**
 * The `staged_uploads.result` jsonb — a FinalizeResult subset plus checksums
 * (spec §11). Plain `z.object` strips unknown keys: lenient by construction.
 */
export const finalizeResultSchema = z.object({
  upserted: z
    .array(z.object({ collection_id: z.string(), item_id: z.string() }))
    .optional(),
  rejected: z.array(rejectedEntrySchema).optional(),
  restored: z.boolean().optional(),
  /** filename → digest (`sha256:…`), recorded by the checksum step. */
  checksums: z.record(z.string(), z.string()).optional(),
});

/** The pipeline-written verdict columns as the poll route serves them. */
export const pushUploadStatusSchema = z.object({
  status: stagedUploadStatusSchema,
  result: finalizeResultSchema.nullable().optional(),
  error: z.string().nullable().optional(),
});

export type PushUploadStatus = z.infer<typeof pushUploadStatusSchema>;
