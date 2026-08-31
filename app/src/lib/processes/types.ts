/**
 * Client-facing process types.
 *
 * Type-only re-exports from the server-side storage module, so nothing from
 * `storage.ts` (the pg client) is ever bundled into the browser — the
 * re-export is fully erased at build time. The connections/types.ts pattern.
 */
export type { ApiProcess as Process } from "./storage";
export type { ApiProcessRevision as ProcessRevision } from "./storage";
export type { ApiProcessSource as ProcessSource } from "./storage";
export type { ApiProcessOutput as ProcessOutput } from "./storage";
export type { ApiProcessCheck as ProcessCheck } from "./storage";
export type { ApiProcessRun as ProcessRun } from "./storage";
