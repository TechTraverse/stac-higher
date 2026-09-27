/**
 * `container_images.verdict` as the UI reads it (spec §4.1:
 * `{pass, reasons[], counts, fixed_counts, kev[], max_risk, evaluated_at,
 * policy_version}`). The pipeline writes it (C-2). This reader is lenient on
 * purpose, like the scan-result reader: unknown keys are dropped and missing
 * lists default, so the dashboard survives a newer writer. Client-safe (zod
 * only).
 */
import { z } from "zod";

export const imageVerdictSchema = z.object({
  pass: z.boolean(),
  reasons: z.array(z.string()).default([]),
  counts: z.record(z.string(), z.number()).optional(),
  fixed_counts: z.record(z.string(), z.number()).optional(),
  kev: z.array(z.string()).default([]),
  max_risk: z.number().nullable().optional(),
  evaluated_at: z.string().nullable().optional(),
  policy_version: z.number().nullable().optional(),
});

export type ImageVerdict = z.infer<typeof imageVerdictSchema>;

export function readVerdict(raw: unknown): ImageVerdict | null {
  const parsed = imageVerdictSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}
