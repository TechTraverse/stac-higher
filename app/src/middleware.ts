import { defineMiddleware } from "astro:middleware";
import { runMigrations } from "@/lib/db/migrate";
import { resolveAuthContext } from "@/lib/auth/resolve";
import { getAuthConfig } from "@/lib/auth/config";
import { anonymous } from "@/lib/auth/types";

export const onRequest = defineMiddleware(async (context, next) => {
  if (context.url.pathname.startsWith("/api/extensions")) {
    await runMigrations();
  }

  // Resolve the canonical identity for every request (session cookie →
  // claims mapping; refreshes the access token when it is near expiry).
  // Auth failures never break a page — they degrade to anonymous.
  //
  // RBAC seam: `locals.auth` is the single input for the upcoming permission
  // middleware (ROADMAP §7). Enforcement does NOT happen here; no existing
  // page or route is gated on login in Phase 1.
  try {
    context.locals.auth = await resolveAuthContext(context.cookies);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[auth] Auth resolution failed, treating as anonymous: ${msg}`);
    context.locals.auth = anonymous(getAuthConfig().mode);
  }

  return next();
});
