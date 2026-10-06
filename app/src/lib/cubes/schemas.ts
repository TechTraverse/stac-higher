/**
 * Cube sink config (virtual cube spec §3.1, ADR 0022): the strict WRITE gate
 * for stac_higher.cube_sinks.config. Pinned against the pipeline's lenient
 * reader (`pipeline/cubes/config.py`) by
 * tests/contract-fixtures/cube-sink-config.json.
 */
import { z } from "zod";

export const MAX_AGE_LIMIT_SECONDS = 30 * 86_400;
const UNIT_SECONDS = { m: 60, h: 3_600, d: 86_400 } as const;

/** `^\d+[mhd]$` → seconds, else null. */
export function durationSeconds(value: string): number | null {
  const match = /^(\d+)([mhd])$/.exec(value);
  if (!match) return null;
  return Number(match[1]) * UNIT_SECONDS[match[2] as keyof typeof UNIT_SECONDS];
}

const name = z.string().trim().min(1);
const uniqueNames = (min: number, max: number) =>
  z
    .array(name)
    .min(min)
    .max(max)
    .refine((list) => new Set(list).size === list.length, "names must be unique");

const windowSchema = z
  .strictObject({
    max_steps: z.number().int().min(1).max(10_000).optional(),
    max_age: z
      .string()
      .refine((v) => {
        const s = durationSeconds(v);
        return s !== null && s > 0 && s <= MAX_AGE_LIMIT_SECONDS;
      }, "max_age must be a duration like 24h (^\\d+[mhd]$), above zero and at most 30d")
      .optional(),
  })
  .refine(
    (w) => w.max_steps !== undefined || w.max_age !== undefined,
    "window needs max_steps, max_age or both",
  );

export const cubeSinkConfigSchema = z
  .strictObject({
    parser: z.literal("hdf5").default("hdf5"),
    append_dim: name,
    variables: uniqueNames(1, 64),
    loadable_variables: uniqueNames(1, 64),
    asset_key: z.string().regex(/^[a-z0-9_-]{1,32}$/).default("cube"),
    window: windowSchema.optional(),
    on_late: z.literal("skip").default("skip"),
  })
  .refine((c) => c.loadable_variables.includes(c.append_dim), {
    message: "loadable_variables must include append_dim",
    path: ["loadable_variables"],
  });

export type CubeSinkConfig = z.output<typeof cubeSinkConfigSchema>;

/** PUT /api/collections/[id]/cube-sink — create or replace. */
export const cubeSinkPutSchema = z.strictObject({
  source_collection_id: z.string().trim().min(1),
  config: cubeSinkConfigSchema,
  enabled: z.boolean().default(true),
});

/** PATCH /api/collections/[id]/cube-sink — toggle only. */
export const cubeSinkPatchSchema = z.strictObject({ enabled: z.boolean() });

const sorted = (list: readonly string[]) => [...list].sort().join("\u0000");

/** Whether `next` changes what the existing repository's arrays are made
 * of. Window, asset_key and on_late are not layout. */
export function layoutChanged(stored: CubeSinkConfig, next: CubeSinkConfig): boolean {
  return (
    stored.parser !== next.parser ||
    stored.append_dim !== next.append_dim ||
    sorted(stored.variables) !== sorted(next.variables) ||
    sorted(stored.loadable_variables) !== sorted(next.loadable_variables)
  );
}
