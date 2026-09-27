/**
 * The scanner's `result.json` as the APP reads it back out of
 * `image_scans.result` (C-1, container-images spec §6.4). This is the
 * LENIENT reader: unknown keys are stripped and a newer `version` is
 * tolerated, so the C-3 dashboard survives a newer scanner. The pipeline's
 * parser (`pipeline/images/scan_result.py`) is the strict side. Pinned by
 * `tests/contract-fixtures/image-scan-result.json`.
 */
import { z } from "zod";
import { imageDigestSchema, imageReferenceSchema } from "./reference";
import { IMAGE_SCAN_KINDS } from "./status";

export const SEVERITIES = ["critical", "high", "medium", "low", "negligible", "unknown"] as const;

const count = z.number().int().min(0);
const severityCountsSchema = z.object({
  critical: count,
  high: count,
  medium: count,
  low: count,
  negligible: count,
  unknown: count,
});

const findingSchema = z.object({
  id: z.string().min(1),
  severity: z.enum(SEVERITIES),
  package: z.string().min(1),
  version: z.string(),
  fixed_in: z.string().nullable(),
  kev: z.boolean(),
  epss: z.number().min(0).max(1).nullable(),
  risk: z.number().min(0),
  published_at: z.string().nullable(),
});

const SUCCESS_KEYS = [
  "digest",
  "platform_digest",
  "platform",
  "size_bytes",
  "config",
  "scanner",
  "sbom_ref",
  "findings_ref",
  "counts",
  "fixed_counts",
  "kev",
  "max_risk",
  "top",
] as const;

export const imageScanResultSchema = z
  .object({
    version: z.number().int().min(1),
    kind: z.enum(IMAGE_SCAN_KINDS),
    reference: imageReferenceSchema,
    tag: z.string().min(1),
    error: z.string().min(1).nullable().default(null),
    digest: imageDigestSchema.optional(),
    platform_digest: imageDigestSchema.optional(),
    platform: z.object({ os: z.string().min(1), architecture: z.string().min(1) }).optional(),
    size_bytes: z.number().int().min(0).optional(),
    config: z
      .object({
        user: z.string(),
        entrypoint: z.array(z.string()).nullable(),
        cmd: z.array(z.string()).nullable(),
      })
      .optional(),
    scanner: z
      .object({ syft: z.string(), grype: z.string(), db_built_at: z.string() })
      .optional(),
    sbom_ref: z.string().min(1).optional(),
    findings_ref: z.string().min(1).optional(),
    counts: severityCountsSchema.optional(),
    fixed_counts: severityCountsSchema.optional(),
    kev: z.array(z.string().min(1)).optional(),
    max_risk: z.number().min(0).optional(),
    top: z.array(findingSchema).optional(),
    tag_drift: z
      .object({ current_digest: imageDigestSchema.nullable(), drifted: z.boolean() })
      .nullable()
      .optional(),
  })
  .superRefine((doc, ctx) => {
    if (doc.error !== null) return; // a failed scan carries only its identity
    for (const key of SUCCESS_KEYS) {
      if (doc[key] === undefined) {
        ctx.addIssue({ code: "custom", path: [key], message: `${key} is required on a successful scan` });
      }
    }
  });

export type ImageScanResult = z.infer<typeof imageScanResultSchema>;
