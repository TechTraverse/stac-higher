/**
 * Zod schemas for collection↔connection associations (ROADMAP §5.1, Phase 4/5).
 *
 * An association wires a connection to a built-in-catalog collection in a
 * direction — `ingest` (Phase 4) or `deliver` (Phase 5). `associationCreateSchema`
 * is a discriminated union on `direction`; updates validate `config` against
 * the existing row's direction (`parseAssociationUpdate` takes it as an
 * argument, since the payload itself carries no `direction`).
 *
 * Both `config` shapes are cross-runtime contracts — the Python pipeline
 * parses the same JSON out of `collection_connections.config`, so the field
 * names/shapes here must not drift from §5.1. Optional knobs carry defaults so
 * a minimal UI form produces a complete, pipeline-ready config.
 */
import { z } from "zod";
import { nonBlank, retrySpecSchema } from "@/lib/schema-helpers";

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
      .enum(["raster_auto", "sidecar", "defaults_only", "extractor"])
      .default("raster_auto"),
    sidecar: z
      .object({
        pattern: z.string().min(1),
        parser: z.enum(["generic_xml", "json"]).default("generic_xml"),
      })
      .strict()
      .optional(),
    // GOES spec §6: the operator-authored process that fixes up each draft
    // item. Required with `strategy: extractor`, refused otherwise — the
    // route checks the process exists, is kind=extractor and is owned by
    // the connection's group.
    extractor: z
      .object({ process_id: z.string().uuid() })
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
  .strict()
  .superRefine((m, ctx) => {
    if (m.strategy === "extractor" && !m.extractor) {
      ctx.addIssue({
        code: "custom",
        path: ["extractor"],
        message: "strategy 'extractor' needs extractor.process_id",
      });
    }
    if (m.strategy !== "extractor" && m.extractor) {
      ctx.addIssue({
        code: "custom",
        path: ["extractor"],
        message: "extractor.process_id is only valid with strategy 'extractor'",
      });
    }
  });

/** post-ingest action: `leave`, `delete`, or `move:<path>`. */
const postIngestSchema = z
  .string()
  .regex(
    /^(leave|delete|move:.+)$/,
    "post_ingest must be 'leave', 'delete', or 'move:<path>'",
  )
  .default("leave");

// W-1 `path_template` tokens — mirrors `TOKENS` in
// services/pipeline/src/pipeline/ingest/window.py. Keep in sync.
const PATH_TEMPLATE_TOKENS = new Set(["Y", "m", "d", "j", "H"]);
const TEMPLATE_TOKEN = /\{([^{}]*)\}/g;

export const ingestConfigSchema = z
  .object({
    source_path: nonBlank("source_path is required"),
    include: globList,
    exclude: globList,
    // Procrastinate's periodic scheduler is 1-minute granular; a floor of 60s
    // keeps the poll interval meaningful (the pipeline models it as N ticks).
    poll_frequency_seconds: z.number().int().min(60).default(300),
    // When a discovered file becomes eligible for FETCH (G-3). `auto` decides
    // from the connection's protocol: s3 keys are atomically visible, so they
    // settle on first sight; ftp/sftp uploads are visible mid-write and keep
    // the unchanged-across-two-polls window.
    settle: z.enum(["auto", "two_polls", "immediate"]).default("auto"),
    // W-1: what comes IN. `begin`/`end` are each an RFC3339 timestamp or a
    // `-<n>[smhd]` offset, re-resolved every poll — that is what makes a
    // rolling window roll. The GRAMMAR is checked pipeline-side (one parser,
    // one set of rules); this gate checks the shape.
    window: z
      .object({
        begin: z.string().min(1, "window.begin is required"),
        end: z.string().min(1).nullable().default(null),
      })
      .strict()
      .optional(),
    // Expands the window into the key prefixes worth listing, so the LISTING
    // is bounded and not just its result. Tokens: {Y} {m} {d} {j} {H}.
    path_template: z.string().min(1).optional(),
    // Caps how many NEW files one poll admits, oldest first — a wide window
    // becomes a paced backfill instead of a stampede.
    max_files_per_poll: z.number().int().min(1).optional(),
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
    if (cfg.path_template !== undefined) {
      if (cfg.window === undefined) {
        ctx.addIssue({
          code: "custom",
          path: ["path_template"],
          message:
            "path_template needs a window to expand — set window.begin, or drop " +
            "the template and scope source_path instead",
        });
      }
      // Same guards as the pipeline's validate_template(): at least one
      // known token, no unknown ones — caught at write time, not mid-listing.
      const tokens = [...cfg.path_template.matchAll(TEMPLATE_TOKEN)].map((m) => m[1]);
      const unknown = tokens.filter((t) => !PATH_TEMPLATE_TOKENS.has(t));
      if (unknown.length > 0) {
        ctx.addIssue({
          code: "custom",
          path: ["path_template"],
          message: `path_template has unknown token(s) ${unknown.join(", ")}; known: {Y} {m} {d} {j} {H}`,
        });
      } else if (tokens.length === 0) {
        ctx.addIssue({
          code: "custom",
          path: ["path_template"],
          message: "path_template must contain at least one date token, e.g. {Y}/{j}/{H}/",
        });
      }
    }
  });

export type IngestConfig = z.infer<typeof ingestConfigSchema>;

/**
 * Optional flow expectation (§5.1) — the substrate for M2-B's absence-of-data
 * alerts, evaluated against the pipeline-written `flow_stats` rollup. The
 * shape is direction-specific (an ingest association declares an activity
 * window, a deliver association an NRT SLO) and is a cross-runtime contract
 * with `services/pipeline/src/pipeline/flow/expectation.py` — golden fixtures
 * in `tests/contract-fixtures/{ingest,delivery}-expectation.json`.
 */
export const ingestExpectationSchema = z
  .object({
    expect_activity_within_seconds: z.number().int().min(1),
  })
  .strict();

export const deliveryExpectationSchema = z
  .object({
    deliver_within_seconds: z.number().int().min(1),
  })
  .strict();

export type Expectation =
  | z.infer<typeof ingestExpectationSchema>
  | z.infer<typeof deliveryExpectationSchema>;

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

const retrySchema = retrySpecSchema(5);

export const deliveryConfigSchema = z
  .object({
    // Rendered per asset — see delivery/path.py (Slice B). Tokens: {collection}
    // {item_id} {filename} {yyyy} {mm} {dd}.
    path_template: nonBlank("path_template is required"),
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

// Fields shared by both create variants; `direction`, `config` and
// `expectation` differ per arm.
const baseCreateFields = {
  connection_id: z.string().uuid("connection_id must be a connection UUID"),
  enabled: z.boolean().default(true),
};

const ingestCreateSchema = z
  .object({
    ...baseCreateFields,
    direction: z.literal("ingest"),
    config: ingestConfigSchema,
    expectation: ingestExpectationSchema.nullable().default(null),
  })
  .strict();

const deliveryCreateSchema = z
  .object({
    ...baseCreateFields,
    direction: z.literal("deliver"),
    config: deliveryConfigSchema,
    expectation: deliveryExpectationSchema.nullable().default(null),
  })
  .strict();

export const associationCreateSchema = z.discriminatedUnion("direction", [
  ingestCreateSchema,
  deliveryCreateSchema,
]);

export type AssociationCreateInput = z.infer<typeof associationCreateSchema>;

// The update payload carries no `direction` (immutable on the row), so the
// caller supplies it from the existing association and the config is validated
// against that direction's schema — an ingest-shaped config can never land on
// a `deliver` row (ISSUE I-39: that used to stall the dispatcher).
const baseUpdateFields = {
  enabled: z.boolean().optional(),
};

export const ingestUpdateSchema = z
  .object({
    ...baseUpdateFields,
    config: ingestConfigSchema.optional(),
    expectation: ingestExpectationSchema.nullable().optional(),
  })
  .strict();

export const deliveryUpdateSchema = z
  .object({
    ...baseUpdateFields,
    config: deliveryConfigSchema.optional(),
    expectation: deliveryExpectationSchema.nullable().optional(),
  })
  .strict();

export type AssociationUpdateInput =
  | z.infer<typeof ingestUpdateSchema>
  | z.infer<typeof deliveryUpdateSchema>;

// Pre-parse payload shapes for API clients: `z.input` leaves defaulted fields
// optional, so the browser can send a sparse config and the server-side parse
// fills nested defaults (see `buildConfig` in IngestFormDialog /
// DeliveryFormDialog).
export type AssociationCreatePayload = z.input<typeof associationCreateSchema>;

export type AssociationUpdatePayload =
  | z.input<typeof ingestUpdateSchema>
  | z.input<typeof deliveryUpdateSchema>;

export type ParsedCreate =
  | { success: true; data: AssociationCreateInput }
  | { success: false; error: z.ZodError };

export type ParsedUpdate =
  | { success: true; data: AssociationUpdateInput }
  | { success: false; error: z.ZodError };

export function parseAssociationCreate(data: unknown): ParsedCreate {
  return associationCreateSchema.safeParse(data);
}

export function parseAssociationUpdate(
  data: unknown,
  direction: AssociationDirection,
): ParsedUpdate {
  const schema =
    direction === "deliver" ? deliveryUpdateSchema : ingestUpdateSchema;
  return schema.safeParse(data);
}
