/**
 * `image_scans.result.diff` as the UI reads it (C-4, container-images spec
 * §8.2): what a rescan changed since the image's previous scan. The pipeline
 * writes it (`pipeline/images/diff.py`); this reader is lenient like the
 * other image readers, so unknown keys are stripped. Null for an admission
 * or a first rescan. Client-safe (zod only). Pinned by
 * `tests/contract-fixtures/image-scan-diff.json`.
 */
import { z } from "zod";

const ids = z.array(z.string().min(1));
const delta = z.number().int();

export const imageScanDiffSchema = z.object({
  previous_scan_id: z.string().min(1).nullable(),
  new: ids,
  resolved: ids,
  newly_fixed: ids,
  new_kev: ids,
  verdict_changed: z.boolean(),
  counts_delta: z.object({
    critical: delta,
    high: delta,
    medium: delta,
    low: delta,
    negligible: delta,
    unknown: delta,
  }),
});

export type ImageScanDiff = z.infer<typeof imageScanDiffSchema>;

/** The diff stored beside a scan's result, or null when there is none or it
 * does not read as the §8.2 shape. */
export function readScanDiff(result: unknown): ImageScanDiff | null {
  if (result === null || typeof result !== "object") return null;
  const parsed = imageScanDiffSchema.safeParse((result as Record<string, unknown>).diff);
  return parsed.success ? parsed.data : null;
}
