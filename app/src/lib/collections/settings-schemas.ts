/**
 * Write schema for collection settings (M2-E, spec §7). App-internal — the
 * pipeline reads these as typed COLUMNS (M2-F), not a jsonb config, so this
 * is not a cross-runtime contract and needs no golden fixture.
 */
import { z } from "zod";

export const collectionSettingsUpdateSchema = z
  .object({
    /** null = unowned/public (ADR 0003). */
    group_id: z.string().min(1).max(200).nullable(),
    externally_writable: z.boolean(),
    /** null = keep forever; positive days otherwise (matches the 003 CHECK). */
    retention_days: z.number().int().min(1).max(36500).nullable(),
    /** null = no count cap; keep the newest N by item datetime otherwise (W-2,
     * matches the 026 CHECK). Unions with retention_days in the sweep. */
    retention_max_items: z.number().int().min(1).max(1_000_000).nullable(),
    gc_grace_days: z.number().int().min(0).max(36500),
    archived: z.boolean(),
    /** Link-level OGC serving exposure (titiler-pgstac / tipg) — see I-1 caveat. */
    serving_enabled: z.boolean(),
  })
  .strict();

export type CollectionSettingsUpdatePayload = z.infer<
  typeof collectionSettingsUpdateSchema
>;

export function parseCollectionSettingsUpdate(body: unknown) {
  return collectionSettingsUpdateSchema.safeParse(body);
}
