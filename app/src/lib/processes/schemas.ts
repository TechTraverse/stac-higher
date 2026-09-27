/**
 * Zod schemas for the Phase 9 process shapes (ROADMAP §5.6, M5 design spec
 * §3/§5.6). Each is a cross-runtime contract: the Python pipeline parses the
 * same JSON back out of `process_sources.trigger` / `.expectation` and
 * `process_revisions.runtime` / `.env`, so field names and defaults here must
 * not drift from `services/pipeline/src/pipeline/process/config.py`. Golden
 * fixtures in `tests/contract-fixtures/process-*.json` keep both sides honest
 * — change a field, a default or an enum on either side and one of the two
 * suites fails until the fixture and the other validator follow.
 *
 * The established asymmetry applies: Zod is the STRICT write gate (unknown
 * keys rejected, defaults applied so a minimal UI form produces a complete
 * document) and the Python reader is LENIENT (unknown keys ignored, numbers
 * coerced) so a newer writer never bricks a running pipeline.
 */
import { z } from "zod";
import { nonBlank, retrySpecSchema } from "@/lib/schema-helpers";
import { imageSnapshotSchema } from "@/lib/images/reference";

// ---------------------------------------------------------------------------
// trigger (process_sources.trigger — §5.6)
// ---------------------------------------------------------------------------

export const PROCESS_TRIGGER_KINDS = ["item_event", "cron"] as const;
export type ProcessTriggerKind = (typeof PROCESS_TRIGGER_KINDS)[number];

/**
 * One cron field: `*`, a number, a name (`mon`, `jan`), a range, a list, or
 * any of those with a `/step`. Deliberately structural, not semantic — it
 * rejects prose and six-field (seconds-granular) schedules, which is what the
 * write gate is for; the scheduler is the authority on whether a structurally
 * valid schedule ever fires.
 */
const CRON_FIELD = "(?:\\*|[0-9a-zA-Z]+(?:-[0-9a-zA-Z]+)?)(?:/\\d+)?";
const CRON_LIST = `${CRON_FIELD}(?:,${CRON_FIELD})*`;
const CRON_RE = new RegExp(`^${CRON_LIST}(?: ${CRON_LIST}){4}$`);

/** Procrastinate's periodic scheduler is minute-granular five-field cron. */
export const cronScheduleSchema = z
  .string()
  .transform((s) => s.trim())
  .refine(
    (s) => CRON_RE.test(s),
    "schedule must be a five-field cron expression (minute hour day month weekday)",
  );

const itemEventTriggerSchema = z
  .object({
    kind: z.literal("item_event"),
    // Optional CQL2 subset, evaluated on the same path as a delivery
    // association's item_filter. NOT parsed here (ISSUES I-41) — a filter that
    // does not compile fails loudly at dispatch rather than silently matching
    // everything.
    item_filter: nonBlank("item_filter must be a CQL2 expression")
      .nullable()
      .default(null),
  })
  .strict();

const cronTriggerSchema = z
  .object({
    kind: z.literal("cron"),
    schedule: cronScheduleSchema,
  })
  .strict();

export const processTriggerSchema = z.discriminatedUnion("kind", [
  itemEventTriggerSchema,
  cronTriggerSchema,
]);

export type ProcessTrigger = z.infer<typeof processTriggerSchema>;

// ---------------------------------------------------------------------------
// runtime (process_revisions.runtime — §5.6, ADR 0013)
// ---------------------------------------------------------------------------

export const PROCESS_RUNTIME_KINDS = [
  "inline_python",
  "inline_python_on_image",
  "container",
] as const;
export type ProcessRuntimeKind = (typeof PROCESS_RUNTIME_KINDS)[number];
/** The kinds that run on a scanned USER image (C-1, container-images spec §3). */
export const USER_IMAGE_RUNTIME_KINDS = ["inline_python_on_image", "container"] as const;
/** `command` (kind 3) maps to Docker `Cmd` / Kubernetes `args`, never `Entrypoint` or `User`. */
export const MAX_COMMAND_ENTRIES = 64;

/** `memory_mb` becomes the executor's `HostConfig.Memory`. The floor leaves
 * room for the interpreter itself — a smaller limit OOM-kills every run
 * before user code runs, which is a worse failure than refusing the config. */
const MEMORY_MB_MIN = 128;
/** A run is not a service (ADR 0013): the executor enforces this as a
 * wall-clock kill, so the ceiling bounds how long a wedged run can hold a
 * container slot. */
const TIMEOUT_SECONDS_MAX = 86_400;

/** The §5.1 RetrySpec, with a process's smaller attempt budget. */
export const processRetrySchema = retrySpecSchema(3);

/**
 * Network profile levels (GOES spec §4, ADR 0018), ORDERED lowest first — the
 * `PROCESS_NETWORK_MAX` cap compares positions. Mirrors `NETWORK_LEVELS` in
 * `services/pipeline/src/pipeline/process/config.py`.
 */
export const PROCESS_NETWORK_LEVELS = ["isolated", "inputs", "hosts", "open"] as const;
export type NetworkLevel = (typeof PROCESS_NETWORK_LEVELS)[number];

/** A `hosts` entry is a bare hostname: no scheme, no port, no wildcard. */
const HOSTNAME_RE = /^[A-Za-z0-9.-]+$/;

export const processNetworkSchema = z
  .object({
    level: z.enum(PROCESS_NETWORK_LEVELS).default("isolated"),
    hosts: z
      .array(z.string().regex(HOSTNAME_RE, "hosts entries must be bare hostnames"))
      .default([]),
  })
  .strict()
  .superRefine((network, ctx) => {
    if (network.level === "hosts" && network.hosts.length === 0) {
      ctx.addIssue({
        code: "custom",
        path: ["hosts"],
        message: "network level 'hosts' requires a non-empty hosts list",
      });
    }
    if (network.level !== "hosts" && network.hosts.length > 0) {
      ctx.addIssue({
        code: "custom",
        path: ["hosts"],
        message: "hosts are only allowed with network level 'hosts'",
      });
    }
  });

export type ProcessNetwork = z.infer<typeof processNetworkSchema>;

/**
 * Platform runtime image ALIASES (X-queue spec §8). An alias names one of the
 * platform-built images — never a user image (that is `runtime.image`, the
 * scanned snapshot of kinds 2–3, ADR 0021) — and the pipeline
 * resolves it at launch through `PROCESS_RUNTIME_IMAGE` /
 * `PROCESS_RUNTIME_IMAGE_STACTOOLS`, dying with a reason when the alias is
 * unknown there (the `PROCESS_NETWORK_MAX` dual-enforcement pattern). Mirrors
 * `RUNTIME_IMAGE_ALIASES` in `services/pipeline/src/pipeline/process/config.py`.
 * K-1's hardware profiles carry an `image` BASE; the alias selects the
 * variant (`<base>-stactools`), so the two compose.
 */
export const PROCESS_RUNTIME_IMAGE_ALIASES = ["default", "stactools"] as const;
export type RuntimeImageAlias = (typeof PROCESS_RUNTIME_IMAGE_ALIASES)[number];

/**
 * K-1 (process-compute spec §4): the hardware a run asks for — a profile id
 * this deployment defines plus counts within the profile's bounds. The SHAPE
 * lives here; the bounds and the profile's existence are checked by the deploy
 * route (`hardwareBoundsError`) and again by the pipeline at launch, because
 * the profile set is deployment config, not part of the schema.
 */
export const hardwareSchema = z
  .object({
    profile: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, "hardware.profile must be a profile id"),
    cpu: z.number().positive(),
    gpu_count: z.number().int().min(0).default(0),
  })
  .strict();
export type ProcessHardware = z.infer<typeof hardwareSchema>;

const runtimeLimits = {
  memory_mb: z.number().int().min(MEMORY_MB_MIN).default(512),
  timeout_seconds: z.number().int().min(1).max(TIMEOUT_SECONDS_MAX).default(900),
  // Function default so an omitted object is PARSED through its schema
  // (applying the inner defaults) instead of stored as a bare `{}`.
  retry: processRetrySchema.default(() => processRetrySchema.parse({})),
  network: processNetworkSchema.default(() => processNetworkSchema.parse({})),
  // Every stored revision predates this block: absent means `standard` at its
  // default cpu (1 — the shipped sets pin it), no GPU. The pipeline reader
  // defaults the same way.
  hardware: hardwareSchema.default(() => ({ profile: "standard", cpu: 1, gpu_count: 0 })),
};

const inlinePythonRuntimeSchema = z
  .object({
    kind: z.literal("inline_python"),
    // Present and null-only: inline code runs on a PLATFORM image picked by
    // `runtime_image`; code on your own image is `inline_python_on_image`.
    image: z.null().default(null),
    // Every stored revision predates the alias; absent reads as `default`
    // here and in the Python reader.
    runtime_image: z.enum(PROCESS_RUNTIME_IMAGE_ALIASES).default("default"),
    ...runtimeLimits,
  })
  .strict();

/** Kinds 2–3 name a scanned image by snapshot; an alias beside it would
 * name a second image, so it is null-only (spec §3). */
const userImageFields = {
  image: imageSnapshotSchema,
  runtime_image: z.null().default(null),
};

const inlinePythonOnImageRuntimeSchema = z
  .object({
    kind: z.literal("inline_python_on_image"),
    ...userImageFields,
    ...runtimeLimits,
  })
  .strict();

const commandSchema = z
  .array(z.string().refine((s) => s.trim().length > 0, "command entries must be non-blank"))
  .min(1, "command must be non-empty when present (omit it to use the image's own CMD)")
  .max(MAX_COMMAND_ENTRIES, `command carries at most ${MAX_COMMAND_ENTRIES} entries`);

const containerRuntimeSchema = z
  .object({
    kind: z.literal("container"),
    ...userImageFields,
    // Overrides the image's Cmd, never its Entrypoint or User (spec §14.4).
    command: commandSchema.nullable().default(null),
    ...runtimeLimits,
  })
  .strict();

/**
 * The full runtime shape, all three arms, for READING a stored runtime.
 * `processRuntimeSchema` below is the write gate. The two differ only in the
 * slice-1 network rule. Whether a kind 2/3 snapshot's image may be deployed
 * is NOT a shape question: the revisions route runs the DB-backed
 * `checkImageGate` (C-1, container-images spec §3).
 */
export const processRuntimeReadSchema = z.discriminatedUnion("kind", [
  inlinePythonRuntimeSchema,
  inlinePythonOnImageRuntimeSchema,
  containerRuntimeSchema,
]);

export type ProcessRuntime = z.infer<typeof processRuntimeReadSchema>;

/** The GOES slice-1 refusal for network levels above `isolated`. */
export const NETWORK_LEVEL_NOT_YET_AVAILABLE =
  "network levels above 'isolated' arrive with the egress proxy; see the GOES " +
  "spec §11. Every process runs isolated this slice — inputs are staged into " +
  "the run.";

/**
 * The WRITE gate and the default name. The shape carries every kind. The
 * image check for kinds 2–3 lives in the revisions route (it needs the DB),
 * and the pipeline re-checks at launch. The network asymmetry stays here
 * (GOES spec §4): the reader carries every level, but the write gate stores
 * only `isolated` until the egress proxy exists.
 */
export const processRuntimeSchema = processRuntimeReadSchema.superRefine(
  (runtime, ctx) => {
    if (runtime.network.level !== "isolated") {
      ctx.addIssue({
        code: "custom",
        path: ["network", "level"],
        message: NETWORK_LEVEL_NOT_YET_AVAILABLE,
      });
    }
  },
);

// ---------------------------------------------------------------------------
// env (process_revisions.env — §5.6, extends §5.2)
// ---------------------------------------------------------------------------

/** POSIX environment-variable name. */
const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * A pointer into the §5.2 encrypted-credentials envelope: a named key inside
 * a connection's write-only credentials blob. Resolution happens ONLY in the
 * pipeline at run launch, and only into the run container's environment —
 * never into the platform worker's own process (ADR 0013's isolation
 * invariant), never back through the API, never into audit detail.
 */
export const secretRefSchema = z
  .object({
    connection_id: z.string().uuid("secret_ref.connection_id must be a connection UUID"),
    key: nonBlank("secret_ref.key is required"),
  })
  .strict();

export type SecretRef = z.infer<typeof secretRefSchema>;

const envEntrySchema = z
  .object({
    name: z
      .string()
      .regex(ENV_NAME_RE, "name must be a POSIX environment-variable name"),
    // `value` is for NON-SECRET configuration. Secrets go through secret_ref;
    // that is why the collision below is a hard reject rather than a
    // precedence rule — a plaintext secret sitting beside a reference is a
    // leak that a precedence rule would quietly preserve.
    value: z.string().optional(),
    secret_ref: secretRefSchema.optional(),
  })
  .strict()
  .superRefine((entry, ctx) => {
    const hasValue = entry.value !== undefined;
    const hasRef = entry.secret_ref !== undefined;
    if (hasValue && hasRef) {
      ctx.addIssue({
        code: "custom",
        path: ["secret_ref"],
        message:
          "an env entry carries either a literal value or a secret_ref, never both",
      });
    }
    if (!hasValue && !hasRef) {
      ctx.addIssue({
        code: "custom",
        path: ["value"],
        message: "an env entry needs either a value or a secret_ref",
      });
    }
  });

export const processEnvSchema = z
  .array(envEntrySchema)
  .default([])
  .superRefine((entries, ctx) => {
    const seen = new Set<string>();
    for (const [index, entry] of entries.entries()) {
      if (seen.has(entry.name)) {
        ctx.addIssue({
          code: "custom",
          path: [index, "name"],
          message: `duplicate env name ${entry.name} — the resolved environment would be ambiguous`,
        });
      }
      seen.add(entry.name);
    }
  });

export type ProcessEnv = z.infer<typeof processEnvSchema>;

// ---------------------------------------------------------------------------
// expectation (process_sources.expectation — spec §8, I-63)
// ---------------------------------------------------------------------------

/**
 * PER SOURCE, not per process (I-63): sources carry different cron cadences
 * and arrival profiles, and the source id takes the association position in
 * the ADR 0010 dedup key. A breach raises `process_stalled`; no expectation
 * means no alerting, since a quiet source can be entirely normal.
 */
export const processExpectationSchema = z
  .object({
    run_within_seconds: z.number().int().min(1),
  })
  .strict();

export type ProcessExpectation = z.infer<typeof processExpectationSchema>;

// ---------------------------------------------------------------------------
// API payloads (M5-A). The route path carries the process id; these validate
// the bodies. Group ownership and collection manageability are enforced
// in-route, where the row and the caller's identity are both known.
// ---------------------------------------------------------------------------

/** GOES spec §6.1: a transform is wired to source/output collections; an
 * extractor is selected on an ingest association and fixes up draft items.
 * Create-only — `processUpdateSchema` deliberately lacks it. */
export const PROCESS_KINDS = ["transform", "extractor"] as const;
export type ProcessKind = (typeof PROCESS_KINDS)[number];

/** GOES spec §6.5: an extractor runs once per scene group, so its ceiling is
 * an order of magnitude above a transform's. Resolved here rather than in the
 * create form alone, so an API caller gets the same default the UI shows. */
export const DEFAULT_MAX_RUNS_PER_HOUR: Record<ProcessKind, number> = {
  transform: 60,
  extractor: 600,
};

export const processCreateSchema = z
  .object({
    name: nonBlank("name is required"),
    description: z.string().default(""),
    group_id: nonBlank("group_id is required"),
    kind: z.enum(PROCESS_KINDS).default("transform"),
    enabled: z.boolean().default(true),
    // §7: operator-editable, floored at 1 so "pause by ceiling" stays
    // expressible without a zero that would read as "unlimited". Optional so
    // the kind-aware default below can apply; an explicit value always wins.
    max_runs_per_hour: z.number().int().min(1).optional(),
  })
  .strict()
  .transform((data) => ({
    ...data,
    max_runs_per_hour:
      data.max_runs_per_hour ?? DEFAULT_MAX_RUNS_PER_HOUR[data.kind],
  }));

export type ProcessCreate = z.infer<typeof processCreateSchema>;

/** Every field optional; `current_revision` is NOT here — only a deploy moves
 * it, so an update can never silently repoint what runs. */
export const processUpdateSchema = z
  .object({
    name: nonBlank("name must not be blank").optional(),
    description: z.string().optional(),
    group_id: nonBlank("group_id must not be blank").optional(),
    enabled: z.boolean().optional(),
    max_runs_per_hour: z.number().int().min(1).optional(),
  })
  .strict();

export type ProcessUpdate = z.infer<typeof processUpdateSchema>;

/**
 * A deploy: an immutable revision snapshot, which the route then makes
 * current. `code` is required for `inline_python` and
 * `inline_python_on_image` and refused for `container`, where the image is
 * the process (spec §3). Carrying both would leave two sources of truth
 * for what executes.
 */
export const processRevisionCreateSchema = z
  .object({
    runtime: processRuntimeSchema,
    code: z.string().nullable().default(null),
    env: processEnvSchema,
  })
  .strict()
  .superRefine((revision, ctx) => {
    const kind = revision.runtime.kind;
    const carriesCode = kind !== "container";
    if (carriesCode && (revision.code === null || revision.code.trim().length === 0)) {
      ctx.addIssue({ code: "custom", path: ["code"], message: `${kind} revisions need code` });
    }
    if (!carriesCode && revision.code !== null) {
      ctx.addIssue({
        code: "custom",
        path: ["code"],
        message: "container revisions carry no code: the image is the process",
      });
    }
  });

export type ProcessRevisionCreate = z.infer<typeof processRevisionCreateSchema>;

// ---------------------------------------------------------------------------
// Built-in processes (X-4, X-queue spec §7)
// ---------------------------------------------------------------------------

/** `POST /api/processes/builtin` — create-or-reuse the group's process for a
 * registry entry. Group ownership is enforced in-route. */
export const processBuiltinCreateSchema = z
  .object({
    builtin_id: nonBlank("builtin_id is required"),
    group_id: nonBlank("group_id is required"),
  })
  .strict();

export type ProcessBuiltinCreate = z.infer<typeof processBuiltinCreateSchema>;

/** `POST /api/processes/[id]/revisions` for a BUILT-IN process: "Update to
 * current" — a new revision from the registry template, the only way a
 * built-in process's revision moves. No runtime, no code: both come from the
 * registry the app was built with. */
export const processRevisionFromBuiltinSchema = z
  .object({ from_builtin: z.literal(true) })
  .strict();

/** Pinned so the route and its tests agree. */
export const BUILTIN_CODE_DEPLOY_REFUSAL =
  "This is a built-in extractor: its code comes from the platform's stactools " +
  "library and is read-only. Use \"Update to current\" to move it to the " +
  "registry the platform currently ships, or create a hand-written extractor.";

export const BUILTIN_TEMPLATE_ONLY_FOR_BUILTIN =
  "from_builtin applies only to processes created from the built-in extractor library";

export const BUILTIN_REGISTRY_DRIFT =
  "This built-in extractor is no longer in the platform's registry, so no " +
  "current template exists to deploy. The deployed revision keeps running.";

export const processSourceCreateSchema = z
  .object({
    collection_id: nonBlank("collection_id is required"),
    trigger: processTriggerSchema,
    expectation: processExpectationSchema.nullable().default(null),
    enabled: z.boolean().default(true),
  })
  .strict();

export type ProcessSourceCreate = z.infer<typeof processSourceCreateSchema>;

/** `collection_id` is absent on purpose: it is half the row's unique key and
 * an edge in the cycle-check graph (M5-D), so re-pointing a source at another
 * collection is a delete plus a create, not an edit. */
export const processSourceUpdateSchema = z
  .object({
    trigger: processTriggerSchema.optional(),
    expectation: processExpectationSchema.nullable().optional(),
    enabled: z.boolean().optional(),
  })
  .strict();

export type ProcessSourceUpdate = z.infer<typeof processSourceUpdateSchema>;

export const processOutputCreateSchema = z
  .object({ collection_id: nonBlank("collection_id is required") })
  .strict();

export type ProcessOutputCreate = z.infer<typeof processOutputCreateSchema>;
