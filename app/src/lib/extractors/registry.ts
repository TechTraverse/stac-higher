/**
 * The built-in extractor registry as the APP reads it (X-4, X-queue spec §5/§7).
 *
 * `tests/contract-fixtures/builtin-extractors.json` is imported at BUILD time
 * and bundled — the same document the runtime image ships, reached without a
 * runtime file read or an env var. Outside the repo (the app image) the file
 * arrives through the `fixtures` named build context, which `app/Dockerfile`
 * copies to the path this import resolves to before `npm run build`; the same
 * mechanism the pipeline and runtime images use (X-2). The registry is
 * versioned with the build, which is what the spec asks for: a picker and a
 * template that describe exactly what the platform currently ships.
 */
import registryDocument from "../../../../tests/contract-fixtures/builtin-extractors.json";
import { parseBuiltinExtractors, type BuiltinExtractor } from "./schemas";

let parsed: BuiltinExtractor[] | null = null;

/** The registry, in the picker's order. Parsed once; the strict schema means
 * a registry typo fails at build/test time, never at a request. */
export function builtinExtractors(): BuiltinExtractor[] {
  parsed ??= parseBuiltinExtractors(registryDocument);
  return parsed;
}

export function findBuiltinExtractor(id: string): BuiltinExtractor | null {
  return builtinExtractors().find((entry) => entry.id === id) ?? null;
}
