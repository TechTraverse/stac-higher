/**
 * GET /api/auth/login — start the OIDC authorization-code + PKCE flow.
 *
 * Seals { state, verifier, nonce, returnTo } into a short-lived httpOnly
 * transaction cookie, then redirects to the IdP's authorize endpoint.
 * `?returnTo=` must be a same-origin path; anything else falls back to "/".
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import {
  codeChallengeS256,
  discover,
  generateRandomToken,
} from "@/lib/auth/oidc";
import {
  TXN_COOKIE,
  isSecureOrigin,
  sealValue,
} from "@/lib/auth/session";

const TXN_MAX_AGE_S = 10 * 60;

export function sanitizeReturnTo(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export const GET: APIRoute = async ({ url, cookies, redirect }) => {
  const cfg = getAuthConfig();
  const returnTo = sanitizeReturnTo(url.searchParams.get("returnTo"));

  // Bypass mode has no IdP — "login" is a no-op redirect.
  if (cfg.mode === "bypass") return redirect(returnTo, 302);

  if (!cfg.sessionSecret) {
    return new Response(
      JSON.stringify({
        error:
          "OIDC login requires SESSION_SECRET to be set (any long random string).",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  try {
    const endpoints = await discover(cfg);

    const state = generateRandomToken();
    const nonce = generateRandomToken();
    const verifier = generateRandomToken();

    const txn = await sealValue(
      { state, nonce, verifier, returnTo },
      cfg.sessionSecret,
      TXN_MAX_AGE_S,
    );
    cookies.set(TXN_COOKIE, txn, {
      httpOnly: true,
      sameSite: "lax",
      secure: isSecureOrigin(cfg.redirectUri),
      path: "/",
      maxAge: TXN_MAX_AGE_S,
    });

    const authorize = new URL(endpoints.authorizationEndpoint);
    authorize.searchParams.set("response_type", "code");
    authorize.searchParams.set("client_id", cfg.clientId);
    authorize.searchParams.set("redirect_uri", cfg.redirectUri);
    authorize.searchParams.set("scope", "openid profile email");
    authorize.searchParams.set("state", state);
    authorize.searchParams.set("nonce", nonce);
    authorize.searchParams.set("code_challenge", codeChallengeS256(verifier));
    authorize.searchParams.set("code_challenge_method", "S256");

    return redirect(authorize.toString(), 302);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return new Response(JSON.stringify({ error: message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
