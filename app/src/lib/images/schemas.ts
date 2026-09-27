/**
 * Request bodies for the `/api/images` routes (C-3, container-images spec
 * §9.1). `reference` is what the person typed. The route normalizes it
 * (`normalize.ts`) and checks the policy's `allowed_registries` before any
 * row is written.
 */
import { z } from "zod";

export const imageAddSchema = z
  .object({
    reference: z.string().trim().min(1, "reference is required").max(512),
    // A blank or whitespace-only tag is "no tag", not a validation error:
    // `normalizeImageInput` already treats it that way (falls back to
    // `latest`), so the schema folds it to `undefined` here rather than
    // 400ing a body the route would otherwise accept.
    tag: z
      .string()
      .trim()
      .max(128)
      .transform((value) => (value.length > 0 ? value : undefined))
      .optional(),
    registry_connection_id: z.string().uuid().nullable().optional(),
  })
  .strict();
export type ImageAdd = z.infer<typeof imageAddSchema>;

/** Spec §4.4: the reason is free text shown on the dashboard and recorded in
 * the audit row. Ten characters keeps "ok" out without dictating prose. */
export const IMAGE_EXCEPTION_REASON_MIN = 10;

export const imageExceptionSchema = z
  .object({
    reason: z
      .string()
      .trim()
      .min(IMAGE_EXCEPTION_REASON_MIN, `reason must be at least ${IMAGE_EXCEPTION_REASON_MIN} characters`)
      .max(2000),
    expires_at: z.iso.datetime({ offset: true }),
  })
  .strict();
export type ImageException = z.infer<typeof imageExceptionSchema>;
