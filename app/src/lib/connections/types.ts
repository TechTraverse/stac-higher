/**
 * Client-facing connection types.
 *
 * These are re-exported from the server-side storage module as **type-only**
 * imports, so nothing from `storage.ts` (pg client, node:crypto) is ever
 * bundled into the browser — the re-export is fully erased at build time. The
 * shapes are the API contract: credentials are never present (only
 * `credentials_set`), host keys are metadata-only (fingerprint), and raw
 * secrets are never returned.
 */
export type { ApiConnection as Connection } from "./storage";
export type { ApiConnectionCheck as ConnectionCheck } from "./storage";
