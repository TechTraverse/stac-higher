/**
 * GET /api/auth/logout — clear the session and end the IdP session.
 *
 * Always clears the local session cookie; when the IdP advertises an
 * end_session endpoint, redirects there (RP-initiated logout with
 * id_token_hint) and back to the app afterwards.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import { discover } from "@/lib/auth/oidc";
import { clearSession, readSession } from "@/lib/auth/session";
import { writeAudit } from "@/lib/audit/log";

export const GET: APIRoute = async ({ request, cookies, redirect, locals }) => {
  // Same guard as /api/proxy: never act on a cross-site request.
  const secFetchSite = request.headers.get("sec-fetch-site");
  if (secFetchSite === "cross-site") {
    return new Response(JSON.stringify({ error: "Cross-site logout rejected" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  const cfg = getAuthConfig();
  if (cfg.mode === "bypass") return redirect("/", 302);

  const session = cfg.sessionSecret
    ? await readSession(cookies, cfg.sessionSecret)
    : null;
  clearSession(cookies);

  // Audit the logout (ROADMAP §5.5). `locals.auth` was resolved by the
  // middleware from the session cookie before it was cleared. `writeAudit`
  // never throws, so this cannot break logout.
  const auth = locals.auth;
  if (auth?.authenticated && auth.mode === "oidc") {
    await writeAudit({
      actor: auth.identity.sub,
      actorGroups: auth.identity.groups,
      action: "logout",
      resourceType: "session",
      detail: { mode: "oidc" },
    });
  }

  try {
    const endpoints = await discover(cfg);
    if (endpoints.endSessionEndpoint) {
      const appOrigin = new URL(cfg.redirectUri).origin;
      const endSession = new URL(endpoints.endSessionEndpoint);
      endSession.searchParams.set("client_id", cfg.clientId);
      endSession.searchParams.set("post_logout_redirect_uri", `${appOrigin}/`);
      if (session?.idToken) {
        endSession.searchParams.set("id_token_hint", session.idToken);
      }
      return redirect(endSession.toString(), 302);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[auth] IdP end_session unavailable, local logout only: ${msg}`);
  }
  return redirect("/", 302);
};
