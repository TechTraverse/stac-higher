/**
 * The built-in extractor registry (X queue design spec §5) — the Zod half.
 *
 * `tests/contract-fixtures/builtin-extractors.json` is the single source of
 * truth: the curated stactools packages an operator picks in the Data flow
 * form instead of writing an extractor by hand. Both runtimes read it — this
 * schema for the picker and the process template (X-4), and
 * `services/pipeline/src/pipeline/process/builtin.py` for adapter dispatch and
 * the launch-time check (X-2/X-3) — so a field or an enum drifting on either
 * side fails one of the two suites.
 *
 * The usual asymmetry applies, with the writer being a person editing the
 * registry rather than a form: Zod is the STRICT gate (unknown keys rejected,
 * so a typo fails CI at review time) and the Python reader is LENIENT (unknown
 * keys ignored), so a registry that grows a key cannot brick a pipeline image
 * built before it.
 *
 * Packaging is deliberately NOT decided here. `parseBuiltinExtractors` takes a
 * document; how the fixture reaches each image (a COPY, a mount, an env
 * override) is X-2's and X-4's call — neither build context includes
 * `tests/` today.
 */
import { z } from "zod";
import { nonBlank } from "@/lib/schema-helpers";
import { PROCESS_NETWORK_LEVELS } from "@/lib/processes/schemas";

/** Whether an entry's package can build an item from ONE staged file, or needs
 * the whole group the ingest `grouping.rule` assembled (spec §3.6). */
export const BUILTIN_SUPPORTS = ["single_file", "grouped"] as const;
export type BuiltinSupports = (typeof BUILTIN_SUPPORTS)[number];

/** Whether the product has an anonymous public source. `credentialed` entries
 * ship import-smoked and unit-tested but with no live gate (I-105). */
export const BUILTIN_ACCESS = ["anonymous", "credentialed"] as const;
export type BuiltinAccess = (typeof BUILTIN_ACCESS)[number];

/** Every registry package lives in the `stactools-` namespace on PyPI; the
 * import name is the remainder with dashes as underscores (`moduleName`). */
const PACKAGE_RE = /^stactools-[a-z0-9]+(?:-[a-z0-9]+)*$/;
/**
 * A CONCRETE pin, not a range: the image is built from `package==version` and
 * the pin check compares the two literally, so `0.1.x` (the spec's
 * illustrative placeholder) is not a value a registry entry may carry.
 */
const VERSION_RE = /^\d+(?:\.\d+){0,3}(?:(?:a|b|rc|post|dev)\d+)?$/;
/** One importable module segment under `stac_higher_stactools.adapters`. */
const ADAPTER_RE = /^[a-z][a-z0-9_]*$/;

const builtinRuntimeSchema = z
  .object({
    memory_mb: z.number().int().min(128),
    timeout_seconds: z.number().int().min(1).max(86_400),
    network: z
      .object({ level: z.enum(PROCESS_NETWORK_LEVELS) })
      .strict(),
  })
  .strict();

export const builtinExtractorSchema = z
  .object({
    id: nonBlank("id is required").refine(
      (s) => /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(s),
      "id must be a lowercase slug",
    ),
    label: nonBlank("label is required"),
    package: nonBlank("package is required").refine(
      (s) => PACKAGE_RE.test(s),
      "package must be a stactools-* distribution name",
    ),
    version: nonBlank("version is required").refine(
      (s) => VERSION_RE.test(s),
      "version must be a concrete pin, not a range",
    ),
    adapter: nonBlank("adapter is required").refine(
      (s) => ADAPTER_RE.test(s),
      "adapter must be a module name under stac_higher_stactools.adapters",
    ),
    supports: z.enum(BUILTIN_SUPPORTS),
    products: z.array(nonBlank("a product hint may not be blank")).min(1),
    access: z.enum(BUILTIN_ACCESS),
    runtime: builtinRuntimeSchema,
    extensions: z.array(nonBlank("an extension id may not be blank")).default([]),
  })
  .strict();

export type BuiltinExtractor = z.output<typeof builtinExtractorSchema>;

export const builtinExtractorRegistrySchema = z
  .object({
    style: z.literal("registry"),
    version: z.number().int().min(1),
    description: z.string().optional(),
    extractors: z.array(builtinExtractorSchema).min(1),
    cases: z.array(z.unknown()).optional(),
  })
  .passthrough()
  .superRefine((doc, ctx) => {
    for (const field of ["id", "adapter"] as const) {
      const seen = new Set<string>();
      for (const entry of doc.extractors) {
        if (seen.has(entry[field])) {
          ctx.addIssue({
            code: "custom",
            message: `duplicate ${field} "${entry[field]}"`,
            path: ["extractors"],
          });
        }
        seen.add(entry[field]);
      }
    }
  });

/** The registry document, validated. Throws on anything the gate refuses. */
export function parseBuiltinExtractors(doc: unknown): BuiltinExtractor[] {
  return builtinExtractorRegistrySchema.parse(doc).extractors;
}

/**
 * The package's import name — `stactools-goes-glm` → `stactools.goes_glm`.
 * The image's build-time smoke test imports exactly this for every entry
 * (spec §6), so the derivation is part of the contract rather than a
 * convenience.
 */
export function moduleName(entry: BuiltinExtractor): string {
  return `stactools.${entry.package.slice("stactools-".length).replaceAll("-", "_")}`;
}

/** The `package==version` pin the runtime image must install for an entry. */
export function pinFor(entry: BuiltinExtractor): string {
  return `${entry.package}==${entry.version}`;
}
