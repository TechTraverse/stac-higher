/**
 * GET /api/auth/callback — OIDC redirect URI.
 *
 * Validates state against the sealed transaction cookie, exchanges the code
 * (PKCE), verifies the ID token against the issuer JWKS (nonce included),
 * writes the encrypted session cookie, and redirects to the original page.
 */
import type { APIRoute } from "astro";
import { getAuthConfig } from "@/lib/auth/config";
import {
  discover,
  exchangeCodeForTokens,
  tokenResponseToSession,
  verifyIdToken,
} from "@/lib/auth/oidc";
import {
  TXN_COOKIE,
  openValue,
  writeSession,
} from "@/lib/auth/session";
import { sanitizeReturnTo } from "./login";

interface LoginTxn {
  state: string;
  nonce: string;
  verifier: string;
  returnTo: string;
}

function badRequest(message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status: 400,
    headers: { "Content-Type": "application/json" },
  });
}

export const GET: APIRoute = async ({ url, cookies, redirect }) => {
  const cfg = getAuthConfig();
  if (cfg.mode === "bypass" || !cfg.sessionSecret) return redirect("/", 302);

  const idpError = url.searchParams.get("error");
  if (idpError) {
    const description = url.searchParams.get("error_description") ?? "";
    return badRequest(`Login failed at the identity provider: ${idpError}${description ? ` (${description})` : ""}`);
  }

  const txnToken = cookies.get(TXN_COOKIE)?.value;
  cookies.delete(TXN_COOKIE, { path: "/" });
  if (!txnToken) {
    return badRequest("Missing or expired login transaction — start again at /api/auth/login");
  }
  const txn = await openValue<LoginTxn>(txnToken, cfg.sessionSecret);
  if (!txn) {
    return badRequest("Invalid login transaction — start again at /api/auth/login");
  }

  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (!state || state !== txn.state) return badRequest("State mismatch");
  if (!code) return badRequest("Missing authorization code");

  try {
    const endpoints = await discover(cfg);
    const tokens = await exchangeCodeForTokens(cfg, endpoints, code, txn.verifier);
    if (tokens.id_token) {
      await verifyIdToken(cfg, endpoints, tokens.id_token, txn.nonce);
    }
    await writeSession(cookies, tokenResponseToSession(tokens), {
      sessionSecret: cfg.sessionSecret,
      sessionMaxAgeS: cfg.sessionMaxAgeS,
      redirectUri: cfg.redirectUri,
    });
    return redirect(sanitizeReturnTo(txn.returnTo), 302);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return new Response(JSON.stringify({ error: `Login failed: ${message}` }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
