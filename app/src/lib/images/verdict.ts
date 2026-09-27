/**
 * `container_images.verdict` as the UI reads it (spec §4.1:
 * `{pass, reasons[], counts, fixed_counts, kev[], max_risk, evaluated_at,
 * policy_version}`). The pipeline writes it (C-2). This reader is lenient on
 * purpose, like the scan-result reader: unknown keys are dropped and missing
 * lists default, so the dashboard survives a newer writer. Client-safe (zod
 * only).
 */
import { z } from "zod";
import type { ImageStatus } from "./status";

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

/**
 * Has an approved image's exception lapsed without its latest scan now
 * passing the policy (controller ruling, C-3)? An exception is a time-boxed
 * grant, not a permanent override: once it expires, "still approved" only
 * holds if the last verdict actually passes on its own. A missing or
 * unparseable verdict counts as NOT passing (fails closed). The boundary
 * matches the gate's: expiry exactly `now` counts as expired. Since C-4 the
 * pipeline's hourly tick clears an expired exception (and flags a failing
 * image), so at the deploy gate this rule is defence in depth; the UI no
 * longer uses it to pick the word "expired" (`isExceptionExpired` in
 * components/images/format.ts does).
 */
export function exceptionLapsed(
  i: { status: ImageStatus; exceptionExpiresAt: Date | string | null | undefined; verdict: unknown },
  now: Date,
): boolean {
  if (i.status !== "approved" || i.exceptionExpiresAt == null) return false;
  const t = new Date(i.exceptionExpiresAt).getTime();
  if (Number.isFinite(t) && t > now.getTime()) return false;
  return readVerdict(i.verdict)?.pass !== true;
}
