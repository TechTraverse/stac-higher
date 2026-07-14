/**
 * Per-request auth resolution — the source of `locals.auth`.
 *
 * RBAC seam: the Phase 1 follow-on permission middleware builds on
 * `locals.auth` exclusively. It never re-reads cookies or tokens; the
 * `AuthContext` produced here is the complete authority input.
 *
 * Trust model: identity is derived by decoding (not re-verifying) the access
 * token stored in the session. That is sound because the token only ever
 * enters the session server-side, straight from the IdP token endpoint, and
 * the session cookie is AEAD-encrypted (`dir`/A256GCM) with SESSION_SECRET —
 * a client cannot tamper with it. The ID token IS signature-verified against
 * the issuer JWKS at login (callback route). Decoding per request keeps auth
 * working even when the IdP is briefly unreachable.
 */
import { decodeJwt } from "jose";
import { getAuthConfig, getBypassIdentity, type Env } from "./config";
import { loadClaimsMapping, mapClaims } from "./claims";
import { discover, refreshAccessToken, tokenResponseToSession } from "./oidc";
import {
  clearSession,
  readSession,
  writeSession,
  type CookieJar,
} from "./session";
import { anonymous, type AuthContext } from "./types";

/** Refresh the access token when it expires within this window. */
export const REFRESH_SKEW_S = 60;

export async function resolveAuthContext(
  cookies: CookieJar,
  env: Env = process.env,
): Promise<AuthContext> {
  const cfg = getAuthConfig(env);

  if (cfg.mode === "bypass") {
    return {
      authenticated: true,
      mode: "bypass",
      identity: getBypassIdentity(env),
    };
  }

  if (!cfg.sessionSecret) return anonymous("oidc");

  let session = await readSession(cookies, cfg.sessionSecret);
  if (!session) return anonymous("oidc");

  const now = Math.floor(Date.now() / 1000);
  if (session.expiresAt - now < REFRESH_SKEW_S) {
    if (!session.refreshToken) {
      clearSession(cookies);
      return anonymous("oidc");
    }
    try {
      const endpoints = await discover(cfg);
      const tokens = await refreshAccessToken(
        cfg,
        endpoints,
        session.refreshToken,
      );
      session = tokenResponseToSession(tokens, session);
      await writeSession(cookies, session, {
        sessionSecret: cfg.sessionSecret,
        sessionMaxAgeS: cfg.sessionMaxAgeS,
        redirectUri: cfg.redirectUri,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[auth] Token refresh failed, ending session: ${msg}`);
      clearSession(cookies);
      return anonymous("oidc");
    }
  }

  try {
    const identity = mapClaims(
      decodeJwt(session.accessToken) as Record<string, unknown>,
      loadClaimsMapping(env),
    );
    return { authenticated: true, mode: "oidc", identity };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[auth] Could not derive identity from session: ${msg}`);
    clearSession(cookies);
    return anonymous("oidc");
  }
}
