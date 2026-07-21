/**
 * Zod schemas for collection↔connection associations (ROADMAP §5.1, Phase 4/5).
 *
 * An association wires a connection to a built-in-catalog collection in a
 * direction — `ingest` (Phase 4) or `deliver` (Phase 5). `associationCreateSchema`
 * is a discriminated union on `direction` so both are creatable; the update
 * payload's `config` stays ingest-only for now (delivery-config UPDATE is a
 * later slice).
 *
 * Both `config` shapes are cross-runtime contracts — the Python pipeline
 * parses the same JSON out of `collection_connections.config`, so the field
 * names/shapes here must not drift from §5.1. Optional knobs carry defaults so
 * a minimal UI form produces a complete, pipeline-ready config.
 */
import { z } from "zod";

/** Directions the DB admits. */
export const ASSOCIATION_DIRECTIONS = ["ingest", "deliver"] as const;
export type AssociationDirection = (typeof ASSOCIATION_DIRECTIONS)[number];

export const STORAGE_MODES = ["copy", "reference"] as const;
export type StorageMode = (typeof STORAGE_MODES)[number];

// ---------------------------------------------------------------------------
// ingest config (stored as-is in collection_connections.config jsonb, §5.1)
// ---------------------------------------------------------------------------

const globList = z.array(z.string().min(1)).default([]);

export const groupingSchema = z
  .object({
    // `none` = one file per product (the common raster case); `shared_basename`
    // groups files that share a basename (e.g. .tif + .xml sidecar).
    rule: z.enum(["none", "shared_basename"]).default("none"),
    timeout_seconds: z.number().int().min(0).default(900),
    on_timeout: z.enum(["ingest_partial", "discard"]).default("ingest_partial"),
  })
  .strict();

export const metadataSchema = z
  .object({
    strategy: z
      .enum(["raster_auto", "sidecar", "defaults_only"])
      .default("raster_auto"),
    sidecar: z
      .object({
        pattern: z.string().min(1),
        parser: z.enum(["generic_xml", "json"]).default("generic_xml"),
      })
      .strict()
      .optional(),
    // Collection-level fallbacks applied when extraction leaves a field unset.
    defaults: z
      .object({
        datetime: z.string().min(1).optional(),
        // Opt-in geometry fallback (ISSUE I-27): pgstac requires a non-null
        // item geometry, so a strategy/best-effort GDAL read that still
        // yields nothing can fall back to the collection's overall extent
        // bbox instead of failing the item.
        geometry: z.enum(["collection"]).optional(),
      })
      .strict()
      .default({}),
  })
  .strict();

/** post-ingest action: `leave`, `delete`, or `move:<path>`. */
const postIngestSchema = z
  .string()
  .regex(
    /^(leave|delete|move:.+)$/,
    "post_ingest must be 'leave', 'delete', or 'move:<path>'",
  )
  .default("leave");

export const ingestConfigSchema = z
  .object({
    source_path: z.string().min(1, "source_path is required"),
    include: globList,
    exclude: globList,
    // Procrastinate's periodic scheduler is 1-minute granular; a floor of 60s
    // keeps the poll interval meaningful (the pipeline models it as N ticks).
    poll_frequency_seconds: z.number().int().min(60).default(300),
    storage_mode: z.enum(STORAGE_MODES).default("copy"),
    // Function defaults so an omitted nested object is PARSED through its schema
    // (applying the inner field defaults) — `.default({})` would store a bare
    // `{}` and skip them.
    grouping: groupingSchema.default(() => groupingSchema.parse({})),
    metadata: metadataSchema.default(() => metadataSchema.parse({})),
    post_ingest: postIngestSchema,
  })
  .strict()
  .superRefine((cfg, ctx) => {
    // Reference mode's source bytes ARE the catalog's asset — deleting or moving
    // them would orphan every item that references them. Only `leave` is valid.
    if (cfg.storage_mode === "reference" && cfg.post_ingest !== "leave") {
      ctx.addIssue({
        code: "custom",
        path: ["post_ingest"],
        message:
          "reference mode cannot delete or move the source — its bytes are the " +
          "catalog's asset; use post_ingest 'leave' (or switch to copy mode)",
      });
    }
  });

export type IngestConfig = z.infer<typeof ingestConfigSchema>;

/** Optional flow expectation (§5.1) — drives Phase 6 absence-of-data alerts. */
export const expectationSchema = z
  .object({
    expect_activity_within_seconds: z.number().int().min(1),
  })
  .strict();

export type Expectation = z.infer<typeof expectationSchema>;

// ---------------------------------------------------------------------------
// delivery config (stored as-is in collection_connections.config jsonb, §5.1)
// ---------------------------------------------------------------------------

const payloadSchema = z
  .object({
    item_json: z.boolean().default(false),
    // per-file checksum sidecars: null = none.
    checksums: z.enum(["md5", "sha256"]).nullable().default(null),
    // manifest written LAST — the "product complete" signal for watchers.
    completion_marker: z.boolean().default(false),
  })
  .strict();

const retrySchema = z
  .object({
    max_attempts: z.number().int().min(1).default(5),
    backoff: z.enum(["exponential", "fixed"]).default("exponential"),
  })
  .strict();

export const deliveryConfigSchema = z
  .object({
    // Rendered per asset — see delivery/path.py (Slice B). Tokens: {collection}
    // {item_id} {filename} {yyyy} {mm} {dd}.
    path_template: z.string().min(1, "path_template is required"),
    // optional CQL2 subset — null delivers every item.
    item_filter: z.string().min(1).nullable().default(null),
    // null = all assets; otherwise the asset keys to deliver.
    asset_keys: z.array(z.string().min(1)).nullable().default(null),
    payload: payloadSchema.default(() => payloadSchema.parse({})),
    on_update: z.enum(["redeliver", "ignore"]).default("redeliver"),
    overwrite: z.enum(["never", "always", "if_newer"]).default("if_newer"),
    retry: retrySchema.default(() => retrySchema.parse({})),
    max_concurrent_transfers: z.number().int().min(1).default(4),
  })
  .strict();

export type DeliveryConfig = z.infer<typeof deliveryConfigSchema>;

// ---------------------------------------------------------------------------
// create / update payloads (collection_id comes from the route path)
// ---------------------------------------------------------------------------

const ingestCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("ingest"),
    enabled: z.boolean().default(true),
    config: ingestConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();

const deliveryCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("deliver"),
    enabled: z.boolean().default(true),
    config: deliveryConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();

export const associationCreateSchema = z.discriminatedUnion("direction", [
  ingestCreateSchema,
  deliveryCreateSchema,
]);

export type AssociationCreateInput = z.infer<typeof associationCreateSchema>;

export const associationUpdateSchema = z
  .object({
    enabled: z.boolean().optional(),
    config: ingestConfigSchema.optional(),
    expectation: expectationSchema.nullable().optional(),
  })
  .strict();

export type AssociationUpdateInput = z.infer<typeof associationUpdateSchema>;

export type ParsedCreate =
  | { success: true; data: AssociationCreateInput }
  | { success: false; error: z.ZodError };

export type ParsedUpdate =
  | { success: true; data: AssociationUpdateInput }
  | { success: false; error: z.ZodError };

export function parseAssociationCreate(data: unknown): ParsedCreate {
  return associationCreateSchema.safeParse(data);
}

export function parseAssociationUpdate(data: unknown): ParsedUpdate {
  return associationUpdateSchema.safeParse(data);
}
