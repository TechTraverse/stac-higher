/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    /** Canonical auth context for the request — set by `src/middleware.ts`.
     * Future RBAC/permission middleware consumes this and nothing else. */
    auth: import("./lib/auth/types").AuthContext;
    /** Optional detail a gated route adds to its audit row (C-3). Merged
     * beneath the guard's method/path/outcome/status (and reason on a
     * denial); redacted by `writeAudit`. Never put a secret here. */
    auditDetail?: Record<string, unknown>;
    /** Browser runtime config (R-1, spec §8), resolved from the env by
     * `src/middleware.ts` before a page route renders, so a bad value is a 500
     * instead of an error mid-stream (#194). Set on every non-`/api/` route;
     * `Layout.astro` injects it. Unset on API routes. */
    runtimeConfig?: import("./lib/runtime-config").RuntimeConfig;
  }
}
