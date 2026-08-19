/**
 * Notification channel write schemas (M2-C, M2 spec §4, ADR 0010).
 *
 * The webhook `config` is a CROSS-RUNTIME CONTRACT: the app writes it through
 * `webhookChannelConfigSchema` and the pipeline reads it back with
 * `services/pipeline/src/pipeline/notify/config.py` before dispatching. Field
 * names must not drift — the golden fixture in
 * `tests/contract-fixtures/webhook-channel-config.json` keeps both sides
 * honest (consumed by contract-fixtures.test.ts and
 * test_contract_fixtures.py).
 *
 * The URL is deliberately NOT egress-checked here: a webhook target is
 * operator-declared configuration, and the private/loopback policy belongs to
 * the pipeline's dispatch-time egress guard (`resolve_pinned`), next to the
 * connections egress policy — not to the app's `safeFetch`, which stays
 * narrow (spec §4).
 */
import { z } from "zod";

export const webhookChannelConfigSchema = z
  .object({
    /** Dispatch target. Scheme is validated here (http/https only); the
     * private/loopback egress decision happens pipeline-side at POST time. */
    url: z
      .string()
      .min(1, "url is required")
      .max(2048)
      .url("url must be a valid URL")
      .refine((u) => /^https?:\/\//i.test(u), {
        message: "url must be http(s)",
      }),
    /** Optional shared secret; when set, the pipeline signs each POST body
     * with HMAC-SHA256 in the X-StacHigher-Signature header. Write-only in
     * API responses (redacted to `has_secret`). */
    secret: z.string().min(1).max(256).optional(),
  })
  .strict();

export type WebhookChannelConfig = z.infer<typeof webhookChannelConfigSchema>;

/** in_app needs no config (spec §4) — the alerts row plus per-user read state
 * IS the delivery. An empty object keeps the column shape uniform. */
export const inAppChannelConfigSchema = z.object({}).strict();

export const channelCreateSchema = z.discriminatedUnion("kind", [
  z
    .object({
      kind: z.literal("webhook"),
      group_id: z.string().min(1, "group_id is required"),
      config: webhookChannelConfigSchema,
    })
    .strict(),
  z
    .object({
      kind: z.literal("in_app"),
      group_id: z.string().min(1, "group_id is required"),
      config: inAppChannelConfigSchema.default({}),
    })
    .strict(),
]);

export type ChannelCreate = z.infer<typeof channelCreateSchema>;

/** PUT replaces `config` wholesale (mirroring the connections credential
 * rule) — kind and group are immutable; delete + recreate to change them. */
export const channelUpdateSchema = z
  .object({
    config: z.union([webhookChannelConfigSchema, inAppChannelConfigSchema]),
  })
  .strict();

export type ChannelUpdate = z.infer<typeof channelUpdateSchema>;

export function parseChannelCreate(body: unknown) {
  return channelCreateSchema.safeParse(body);
}

export function parseChannelUpdate(body: unknown) {
  return channelUpdateSchema.safeParse(body);
}
