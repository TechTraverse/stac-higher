/**
 * Zod building blocks shared by the cross-runtime contract schemas
 * (`associations/schemas.ts` §5.1, `processes/schemas.ts` §5.6).
 *
 * These are not generic conveniences — each one encodes a rule the Python
 * lenient readers also enforce, so a single definition is what keeps the two
 * runtimes from drifting apart one contract module at a time.
 */
import { z } from "zod";

/**
 * Non-blank string. The pipeline's readers `.strip()` required strings, so a
 * whitespace-only value must not pass the write gate either — the contract
 * fixtures pin this on both sides (`tests/contract-fixtures/`).
 */
export const nonBlank = (message: string) =>
  z
    .string()
    .min(1, message)
    .refine((s) => s.trim().length > 0, message);

/**
 * The queue RetrySpec shape (§5.1), used by delivery associations and process
 * runtimes alike. Only the attempt budget differs between them, so it is the
 * one parameter — everything else drifting would break one of the two
 * fixtures.
 */
export const retrySpecSchema = (maxAttemptsDefault: number) =>
  z
    .object({
      max_attempts: z.number().int().min(1).default(maxAttemptsDefault),
      backoff: z.enum(["exponential", "fixed"]).default("exponential"),
    })
    .strict();
