/**
 * Canonical identity model — the ONLY identity shape the app consumes.
 *
 * IdP tokens (Keycloak, Cognito, Entra, Okta, …) are normalized into this
 * shape by the claims-mapping layer (`claims.ts`, ROADMAP §5.5). Nothing
 * outside `lib/auth/` may read raw token claims.
 *
 * RBAC seam (Phase 1 follow-on): permission middleware will consume
 * `locals.auth` (an `AuthContext`) as its single input. Keep this shape
 * stable — capabilities derive from `roles` + `groups` per ROADMAP §7.
 */

export const CANONICAL_ROLES = ["member", "operator", "admin"] as const;

export type CanonicalRole = (typeof CANONICAL_ROLES)[number];

export function isCanonicalRole(value: unknown): value is CanonicalRole {
  return (
    typeof value === "string" &&
    (CANONICAL_ROLES as readonly string[]).includes(value)
  );
}

export interface CanonicalIdentity {
  sub: string;
  email: string | null;
  name: string | null;
  groups: string[];
  roles: CanonicalRole[];
}

export type AuthMode = "oidc" | "bypass";

export type AuthContext =
  | { authenticated: true; mode: AuthMode; identity: CanonicalIdentity }
  | { authenticated: false; mode: AuthMode; identity: null };

export function anonymous(mode: AuthMode): AuthContext {
  return { authenticated: false, mode, identity: null };
}
