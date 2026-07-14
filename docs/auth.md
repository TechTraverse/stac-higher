# Authentication (Phase 1)

OIDC authorization-code + PKCE login in the Astro app, with a claims-mapping
layer that keeps the app IdP-agnostic and a dev-bypass mode that keeps local
dev, unit tests, and e2e working with zero IdP setup.

## Modes

| Mode | When | Behavior |
|---|---|---|
| `bypass` | Default in dev when no OIDC env is set, or `AUTH_MODE=bypass` | Every request resolves to a static dev identity (default: an `operator` in `earth-observation`). No IdP involved. Refused in production builds unless `AUTH_BYPASS_FORCE=true` (loud warning). |
| `oidc` | Any OIDC env present, `AUTH_MODE=oidc`, or any production build | Real login against the configured issuer (Keycloak in compose). Anonymous requests keep working — nothing is gated on login in Phase 1. |

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `AUTH_MODE` | auto | `oidc` \| `bypass` (see above) |
| `OIDC_ISSUER` | `http://localhost:8180/realms/stac-higher` | Browser-facing issuer |
| `OIDC_ISSUER_INTERNAL` | = `OIDC_ISSUER` | Server-to-server issuer base (e.g. `http://keycloak:8080/realms/stac-higher` when the app runs inside compose). Discovery/token/JWKS use it; authorize/end_session URLs are rewritten to the public issuer. |
| `OIDC_CLIENT_ID` | `stac-higher-app` | Public PKCE client |
| `OIDC_REDIRECT_URI` | `http://localhost:4321/api/auth/callback` | Registered redirect URI |
| `SESSION_SECRET` | — | Required for OIDC login. Any long random string; encrypts the session cookie (AES-256-GCM via SHA-256 of the secret). |
| `SESSION_MAX_AGE_S` | `28800` (8 h) | Absolute session-cookie lifetime (re-sealed on each token refresh) |
| `AUTH_CLAIMS_MAPPING` | — | Inline JSON claims-mapping config |
| `AUTH_CLAIMS_MAPPING_FILE` | — | Path to a JSON claims-mapping config |
| `DEV_AUTH_IDENTITY` | — | JSON partial identity for bypass mode, e.g. `{"name":"Weather Admin","groups":["weather"],"roles":["admin"]}` |
| `AUTH_BYPASS_FORCE` | — | `true` allows bypass in production builds. Dangerous; loudly logged. |

Local OIDC login against the compose Keycloak needs only:

```
SESSION_SECRET=<any long random string>
AUTH_MODE=oidc            # or set OIDC_ISSUER etc. explicitly
```

## Claims mapping (ROADMAP §5.5)

`app/src/lib/auth/claims.ts` maps arbitrary claim paths to the canonical
`{ sub, email, name, groups, roles }` model. The app only ever consumes the
mapped output. Config fields (all optional; Keycloak defaults shown):

```jsonc
{
  "sub": "sub",
  "email": "email",
  "name": ["name", "preferred_username", "email"], // first match wins
  "groups": "groups",                    // dot paths: "realm_access.roles"
  "roles": "realm_access.roles",
  "groupMap": { "<raw>": "<canonical>" }, // e.g. Entra GUIDs → names
  "roleMap": { "<raw>": "member|operator|admin" }
}
```

After `roleMap`, anything outside `member`/`operator`/`admin` is dropped.
Examples: Cognito → `{"groups":"cognito:groups","roles":"cognito:groups","roleMap":{"stac-operators":"operator"}}`;
Entra → `{"sub":"oid","email":"preferred_username","roles":"roles","roleMap":{"Operator":"operator"}}`.

## Session

Encrypted+authenticated JWE cookie (`sh_session`, httpOnly, SameSite=Lax,
`secure` on https) holding access/refresh/ID tokens — chunked across
`sh_session.N` cookies when large. Identity is derived per request from the
access token through the claims mapper; the middleware refreshes the access
token when it expires within 60 s and re-seals the cookie. Refresh failure
degrades to anonymous, never to an error page.

## Routes & request context

- `GET /api/auth/login?returnTo=/path` — redirect to the IdP (PKCE + state +
  nonce in a sealed 10-min `sh_oidc_txn` cookie)
- `GET /api/auth/callback` — code exchange, ID-token verification (issuer
  JWKS via `jose`), session cookie, redirect to `returnTo`
- `GET /api/auth/logout` — clears the session, RP-initiated logout at the IdP
- `GET /api/auth/me` — the canonical `AuthContext` (never tokens); consumed
  by the header's `UserMenu`

Every request gets `locals.auth: AuthContext` from `src/middleware.ts`:

```ts
type AuthContext =
  | { authenticated: true; mode: "oidc" | "bypass"; identity: CanonicalIdentity }
  | { authenticated: false; mode: "oidc" | "bypass"; identity: null };
```

**RBAC seam**: the Phase 1 follow-on permission middleware consumes
`locals.auth` exclusively (capabilities per ROADMAP §7). No enforcement
exists yet; no page or route is gated on login.
