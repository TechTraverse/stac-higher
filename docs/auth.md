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
| `AUTH_BEARER_AUDIENCES` | `stac-higher` | Comma-separated `aud` values accepted on `Authorization: Bearer` JWTs hitting `/api/*` (see below). Mirrors the proxy's `ALLOWED_JWT_AUDIENCES`. |

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
  "name": ["name", "preferred_username", "email", "azp"], // first match wins
  "groups": "groups",                    // dot paths: "realm_access.roles"
  "roles": "realm_access.roles",
  "groupMap": { "<raw>": "<canonical>" }, // e.g. Entra GUIDs → names
  "roleMap": { "<raw>": "member|operator|admin" }
}
```

(`azp` is the last-resort display name for client-credentials tokens —
service accounts carry no profile claims, but `azp` names the OAuth client.)

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

## Bearer tokens on `/api/*` (Phase 7 push ingest)

External push clients are not browsers — no PKCE login, no session cookie —
but the push flow requires them to call the app (`POST /api/uploads` is the
only presign mint, ADR 0005). In `oidc` mode the middleware therefore also
accepts `Authorization: Bearer <JWT>` on `/api/*` requests
(`app/src/lib/auth/bearer.ts`):

- **Verification**: signature against the issuer JWKS (`jose`, JWKS URI from
  the same cached OIDC discovery the session path uses — never re-discovered
  per request), standard `exp` and `iss` checks (`iss` may be either
  `OIDC_ISSUER` or `OIDC_ISSUER_INTERNAL` — which one a server-to-server
  client sees depends on where it fetched its token), and `aud` must contain
  one of `AUTH_BEARER_AUDIENCES` (default `stac-higher`, mirroring the
  proxy's `ALLOWED_JWT_AUDIENCES`).
- **Claims mapping**: verified claims run through the same claims mapper as
  session identities, so `locals.auth` is a normal `CanonicalIdentity` —
  the permission guard, group scoping, and audit rows (`actor` = token
  `sub`) work unchanged.
- **Precedence**: a valid session cookie wins; the bearer path is tried only
  when no session identity resolved. Bypass mode is untouched (everything is
  already the static dev identity; bearer headers are ignored).
- **Degrade semantics**: an invalid/expired/wrong-audience bearer token
  degrades to **anonymous** — exactly the posture of a failed session
  refresh (warn in the log, never an error page). A gated route then 401s
  normally with `{ error, code: "unauthenticated" }`.
- When `locals.auth` came from a bearer token, the middleware also sets
  `locals.bearerToken` (the raw token) — the ADR 0008 BFF catalog route
  forwards it for bearer callers instead of a session token (Phase 7 §4.3).

Tokens come from the IdP's **client-credentials grant on a confidential
client** with the `stac-higher` audience mapper — deployments create their
own (see `docs/push-ingest.md` once P7-I lands). For local dev the realm file
ships a `stac-higher-push` client (secret `stac-higher-push-secret`; its
service account is an operator in `earth-observation`) — a dev-only artifact
with the same never-deploy caveat as the ADR 0002 test clients. Realm-file
edits only take effect after `docker compose down -v`:

```
curl -s http://localhost:8180/realms/stac-higher/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=stac-higher-push \
  -d client_secret=stac-higher-push-secret
```

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

## BFF for built-in-catalog writes (ADR 0008)

The access token never reaches page JavaScript, so under auth enforcement the
browser cannot call the proxy's transaction endpoints itself. Built-in-catalog
mutations from the UI therefore go through `POST/PUT/PATCH/DELETE
/api/catalog/[...path]` (transaction endpoints only): the route reads the
session server-side, injects `Authorization: Bearer <access token>`, and
forwards to `BUILTIN_CATALOG_URL` (falls back to
`PUBLIC_BUILTIN_CATALOG_URL`, then `http://localhost:8081`). In dev-bypass
mode there is no token and none is attached — the pass-through proxy accepts
the write, so both postures share one code path. These routes are gated
(operator+) and audited like every other mutation. `stacFetch` routes
built-in-catalog writes here unconditionally; reads and external catalogs are
untouched.

## RBAC & audit (ROADMAP §7, §5.5)

The permission guard (`src/lib/authz/guard.ts`, called from
`src/middleware.ts`) consumes `locals.auth` exclusively:

- **Reads stay open** — no existing page or catalog read route is gated on
  login. (The platform surfaces — connections, associations, settings,
  alerts, channels, audit — require authentication and are group-scoped
  in-route.)
- **Gated API mutations** require the `operator` or `admin` role. The
  gated-route table (`src/lib/authz/permissions.ts`) now spans the whole
  platform surface: extensions CRUD, connections CRUD + `test` +
  host-key reset, ingest/deliver associations (+ `backfill`, `redeliver`),
  notification channels CRUD, alert `ack`/`resolve`, collection settings
  PUT, presigned-upload minting (`POST /api/uploads`), and the ADR 0008
  catalog BFF (`/api/catalog/[...path]` transaction writes). The audited
  action enum is `create | update | delete | test | backfill | redeliver |
  ack | resolve`. Anonymous → `401`, insufficient role → `403`, both with
  the JSON shape `{ error, code }`
  (`code: "unauthenticated" | "forbidden"`). GROUP ownership (operators act
  only within their own groups) needs the row, so it is enforced inside the
  routes, not the guard. Deliberately ungated: `POST /api/alerts/read`
  (personal read-watermark, member+).
- **Every gated mutation lands one `stac_higher.audit_log` row** (allowed or
  denied), as do OIDC login/logout. The table is append-only (DB triggers
  reject UPDATE/DELETE/TRUNCATE) and monthly-partitioned since migration 018
  (ADR 0012 — rows die only by partition DETACH+DROP); `detail` is redacted
  of credential-shaped keys/values before insert (`src/lib/audit/log.ts`)
  and audit failures log loudly but never fail the audited request.
- `GET /api/audit?limit&before` — the paginated audit viewer (M2 exposes it
  on `/monitoring`-adjacent surfaces; also consumable directly).
  Operators see rows whose `actor_groups` overlap their own groups; admins
  see everything; members/anonymous are rejected.

Compatibility: the dev-bypass identity defaults to an **operator**, so local
dev, unit tests, and the e2e suite keep passing without an IdP or login.
Collection ownership defaults for pre-existing collections:
`docs/decisions/0003-preexisting-collections.md`.
