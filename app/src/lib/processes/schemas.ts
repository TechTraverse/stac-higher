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

export const PROCESS_RUNTIME_KINDS = ["inline_python", "container"] as const;
export type ProcessRuntimeKind = (typeof PROCESS_RUNTIME_KINDS)[number];

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

const runtimeLimits = {
  memory_mb: z.number().int().min(MEMORY_MB_MIN).default(512),
  timeout_seconds: z.number().int().min(1).max(TIMEOUT_SECONDS_MAX).default(900),
  // Function default so an omitted object is PARSED through its schema
  // (applying the inner defaults) instead of stored as a bare `{}`.
  retry: processRetrySchema.default(() => processRetrySchema.parse({})),
};

const inlinePythonRuntimeSchema = z
  .object({
    kind: z.literal("inline_python"),
    // Present and null-only: the arm carries the field so the two runtime
    // shapes stay one document, but inline code has nothing to run an image
    // in, and a stray image would read as if it were honored.
    image: z.null().default(null),
    ...runtimeLimits,
  })
  .strict();

const containerRuntimeSchema = z
  .object({
    kind: z.literal("container"),
    image: nonBlank("container runtime requires an image reference"),
    ...runtimeLimits,
  })
  .strict();

/**
 * The full §5.6 runtime shape, BOTH arms — for READING a stored runtime, since
 * a future revision may legitimately carry `container`.
 *
 * It deliberately does NOT get the short name: `processRuntimeSchema` below is
 * the write gate, so reaching for the obvious import cannot accidentally store
 * a runtime this slice refuses to run. (The sibling `connections/schemas.ts`
 * makes the same call in the strongest form — it exports no response-side
 * credential schema at all.)
 */
export const processRuntimeReadSchema = z.discriminatedUnion("kind", [
  inlinePythonRuntimeSchema,
  containerRuntimeSchema,
]);

export type ProcessRuntime = z.infer<typeof processRuntimeReadSchema>;

/** The slice-1 refusal message, pinned so the route and its tests agree. */
export const CONTAINER_RUNTIME_REFUSAL =
  "container runtimes are not accepted yet — processes run on the platform " +
  "executor image (inline_python). User-supplied images are a supply-chain " +
  "review surface deferred past the first accreditation scope (ADR 0013).";

/**
 * The WRITE gate (M5 slice 1, spec §4) — and the default name, so this is what
 * a route author gets by reaching for the obvious import. The contract carries
 * `container` so nothing is foreclosed and the pipeline reader accepts it, but
 * the app refuses to store one. The golden fixture pins every `container` case
 * as `app: reject` / `pipeline: accept` — that asymmetry is the decision, not
 * an oversight.
 */
export const processRuntimeSchema = processRuntimeReadSchema.refine(
  (runtime) => runtime.kind !== "container",
  { path: ["kind"], message: CONTAINER_RUNTIME_REFUSAL },
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

export const processCreateSchema = z
  .object({
    name: nonBlank("name is required"),
    description: z.string().default(""),
    group_id: nonBlank("group_id is required"),
    enabled: z.boolean().default(true),
    // §7: operator-editable, floored at 1 so "pause by ceiling" stays
    // expressible without a zero that would read as "unlimited".
    max_runs_per_hour: z.number().int().min(1).default(60),
  })
  .strict();

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
 * current. `code` is required for `inline_python` and refused for anything
 * else — the runtime kind decides where the code lives, so carrying both
 * would leave two sources of truth for what executes.
 */
export const processRevisionCreateSchema = z
  .object({
    runtime: processRuntimeSchema,
    code: z.string().nullable().default(null),
    env: processEnvSchema,
  })
  .strict()
  .superRefine((revision, ctx) => {
    const inline = revision.runtime.kind === "inline_python";
    if (inline && (revision.code === null || revision.code.trim().length === 0)) {
      ctx.addIssue({
        code: "custom",
        path: ["code"],
        message: "inline_python revisions need code",
      });
    }
    if (!inline && revision.code !== null) {
      ctx.addIssue({
        code: "custom",
        path: ["code"],
        message: "only inline_python revisions carry code",
      });
    }
  });

export type ProcessRevisionCreate = z.infer<typeof processRevisionCreateSchema>;

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
