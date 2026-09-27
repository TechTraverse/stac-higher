/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    /** Canonical auth context for the request — set by `src/middleware.ts`.
     * Future RBAC/permission middleware consumes this and nothing else. */
    auth: import("./lib/auth/types").AuthContext;
    /** Optional detail a gated route adds to its audit row (C-3). Merged by
     * the guard beneath `outcome`/`status`; redacted by `writeAudit`. Never
     * put a secret here. */
    auditDetail?: Record<string, unknown>;
  }
}
