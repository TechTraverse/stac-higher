# ADR 0015 — Proxy write policy: custom filter factory enforcing `externally_writable`

- **Status:** proposed (2026-08-29, by the Phase 7 design spec
  `docs/superpowers/specs/2026-08-29-phase7-push-ingest-design.md` §5;
  accept/revise with that spec's review)
- **Owners:** proxy policy (`services/proxy-policy/`, new) + auth-enforced
  overlay (`infra/compose.auth-enforced.yml`) + BFF route (app)
- **Related:** ADR 0002 (enforcement scope; "what config alone cannot do"),
  ADR 0008 (BFF for browser writes), ROADMAP §6.2 / §7; ISSUES I-1

## Context

`collection_settings.externally_writable` has existed since migration 003 and
has never gated anything. Phase 7's push-ingest flow (§6.2) requires it to
gate catalog transactions **at stac-auth-proxy** — the enforcement point for
non-browser clients (ROADMAP §7's plane split) — while the ADR 0008 BFF path,
which forwards browser writes through the same proxy with a session token,
must keep writing to *any* collection the UI manages.

ADR 0002 already established the constraint space: proxy config alone cannot
consult `stac_higher.collection_settings`, and role-gating writes is not
config-expressible for Keycloak realm roles (today any valid
`stac-higher`-audience token can write anywhere — a limitation pinned by an
integration test). Its named escape hatches, in preferred order: (a) an OPA
sidecar, (b) a custom filter factory reading Postgres, (c) mirroring
authorization data into catalog documents.

## Decision

**Option (b): a small in-repo custom items filter factory**
(`services/proxy-policy/`, Python), mounted into the proxy container and
configured **only in the auth-enforced overlay**. Per request it returns
CQL2:

- **Reads:** unrestricted (`true`). Read-visibility remains I-1's problem;
  this factory is deliberately write-only policy — but it is the single seam
  where I-1's read filter later lands.
- **Item writes (POST/PUT/PATCH/DELETE):**
  1. requests carrying the BFF shared-secret header (`X-BFF-Auth`, env
     `CATALOG_BFF_SHARED_SECRET` on app and proxy) are unrestricted —
     app-mediated writes are RBAC-gated and audited app-side (ADR 0008) and
     must reach every collection;
  2. otherwise the token must carry `operator` or `admin` in its roles claim
     (claim path configurable, default `realm_access.roles`) — closing ADR
     0002's member-can-write pinned limitation — and the write is validated
     against `collection IN (<externally-writable set>)`, the set read from
     `collection_settings` over a read-only Postgres connection with a short
     TTL cache (~15 s). An empty set renders constant-false CQL2-JSON, never
     an invalid `IN ()`.
- Collection-level transactions stay as ADR 0002 left them
  (token-required via PRIVATE_ENDPOINTS); external clients do not create
  collections.
- Pass-through mode (default compose) is unchanged: no factory, no policy,
  no login — dev/unit/e2e untouched.

**Verify-first preconditions** (the proxy's write-path filtering is
v1.0-era — "test, don't assume", ROADMAP §4): the implementing slice must
probe, before building on them, that (1) the factory can branch on the HTTP
method from the request it receives, and (2) `CHECK_CONFORMANCE` does not
refuse an items-filter configuration our stac-fastapi-pgstac's conformance
cannot satisfy (or disabling it in the overlay is an acceptable, documented
risk). If either fails, fall back to the OPA sidecar carrying the same policy
— the policy *semantics* above are the decision; the factory is the preferred
vehicle.

## Alternatives considered

- **OPA sidecar** (ADR 0002's option (a)) — still needs the
  collection-settings data delivered to it, plus a new always-on service and
  a Rego surface, to evaluate one boolean and a role list. Recorded as the
  fallback and the growth path when policy outgrows a static factory.
- **Mirror `externally_writable` into collection documents + `Template`
  filter** — item-write validation checks the *item* body against the
  filter and cannot join to the collection document; and it would make
  catalog documents an authorization source of truth, contradicting §5.5.
  Rejected.
- **App-mediated push writes** (external clients POST items through an app
  route that forwards, BFF-style) — reuse is attractive and the app is
  already in the push loop (the uploads mint), but it enforces nothing: the
  proxy would still accept any valid token writing anywhere directly, making
  the flag advisory. Enforcement must live at the enforcement point.
  Rejected.

## Consequences

- The enforced-mode proxy gains a **read-only dependency on the app
  database** (one table). Locally that is a mount + connection string; cloud
  deployments must provision a read-only DB role for the proxy task (Phase 8
  IaC note).
- Role mapping now exists in two places (the app's claims mapper; the
  factory's claim-path env). Deployments with exotic claims configure both;
  convergence is an OPA-era concern. Logged in ISSUES at merge.
- The BFF exemption rides a shared secret between two of our own services —
  the same trust shape as `PROXY_AUTH_TOKEN`. The header must never be
  forwardable from outside (the proxy strips/ignores it in pass-through
  mode; enforced-mode deployments keep the secret out of client reach by
  construction — it exists only in app/proxy env).
- ADR 0002's pinned member-can-write integration test flips from
  "documents the limitation" to "asserts the policy" — its removal was
  designed to be loud; this is the intended loud moment.
- The Settings tab's `externally_writable` toggle becomes live policy with
  up to one cache-TTL of lag (~15 s), which the Settings UI copy should
  mention when the toggle ships its enforcement note.

## Revisit

- When I-1 (read-visibility) is implemented: extend this factory (or the OPA
  fallback) rather than adding a second policy point.
- When a deployment needs per-collection *write* policy richer than one
  boolean + role floor (per-group write grants, scopes), move the policy to
  OPA and keep the factory as the delivery vehicle only.
