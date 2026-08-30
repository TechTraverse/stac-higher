/**
 * Bearer-token auth for `/api/*` (Phase 7 push ingest, spec §3).
 *
 * External push clients are not browsers: no PKCE login, no session cookie.
 * In `oidc` mode, API requests may instead carry `Authorization: Bearer
 * <JWT>` minted by the same IdP (client-credentials grant on a confidential
 * client). The token is signature-verified with `jose` against the issuer
 * JWKS (discovered through the existing `discover()` cache — never
 * per-request), with standard `exp`/`iss` checks and an audience check
 * mirroring the auth proxy's `ALLOWED_JWT_AUDIENCES`. Verified claims run
 * through the SAME claims mapper as session identities, so `locals.auth` is
 * a normal `CanonicalIdentity` and every downstream consumer — permission
 * guard, group scoping, audit rows (`actor` = token `sub`) — works
 * unchanged.
 *
 * Precedence (spec §3): a valid session cookie wins; the bearer path is
 * tried only when no session identity resolved. An invalid/expired bearer
 * degrades to anonymous exactly like a failed session refresh
 * (`docs/auth.md` posture: degrade, never error-page) — gated routes then
 * 401 normally. Bypass mode is untouched: everything is already the static
 * dev identity, and bearer headers are ignored.
 */
import { createRemoteJWKSet, jwtVerify } from "jose";
import { getAuthConfig, type Env } from "./config";
import { loadClaimsMapping, mapClaims } from "./claims";
import { discover } from "./oidc";
import { resolveAuthContext } from "./resolve";
import type { CookieJar } from "./session";
import { anonymous, type AuthContext } from "./types";

declare global {
  namespace App {
    interface Locals {
      /** The raw bearer token, present ONLY when `locals.auth` was derived
       * from an `Authorization: Bearer` header (never from a session cookie
       * or bypass). The ADR 0008 BFF catalog route forwards it for bearer
       * callers instead of a session token (spec §4.3 — P7-D). */
      bearerToken?: string;
    }
  }
}

/** Default accepted audience — mirrors the auth proxy's
 * `ALLOWED_JWT_AUDIENCES=stac-higher` (ADR 0002). */
export const DEFAULT_BEARER_AUDIENCES = ["stac-higher"] as const;

/** Accepted `aud` values: `AUTH_BEARER_AUDIENCES` (comma-separated),
 * defaulting to `stac-higher`. The token's `aud` must contain at least one. */
export function getBearerAudiences(env: Env = process.env): string[] {
  const raw = env.AUTH_BEARER_AUDIENCES;
  if (!raw) return [...DEFAULT_BEARER_AUDIENCES];
  const list = raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return list.length > 0 ? list : [...DEFAULT_BEARER_AUDIENCES];
}

/** Extract the token from an `Authorization: Bearer <token>` header.
 * Returns null when the header is absent or not Bearer-shaped. */
export function extractBearerToken(request: Request): string | null {
  const header = request.headers.get("authorization");
  if (!header) return null;
  const match = /^Bearer\s+(\S+)$/i.exec(header.trim());
  return match ? match[1] : null;
}

// Remote JWK sets cache fetched keys internally (and re-fetch on an unknown
// kid with a cooldown), so cache one per JWKS URI — the same pattern as the
// ID-token verifier in oidc.ts.
const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function getJwks(jwksUri: string) {
  let jwks = jwksCache.get(jwksUri);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(jwksUri));
    jwksCache.set(jwksUri, jwks);
  }
  return jwks;
}

/**
 * Verify a bearer JWT (signature via issuer JWKS, `iss`, `exp`, audience)
 * and map its claims to a canonical identity. Returns `null` on ANY failure
 * — the caller degrades to anonymous; this never throws. No-op outside
 * `oidc` mode.
 */
export async function verifyBearerToken(
  token: string,
  env: Env = process.env,
): Promise<AuthContext | null> {
  const cfg = getAuthConfig(env);
  if (cfg.mode !== "oidc") return null;
  try {
    const endpoints = await discover(cfg);
    // Both issuer forms are deployment config, and which one a
    // server-to-server client sees in `iss` depends on where it fetched its
    // token (the compose stack's localhost:8180 vs keycloak:8080 split).
    const issuers = [...new Set([cfg.issuer, cfg.internalIssuer])];
    const { payload } = await jwtVerify(token, getJwks(endpoints.jwksUri), {
      issuer: issuers,
      audience: getBearerAudiences(env),
    });
    const identity = mapClaims(
      payload as Record<string, unknown>,
      loadClaimsMapping(env),
    );
    return { authenticated: true, mode: "oidc", identity };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[auth] Bearer token rejected, treating as anonymous: ${msg}`);
    return null;
  }
}

export interface ResolvedRequestAuth {
  auth: AuthContext;
  /** Set only when `auth` came from a bearer token (see the Locals doc). */
  bearerToken?: string;
}

/**
 * Full per-request auth resolution with the §3 precedence: the session
 * cookie path first (`resolveAuthContext` — refresh, claims mapping); the
 * bearer path only for `/api/*` requests that resolved anonymous in `oidc`
 * mode. Never throws — every failure degrades to anonymous.
 */
export async function resolveRequestAuth(
  cookies: CookieJar,
  request: Request,
  env: Env = process.env,
): Promise<ResolvedRequestAuth> {
  let auth: AuthContext;
  try {
    auth = await resolveAuthContext(cookies, env);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(
      `[auth] Auth resolution failed, treating as anonymous: ${msg}`,
    );
    auth = anonymous(getAuthConfig(env).mode);
  }
  if (auth.authenticated || auth.mode !== "oidc") return { auth };

  if (!new URL(request.url).pathname.startsWith("/api/")) return { auth };
  const token = extractBearerToken(request);
  if (!token) return { auth };

  const bearerAuth = await verifyBearerToken(token, env);
  return bearerAuth ? { auth: bearerAuth, bearerToken: token } : { auth };
}
