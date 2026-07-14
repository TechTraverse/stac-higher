# ADR 0002 — stac-auth-proxy enforcement scope for Phase 1

Status: accepted (Phase 1) · Date: 2026-07-14

## Context

Phase 0 runs stac-auth-proxy in pass-through mode (`DEFAULT_PUBLIC=true`,
`PRIVATE_ENDPOINTS={}`) so nothing local requires a login. Phase 1 must start
enforcing the catalog plane (ROADMAP §7) while keeping a hard compatibility
requirement: dev, unit tests, and the existing e2e suite keep working without
authentication, and anonymous catalog reads through :8081 keep working.

ROADMAP §4 also flags that the proxy's transaction filtering is v1.0-era
(Feb 2026): "integration-test the write paths, don't assume them."

What the proxy offers (v1.x, verified against its configuration docs and
`record-level-auth` guide):

- **Route-level auth**: `DEFAULT_PUBLIC`, `PUBLIC_ENDPOINTS`,
  `PRIVATE_ENDPOINTS` (regex path → methods, optionally `[method, scope]`),
  JWT validation via OIDC discovery/JWKS, and `ALLOWED_JWT_AUDIENCES`
  (at least one value must match the token's `aud`). Its *default*
  `PRIVATE_ENDPOINTS` protects exactly the STAC Transaction endpoints
  (`POST /collections`, `PUT|PATCH|DELETE /collections/{id}`,
  `POST .../items`, `PUT|PATCH|DELETE .../items/{id}`, `POST .../bulk_items`).
- **Record-level auth**: CQL2 filter factories (`COLLECTIONS_FILTER_CLS`,
  `ITEMS_FILTER_CLS` + `_ARGS`/`_KWARGS`) that receive
  `{req, payload (JWT claims or empty for anonymous)}` and return CQL2. Read
  endpoints get the filter appended (DB-level filtering); single-resource
  reads validate the response; **writes are validated against the same
  filter** (POST body, PUT/PATCH existing+new, DELETE existing). Built-ins:
  `Template` (Jinja over claims → CQL2 text) and `Opa` (delegate to an Open
  Policy Agent sidecar). Requires the upstream to advertise the Filter
  Extension for the filtered resource; `CHECK_CONFORMANCE=true` (default)
  refuses to start otherwise.

## Decision

**1. Enforcement is opt-in via a compose override, not a default.**
`infra/compose.auth-enforced.yml` layered over the base file:

```sh
docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait
```

The base stack is byte-for-byte unchanged (pass-through, anonymous CRUD), so
every existing flow — dev server, unit tests, e2e — works with zero auth. An
override file (rather than `${VAR:-default}` interpolation) was chosen because
several of the proxy's settings cannot be set to a harmless empty string
(`PRIVATE_ENDPOINTS` must parse as JSON; `ALLOWED_JWT_AUDIENCES`/
`COLLECTIONS_FILTER_CLS` must be *absent*, not empty, to stay disabled), and
compose has no way to conditionally omit an environment key from one file.

**2. Enforced mode = authenticated transactions + audience check; reads stay
public.** `DEFAULT_PUBLIC` remains `true` this phase (visibility filtering is
additive, later). `PRIVATE_ENDPOINTS` is pinned **explicitly** to the proxy's
documented default transaction map instead of merely unsetting our `{}`
override — an upstream default change then can't silently widen or narrow
what we protect. `ALLOWED_JWT_AUDIENCES=stac-higher` with matching
`oidc-audience-mapper`s on the app and test clients, so validly-signed tokens
minted for other consumers are rejected.

**3. Test identities: a dedicated confidential client with direct-access
grants.** The realm gains alice (operator, /earth-observation), bob (member,
/weather), carol (admin) with committed passwords, and two confidential
clients (`stac-higher-test`, secret `stac-higher-test-secret`; a `-noaud`
twin without the audience mapper for negative tests). The proxy itself is
client-agnostic — it validates issuer/signature/audience via JWKS — so the
password grant only needs *a* client that permits it. We deliberately did
**not** enable direct grants on the public `stac-higher-app` client: a public
client + password grant is an unauthenticated token mint for anyone who can
reach Keycloak, and it would drift the browser client away from the
PKCE-only posture we want to keep testable. The test clients are local-dev
artifacts (secrets committed by design) and must never ship in a real
deployment's realm.

**4. Write-path integration tests live in `tests/integration/` (root),**
runner `node --test` (zero deps, Node ≥ 22 already required). They skip —
never fail — when :8180/:8081 are down or enforcement is off, so any agent or
CI job can invoke them unconditionally; only the enforced stack makes them
assert. Covered: anonymous write rejected, anonymous read allowed, operator
POST/PUT/DELETE round-trip, wrong-realm token rejected, wrong-audience token
rejected, and a test that *pins the known limitation* that member-role users
can also write (see below) so its removal is loud.

## What config alone cannot do (needs OPA, a custom filter factory, or upstream work)

- **Role-gated writes (member vs operator vs admin).** `PRIVATE_ENDPOINTS`
  can require an OAuth *scope* per endpoint, but Keycloak realm roles land in
  `realm_access.roles`, not `scope`; mapping roles into scopes is per-client
  contortion, and even then scopes are all-or-nothing per endpoint — no
  per-collection nuance. ROADMAP §7's role matrix therefore needs the OPA
  integration (`input.payload.realm_access.roles` in Rego) or a small custom
  filter-factory package. Until then, *any* valid stac-higher-audience token
  can write; the integration suite documents this by test.
- **Group-ownership visibility from `collection_settings`.** The proxy can
  only see the request and the token; it cannot consult
  `stac_higher.collection_settings` via config. Options, in preferred order:
  (a) OPA sidecar that queries/receives ownership data; (b) custom filter
  factory (Python package mounted into the proxy image) that reads Postgres;
  (c) mirroring ownership into collection documents (e.g. a
  `stac_higher:group` property) and filtering with the built-in `Template` —
  workable config-only, but makes catalog documents the authorization source
  of truth, which contradicts §5.5.
- **`Template`-filter hazards** (why the commented example in
  `infra/compose.auth-enforced.yml` stays off): interpolating the `groups`
  claim into CQL2-text is injection-shaped (upstream's own docs recommend
  CQL2-JSON from code instead); an empty groups list renders invalid CQL2
  (`IN ()`); and collections filtering requires the upstream to advertise
  Filter-Extension conformance on `/collections` (collection-search), which
  must be verified for our stac-fastapi-pgstac version before enabling or the
  proxy's conformance check fails at startup.

## Consequences

- Default local stack behavior is unchanged; nothing existing needs a login.
- The lead (or CI) flips enforcement with one extra `-f` flag; the exact set
  of protected endpoints is reviewable in-repo rather than implied by an
  image default.
- Realm edits (users/clients/mappers) only apply after
  `docker compose down -v` — the Keycloak volume persists imported realms.
- Phase 1's remaining catalog-plane work (role gating, group visibility) has
  a decided direction — OPA or a custom filter factory — without changing
  this topology; the enforcement override file is where that config will
  land.
- Committed test credentials are acceptable *only* because the realm file is
  a local-dev template; deployment realms must be generated per environment
  (flagged for the Phase 8 IaC work).
