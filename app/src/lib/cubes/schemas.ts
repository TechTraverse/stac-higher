/**
 * Cube sink config (virtual cube spec §3.1, ADR 0022): the strict WRITE gate
 * for stac_higher.cube_sinks.config. Pinned against the pipeline's lenient
 * reader (`pipeline/cubes/config.py`) by
 * tests/contract-fixtures/cube-sink-config.json.
 */
import { z } from "zod";
import { durationSeconds } from "./duration";
import { CUBE_MAINTENANCE_STATUSES } from "./status";

export { durationSeconds };

export const MAX_AGE_LIMIT_SECONDS = 30 * 86_400;
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

const count = z.number().int();

/**
 * `cube_sinks.last_maintenance`, as the pipeline writes it
 * (`pipeline/cubes/maintain.py`). The LENIENT reader: unknown keys are
 * stripped, and attention reasons, publish outcomes and size kinds stay free
 * strings so a newer writer does not break the card. `status` and every type
 * are strict. A key is ABSENT when the run never reached it (no repository, a
 * failed run, a readout not landed yet); `null` only where the pipeline says
 * so. Pinned by tests/contract-fixtures/cube-maintenance-summary.json.
 */
export const cubeMaintenanceSchema = z.object({
  status: z.enum(CUBE_MAINTENANCE_STATUSES),
  started_at: z.string(),
  finished_at: z.string().optional(),
  attention: z.array(z.string()),
  durations_ms: z.record(z.string(), z.number()),
  error: z.string().optional(),
  ledger_pruned: count.optional(),
  window: z.boolean().optional(),
  repository: z.boolean().optional(),
  healed: z.boolean().optional(),
  trimmed: count.optional(),
  published: z.boolean().optional(),
  publish_outcome: z.string().nullable().optional(),
  publish_error: z.string().optional(),
  expired_snapshots: count.nullable().optional(),
  gc: z
    .object({
      snapshots_deleted: count,
      manifests_deleted: count,
      chunks_deleted: count,
      transaction_logs_deleted: count,
      attributes_deleted: count,
      bytes_deleted: count,
      objects_failed_to_delete: count,
      delete_errors: z.array(z.string()),
    })
    .nullable()
    .optional(),
  sizes: z.record(z.string(), z.object({ objects: count, bytes: count })).optional(),
  total_objects: count.optional(),
  total_bytes: count.optional(),
  warn_bytes: count.optional(),
  measured_at: z.string().optional(),
  measure_error: z.string().optional(),
});

export type CubeMaintenance = z.output<typeof cubeMaintenanceSchema>;

/** An unreadable stored summary is `null` (with a warning), never a throw. */
export function readCubeMaintenance(raw: unknown): CubeMaintenance | null {
  if (raw === null || raw === undefined) return null;
  const parsed = cubeMaintenanceSchema.safeParse(raw);
  if (parsed.success) return parsed.data;
  console.warn("[cubes] unreadable last_maintenance", parsed.error.issues);
  return null;
}
