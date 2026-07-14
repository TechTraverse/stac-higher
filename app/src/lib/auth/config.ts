/**
 * Auth configuration & mode resolution.
 *
 * Modes:
 *   - `oidc`   — real login against the configured IdP (Keycloak by default).
 *   - `bypass` — env-gated static identity so local dev, unit tests, and the
 *     e2e suite work with zero IdP setup. Default in dev when no OIDC env is
 *     present. In production builds bypass is refused unless explicitly
 *     forced with `AUTH_BYPASS_FORCE=true` (loud warning either way).
 *
 * Env:
 *   AUTH_MODE           oidc | bypass (optional; resolved automatically)
 *   OIDC_ISSUER         default http://localhost:8180/realms/stac-higher
 *   OIDC_ISSUER_INTERNAL optional server-to-server issuer base (e.g.
 *                       http://keycloak:8080/realms/stac-higher when the app
 *                       itself runs inside compose; browser-facing endpoints
 *                       are rewritten back to OIDC_ISSUER)
 *   OIDC_CLIENT_ID      default stac-higher-app
 *   OIDC_REDIRECT_URI   default http://localhost:4321/api/auth/callback
 *   SESSION_SECRET      required for oidc login (any long random string)
 *   SESSION_MAX_AGE_S   session cookie lifetime, default 28800 (8 h)
 *   DEV_AUTH_IDENTITY   JSON partial CanonicalIdentity for bypass mode
 *   AUTH_BYPASS_FORCE   "true" to allow bypass in production (dangerous)
 */
import {
  isCanonicalRole,
  type AuthMode,
  type CanonicalIdentity,
  type CanonicalRole,
} from "./types";

export type Env = Record<string, string | undefined>;

export const DEFAULT_OIDC_ISSUER = "http://localhost:8180/realms/stac-higher";
export const DEFAULT_OIDC_CLIENT_ID = "stac-higher-app";
export const DEFAULT_OIDC_REDIRECT_URI =
  "http://localhost:4321/api/auth/callback";
export const DEFAULT_SESSION_MAX_AGE_S = 8 * 60 * 60;

export interface AuthConfig {
  mode: AuthMode;
  /** Browser-facing issuer (authorize / end_session redirects). */
  issuer: string;
  /** Issuer base used for server-to-server calls (discovery, token, JWKS). */
  internalIssuer: string;
  clientId: string;
  redirectUri: string;
  sessionSecret: string | null;
  sessionMaxAgeS: number;
  prod: boolean;
}

/** True when running a production build (or NODE_ENV=production). The e2e
 * suite runs the dev server and unit tests run under vitest, so both stay
 * non-production and keep the dev bypass available. */
export function isProdRuntime(): boolean {
  try {
    if (import.meta.env?.PROD === true) return true;
  } catch {
    // import.meta.env unavailable in some runtimes
  }
  return process.env.NODE_ENV === "production";
}

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

let warnedBypass = false;

/**
 * Resolve the effective auth mode. Explicit `AUTH_MODE` wins; otherwise any
 * OIDC-related env present (or a production runtime) selects `oidc`, and a
 * bare dev environment selects `bypass`.
 */
export function resolveAuthMode(env: Env = process.env, prod = isProdRuntime()): AuthMode {
  const explicit = env.AUTH_MODE;
  const oidcConfigured = Boolean(
    env.OIDC_ISSUER ||
      env.OIDC_CLIENT_ID ||
      env.OIDC_REDIRECT_URI ||
      env.SESSION_SECRET,
  );

  let mode: AuthMode;
  if (explicit === "oidc" || explicit === "bypass") {
    mode = explicit;
  } else {
    mode = oidcConfigured || prod ? "oidc" : "bypass";
  }

  if (mode === "bypass" && prod) {
    if (env.AUTH_BYPASS_FORCE === "true") {
      if (!warnedBypass) {
        warnedBypass = true;
        console.error(
          "[auth] *** AUTH BYPASS FORCED IN PRODUCTION (AUTH_BYPASS_FORCE=true) — every request runs as the static dev identity. NEVER use this outside an isolated environment. ***",
        );
      }
      return "bypass";
    }
    if (!warnedBypass) {
      warnedBypass = true;
      console.error(
        "[auth] AUTH_MODE=bypass is not allowed in production builds; falling back to oidc. Set AUTH_BYPASS_FORCE=true only if you fully understand the consequences.",
      );
    }
    return "oidc";
  }

  return mode;
}

export function getAuthConfig(env: Env = process.env, prod = isProdRuntime()): AuthConfig {
  const issuer = stripTrailingSlash(env.OIDC_ISSUER ?? DEFAULT_OIDC_ISSUER);
  return {
    mode: resolveAuthMode(env, prod),
    issuer,
    internalIssuer: stripTrailingSlash(env.OIDC_ISSUER_INTERNAL ?? issuer),
    clientId: env.OIDC_CLIENT_ID ?? DEFAULT_OIDC_CLIENT_ID,
    redirectUri: env.OIDC_REDIRECT_URI ?? DEFAULT_OIDC_REDIRECT_URI,
    sessionSecret: env.SESSION_SECRET ?? null,
    sessionMaxAgeS: Number(env.SESSION_MAX_AGE_S) || DEFAULT_SESSION_MAX_AGE_S,
    prod,
  };
}

/** Default bypass identity: an operator in the earth-observation group —
 * enough capability to exercise every dev flow without being an admin. */
export const DEFAULT_BYPASS_IDENTITY: CanonicalIdentity = {
  sub: "dev-user",
  email: "dev@stac-higher.local",
  name: "Dev Operator",
  groups: ["earth-observation"],
  roles: ["operator"],
};

/**
 * The static identity used in bypass mode. `DEV_AUTH_IDENTITY` (JSON) merges
 * over the default; unknown roles are dropped. Invalid JSON falls back to the
 * default with a warning.
 */
export function getBypassIdentity(env: Env = process.env): CanonicalIdentity {
  const raw = env.DEV_AUTH_IDENTITY;
  if (!raw) return DEFAULT_BYPASS_IDENTITY;
  try {
    const parsed = JSON.parse(raw) as Partial<CanonicalIdentity>;
    return {
      sub:
        typeof parsed.sub === "string" && parsed.sub
          ? parsed.sub
          : DEFAULT_BYPASS_IDENTITY.sub,
      email:
        typeof parsed.email === "string"
          ? parsed.email
          : DEFAULT_BYPASS_IDENTITY.email,
      name:
        typeof parsed.name === "string"
          ? parsed.name
          : DEFAULT_BYPASS_IDENTITY.name,
      groups: Array.isArray(parsed.groups)
        ? parsed.groups.filter((g): g is string => typeof g === "string")
        : DEFAULT_BYPASS_IDENTITY.groups,
      roles: Array.isArray(parsed.roles)
        ? (parsed.roles.filter(isCanonicalRole) as CanonicalRole[])
        : DEFAULT_BYPASS_IDENTITY.roles,
    };
  } catch {
    console.warn(
      "[auth] DEV_AUTH_IDENTITY is not valid JSON — using the default dev identity",
    );
    return DEFAULT_BYPASS_IDENTITY;
  }
}
