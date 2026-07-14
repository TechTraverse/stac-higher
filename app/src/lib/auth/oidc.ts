/**
 * OIDC client: discovery, PKCE, code exchange, refresh, ID-token
 * verification (jose + issuer JWKS).
 *
 * Server-to-server calls (discovery, token endpoint, JWKS) use
 * `internalIssuer`; browser-facing endpoints (authorize, end_session) are
 * rewritten onto the public `issuer` when the two differ (the compose stack's
 * localhost:8180 vs keycloak:8080 split).
 *
 * These fetches deliberately do NOT go through `safeFetch`: the issuer is
 * deployment configuration (not user input), and in local dev it is
 * legitimately a loopback address.
 */
import { createHash, randomBytes } from "node:crypto";
import { createRemoteJWKSet, decodeJwt, jwtVerify } from "jose";
import type { AuthConfig } from "./config";
import type { SessionData } from "./session";

const FETCH_TIMEOUT_MS = 10_000;
const DISCOVERY_TTL_MS = 10 * 60 * 1000;

export interface OidcEndpoints {
  authorizationEndpoint: string;
  tokenEndpoint: string;
  endSessionEndpoint: string | null;
  jwksUri: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
  token_type?: string;
}

export class OidcError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OidcError";
  }
}

const discoveryCache = new Map<
  string,
  { endpoints: OidcEndpoints; fetchedAt: number }
>();

/** Rewrite an internal-issuer URL to its browser-facing equivalent. */
function toPublicUrl(url: string, cfg: AuthConfig): string {
  if (cfg.internalIssuer === cfg.issuer) return url;
  return url.startsWith(cfg.internalIssuer)
    ? cfg.issuer + url.slice(cfg.internalIssuer.length)
    : url;
}

export async function discover(cfg: AuthConfig): Promise<OidcEndpoints> {
  const cacheKey = `${cfg.internalIssuer}|${cfg.issuer}`;
  const cached = discoveryCache.get(cacheKey);
  if (cached && Date.now() - cached.fetchedAt < DISCOVERY_TTL_MS) {
    return cached.endpoints;
  }

  const url = `${cfg.internalIssuer}/.well-known/openid-configuration`;
  let res: Response;
  try {
    res = await fetch(url, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new OidcError(`OIDC discovery failed (${url}): ${msg}`);
  }
  if (!res.ok) {
    throw new OidcError(`OIDC discovery failed (${url}): HTTP ${res.status}`);
  }
  const doc = (await res.json()) as Record<string, unknown>;
  const authorization = doc.authorization_endpoint;
  const token = doc.token_endpoint;
  const jwks = doc.jwks_uri;
  if (
    typeof authorization !== "string" ||
    typeof token !== "string" ||
    typeof jwks !== "string"
  ) {
    throw new OidcError(`OIDC discovery document at ${url} is incomplete`);
  }

  const endpoints: OidcEndpoints = {
    authorizationEndpoint: toPublicUrl(authorization, cfg),
    tokenEndpoint: token,
    endSessionEndpoint:
      typeof doc.end_session_endpoint === "string"
        ? toPublicUrl(doc.end_session_endpoint, cfg)
        : null,
    jwksUri: jwks,
  };
  discoveryCache.set(cacheKey, { endpoints, fetchedAt: Date.now() });
  return endpoints;
}

// --- PKCE -------------------------------------------------------------

export function generateRandomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

export function codeChallengeS256(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

// --- Token endpoint ----------------------------------------------------

async function tokenRequest(
  tokenEndpoint: string,
  form: Record<string, string>,
): Promise<TokenResponse> {
  let res: Response;
  try {
    res = await fetch(tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(form).toString(),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new OidcError(`Token request failed: ${msg}`);
  }
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const detail =
      typeof body.error === "string"
        ? `${body.error}${typeof body.error_description === "string" ? `: ${body.error_description}` : ""}`
        : `HTTP ${res.status}`;
    throw new OidcError(`Token request failed: ${detail}`);
  }
  if (typeof body.access_token !== "string") {
    throw new OidcError("Token response is missing access_token");
  }
  return body as unknown as TokenResponse;
}

export async function exchangeCodeForTokens(
  cfg: AuthConfig,
  endpoints: OidcEndpoints,
  code: string,
  codeVerifier: string,
): Promise<TokenResponse> {
  return tokenRequest(endpoints.tokenEndpoint, {
    grant_type: "authorization_code",
    client_id: cfg.clientId,
    code,
    redirect_uri: cfg.redirectUri,
    code_verifier: codeVerifier,
  });
}

export async function refreshAccessToken(
  cfg: AuthConfig,
  endpoints: OidcEndpoints,
  refreshToken: string,
): Promise<TokenResponse> {
  return tokenRequest(endpoints.tokenEndpoint, {
    grant_type: "refresh_token",
    client_id: cfg.clientId,
    refresh_token: refreshToken,
  });
}

// --- ID-token verification ---------------------------------------------

const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function getJwks(jwksUri: string) {
  let jwks = jwksCache.get(jwksUri);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(jwksUri));
    jwksCache.set(jwksUri, jwks);
  }
  return jwks;
}

/** Verify the ID token against the issuer JWKS and check the login nonce. */
export async function verifyIdToken(
  cfg: AuthConfig,
  endpoints: OidcEndpoints,
  idToken: string,
  expectedNonce: string,
): Promise<void> {
  const { payload } = await jwtVerify(idToken, getJwks(endpoints.jwksUri), {
    issuer: cfg.issuer,
    audience: cfg.clientId,
  });
  if (payload.nonce !== expectedNonce) {
    throw new OidcError("ID token nonce mismatch");
  }
}

/**
 * Build session data from a token response. `expiresAt` comes from the
 * access token's `exp` claim (falling back to `expires_in`). On refresh,
 * tokens the IdP did not rotate are carried over from the previous session.
 */
export function tokenResponseToSession(
  tokens: TokenResponse,
  previous?: SessionData,
): SessionData {
  const now = Math.floor(Date.now() / 1000);
  let expiresAt: number | undefined;
  try {
    expiresAt = decodeJwt(tokens.access_token).exp;
  } catch {
    // opaque access token — fall back to expires_in
  }
  if (expiresAt === undefined) {
    expiresAt = now + (tokens.expires_in ?? 300);
  }
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? previous?.refreshToken ?? null,
    idToken: tokens.id_token ?? previous?.idToken ?? null,
    expiresAt,
  };
}
