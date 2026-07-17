/**
 * Zod schemas for collection↔connection associations (ROADMAP §5.1, Phase 4).
 *
 * An association wires a connection to a built-in-catalog collection in a
 * direction. This slice handles `ingest` only (delivery config is Phase 5);
 * the DB table admits both directions so Phase 5 reuses it.
 *
 * The ingest `config` shape is the cross-runtime contract — the Python pipeline
 * parses the same JSON out of `collection_connections.config`, so the field
 * names/shapes here must not drift from §5.1. Optional knobs carry defaults so
 * a minimal UI form produces a complete, pipeline-ready config.
 */
import { z } from "zod";

/** Directions the DB admits; only `ingest` is writable in this slice. */
export const ASSOCIATION_DIRECTIONS = ["ingest", "deliver"] as const;
export type AssociationDirection = (typeof ASSOCIATION_DIRECTIONS)[number];

export const STORAGE_MODES = ["copy", "reference"] as const;
export type StorageMode = (typeof STORAGE_MODES)[number];

export const DELIVERY_RESERVED_MESSAGE =
  "Delivery associations arrive in a later phase; only 'ingest' can be created yet";

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
  .strict();

export type IngestConfig = z.infer<typeof ingestConfigSchema>;

/** Optional flow expectation (§5.1) — drives Phase 6 absence-of-data alerts. */
export const expectationSchema = z
  .object({
    expect_activity_within_seconds: z.number().int().min(1),
  })
  .strict();

export type Expectation = z.infer<typeof expectationSchema>;

// ---------------------------------------------------------------------------
// create / update payloads (collection_id comes from the route path)
// ---------------------------------------------------------------------------

export const associationCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("ingest"),
    enabled: z.boolean().default(true),
    config: ingestConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();

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

/** Build a ZodError with one custom issue (guaranteed-failing parse). */
function customZodError(
  message: string,
  path: (string | number)[],
): z.ZodError {
  const failing = z.unknown().superRefine((_value, ctx) => {
    ctx.addIssue({ code: "custom", message, path });
  });
  const result = failing.safeParse(null);
  return (result as { success: false; error: z.ZodError }).error;
}

/**
 * Parse a create payload, rejecting `direction: 'deliver'` with a dedicated
 * message before the shape check (the `z.literal("ingest")` would otherwise
 * report an opaque "invalid literal" error).
 */
export function parseAssociationCreate(data: unknown): ParsedCreate {
  if (
    typeof data === "object" &&
    data !== null &&
    (data as Record<string, unknown>).direction === "deliver"
  ) {
    return {
      success: false,
      error: customZodError(DELIVERY_RESERVED_MESSAGE, ["direction"]),
    };
  }
  return associationCreateSchema.safeParse(data);
}

export function parseAssociationUpdate(data: unknown): ParsedUpdate {
  return associationUpdateSchema.safeParse(data);
}
