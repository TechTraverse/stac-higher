/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    /** Canonical auth context for the request — set by `src/middleware.ts`.
     * Future RBAC/permission middleware consumes this and nothing else. */
    auth: import("./lib/auth/types").AuthContext;
  }
}
