import { defineMiddleware } from "astro:middleware";
import { runMigrations } from "@/lib/db/migrate";
import { resolveRequestAuth } from "@/lib/auth/bearer";
import { getAuthConfig } from "@/lib/auth/config";
import { anonymous } from "@/lib/auth/types";
import { applyApiGuard } from "@/lib/authz/guard";
import { buildRuntimeConfig } from "@/lib/runtime-config/server";

export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url;

  // Browser runtime config (R-1, spec §8), read from the env per request and
  // resolved HERE, before the page starts streaming: a bad value thrown from
  // the Layout would arrive after the 200 status line (#194). Page routes
  // only — the API never reads it, and a bad browser-facing URL must not take
  // the API down. The body stays generic; the log names the variable.
  if (!pathname.startsWith("/api/")) {
    try {
      context.locals.runtimeConfig = buildRuntimeConfig();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[runtime-config] ${pathname}: ${msg}`);
      return new Response("Internal server error", {
        status: 500,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
  }

  if (
    pathname.startsWith("/api/extensions") ||
    pathname.startsWith("/api/audit") ||
    pathname.startsWith("/api/connections") ||
    pathname.startsWith("/api/collections") ||
    pathname.startsWith("/api/alerts") ||
    pathname.startsWith("/api/channels") ||
    pathname.startsWith("/api/processes") ||
    pathname.startsWith("/api/monitoring")
  ) {
    await runMigrations();
  }

  // Resolve the canonical identity for every request: session cookie →
  // claims mapping (refreshing the access token when it is near expiry);
  // on /api/* in oidc mode an `Authorization: Bearer <JWT>` is tried when
  // no session is present (Phase 7 §3 — session wins, bypass untouched).
  // Auth failures never break a page — they degrade to anonymous.
  try {
    const { auth, bearerToken } = await resolveRequestAuth(
      context.cookies,
      context.request,
    );
    context.locals.auth = auth;
    if (bearerToken !== undefined) context.locals.bearerToken = bearerToken;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[auth] Auth resolution failed, treating as anonymous: ${msg}`);
    context.locals.auth = anonymous(getAuthConfig().mode);
  }

  // RBAC enforcement (ROADMAP §7): gated API mutations require
  // operator|admin and land one audit_log row each (allowed or denied).
  // Reads stay open — no existing page or read route is gated on login in
  // Phase 1, and the dev-bypass identity (operator) keeps existing flows
  // working without an IdP.
  return applyApiGuard(context, next);
});
