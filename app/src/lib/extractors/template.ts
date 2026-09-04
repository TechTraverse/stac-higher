/**
 * The process a registry entry instantiates as (X-4, X-queue spec §6/§7).
 *
 * A built-in process is an ordinary `kind: extractor` process whose revision
 * is the two-line body below on the `stactools` runtime image (X-3's alias):
 * the wrapper in that image does everything else. Upgrading is a new revision
 * from THIS template against the registry the platform currently ships —
 * never an edit of the body — which is why the template is a function of the
 * registry entry and nothing else.
 */
import {
  DEFAULT_MAX_RUNS_PER_HOUR,
  processRevisionCreateSchema,
  type ProcessRevisionCreate,
} from "@/lib/processes/schemas";
import type { BuiltinExtractor } from "./schemas";

/** A deployable template: the write-gate shape with `code` known non-null. */
export interface BuiltinRevisionTemplate {
  runtime: ProcessRevisionCreate["runtime"];
  code: string;
  env: ProcessRevisionCreate["env"];
}

export function builtinProcessBody(entry: BuiltinExtractor): string {
  return `from stac_higher_stactools import run\nrun(${JSON.stringify(entry.id)})\n`;
}

/**
 * Revision 1 (and every "Update to current") for an entry. Parsed through the
 * WRITE gate on purpose: the template can never store a runtime the gate
 * would refuse from a form — network above the cap, a container image — and
 * a registry entry that drifts outside the gate fails here, loudly.
 */
export function builtinRevisionTemplate(entry: BuiltinExtractor): BuiltinRevisionTemplate {
  const parsed = processRevisionCreateSchema.parse({
    runtime: {
      kind: "inline_python",
      image: null,
      memory_mb: entry.runtime.memory_mb,
      timeout_seconds: entry.runtime.timeout_seconds,
      retry: { max_attempts: 3, backoff: "exponential" },
      network: { level: entry.runtime.network.level, hosts: [] },
      runtime_image: "stactools",
    },
    code: builtinProcessBody(entry),
    env: [],
  });
  return { runtime: parsed.runtime, code: parsed.code ?? builtinProcessBody(entry), env: parsed.env };
}

/** The process row a registry entry creates as (spec §7). */
export function builtinProcessDefaults(entry: BuiltinExtractor) {
  return {
    name: entry.label,
    description:
      `Built-in extractor: ${entry.package} ${entry.version} ` +
      `(${entry.products.join(", ")}). Managed by the platform — code is read-only.`,
    maxRunsPerHour: DEFAULT_MAX_RUNS_PER_HOUR.extractor,
  };
}
