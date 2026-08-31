import { defineMiddleware } from "astro:middleware";
import { runMigrations } from "@/lib/db/migrate";
import { resolveRequestAuth } from "@/lib/auth/bearer";
import { getAuthConfig } from "@/lib/auth/config";
import { anonymous } from "@/lib/auth/types";
import { applyApiGuard } from "@/lib/authz/guard";

export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url;
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
