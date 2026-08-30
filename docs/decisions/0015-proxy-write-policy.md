# ADR 0015 — Proxy write policy: custom filter factory enforcing `externally_writable`

- **Status:** accepted (2026-08-30 — implemented by slice P7-G: factory
  package `services/proxy-policy/`, derived image, image pins, enforced
  overlay, integration legs; proposed 2026-08-29 by the Phase 7 design spec
  `docs/superpowers/specs/2026-08-29-phase7-push-ingest-design.md` §5.
  The one-line live confirm on the pinned images remains P7-Z's)
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
(`services/proxy-policy/`, Python), shipped as a thin **derived image**
(`infra/proxy-policy/Dockerfile`: `FROM` the pinned upstream image +
`pip install psycopg` + the package — the upstream 1.2.0 wheel ships no
Postgres driver, so a bare volume mount cannot work) and configured **only in
the auth-enforced overlay**. Adopting it also **pins the `auth-proxy` and
`stac-fastapi-pgstac` images** (both `:latest` today): the factory builds
against an internal, undocumented context shape, so upstream drift must be a
deliberate bump. Per request the factory returns CQL2:

- **Reads:** unrestricted (`true`) — **including `POST /search`**, a read
  served by POST that a method-only branch would misclassify; the write
  branch keys on transaction-shaped paths, not method alone.
  Read-visibility remains I-1's problem; this factory is deliberately
  write-only policy — but it is the single seam where I-1's read filter
  later lands. Note the mechanical consequence either way: once an items
  filter is configured, enforced-mode reads pass through it (`filter=true&…`
  appended on GET items; single-item reads response-validated) — the
  integration suite gains read legs asserting reads stay unchanged.
- **Item writes (POST/PUT/PATCH/DELETE on transaction paths):**
  1. requests carrying the BFF shared-secret header (`X-BFF-Auth`, env
     `CATALOG_BFF_SHARED_SECRET` on app and proxy) are unrestricted —
     app-mediated writes are RBAC-gated and audited app-side (ADR 0008) and
     browser-session writes must reach every collection. This exemption is
     sound **only because the app holds bearer-identity writes on the BFF
     route to the same `externally_writable`/archived/group precondition
     set** (spec §4.3): the flag is enforced app-side for external callers
     brokered through the BFF, and proxy-side for callers going direct — no
     path skips it. Weakening the app-side check would turn this exemption
     into a bypass; the two are one decision. The secret is **mandatory in the enforced
     overlay**: both sides fail fast at startup when unset, the comparison
     is constant-time, and the header value is never logged — optional
     ("when configured") semantics would silently break every UI write to
     non-externally-writable collections on a misconfigured deployment;
  2. otherwise the token must carry `operator` or `admin` in its roles claim
     (claim path configurable, default `realm_access.roles`) — closing ADR
     0002's member-can-write pinned limitation — and the write is validated
     against `collection IN (<externally-writable set>)`, the set read from
     `collection_settings` over a read-only Postgres connection with a short
     TTL cache (~15 s). An empty set renders constant-false CQL2-JSON, never
     an invalid `IN ()`.
- **`bulk_items` is denied** for requests without the BFF header
  (source-verified hole: the default `items_filter_path` regex
  `^(/collections/([^/]+)/items(/[^/]+)?$|/search$)` — upstream
  `config.py:132` — does not match `POST /collections/{id}/bulk_items`, and
  `Cql2ValidateTransactionMiddleware` passes through when no filter was
  built, so with `ENABLE_TRANSACTIONS_EXTENSIONS=TRUE` any valid-audience
  member token could bulk-insert anywhere). The overlay overrides
  `ITEMS_FILTER_PATH` to also match `bulk_items` and the factory returns
  constant-false there — bulk push is unsupported in Phase 7 (deny, never
  parse the bulk body). If the validate middleware's rejection of a filtered
  bulk body proves 500-shaped, the fallback is blocking `bulk_items` via a
  PRIVATE_ENDPOINTS scope no token carries. Integration-tested either way.
- Collection-level transactions stay as ADR 0002 left them
  (token-required via PRIVATE_ENDPOINTS); external clients do not create
  collections.
- Pass-through mode (default compose) is unchanged: no factory, no policy,
  no login — dev/unit/e2e untouched.

**Feasibility — settled from fetched source** (stac_auth_proxy 1.2.0 wheel,
reviewed 2026-08-29; supersedes this ADR's original verify-first framing):

1. *Method/header branching:* `Cql2BuildFilterMiddleware.__call__` hands the
   factory `{"req": {"path", "method", "query_params", "path_params",
   "headers"}, **scope["state"]}` (`middleware/Cql2BuildFilterMiddleware.py:83-94`)
   — the method branch, `POST /search` carve-out, and `X-BFF-Auth` check are
   all directly implementable.
2. *Conformance check:* with `items_filter` set, startup requires
   ogcapi-features filter conformance classes
   (`Cql2BuildFilterMiddleware.__post_init__:52-61`,
   `lifespan.check_conformance`); stac-fastapi-pgstac 6.3.1 enables the
   Search/ItemCollection/CollectionSearch filter extensions by default
   (`stac_fastapi/pgstac/app.py:63,72,88`), and our compose sets no override.

The implementing slice keeps a **one-line live confirm** (start the enforced
stack on the pinned images; assert startup + one policy leg) against version
drift — not a fallback fork. The OPA sidecar is the growth path, not a
contingency.

**Relationship to the brokered write path (spec §4.3).** Enforcement and
brokering are complementary, not alternatives: the app's brokered push route
(pre-validation, prior-version snapshots, audit rows) is the documented
client contract, and this proxy policy is the enforcement floor that makes
`externally_writable` and the role matrix hold even for clients that bypass
the app. Neither substitutes for the other.

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
- **App-mediated push writes as a replacement for proxy policy** — rejected
  narrowly: a brokered route by itself enforces nothing (the proxy would
  still accept any valid token writing anywhere directly), making the flag
  advisory. Enforcement must live at the enforcement point. But brokering is
  **not** rejected as a write path: the Phase 7 spec (§4.3) ships an
  app-brokered push route as the documented client contract *on top of* this
  policy — synchronous rejections, prior-version snapshots, audit — after
  review pointed out the two are complementary.

## Consequences

- The enforced-mode proxy gains a **read-only dependency on the app
  database** (one table) and runs from a **repo-built derived image** rather
  than the upstream image directly (the upstream wheel has no Postgres
  driver). Locally that is one Dockerfile + connection string; cloud
  deployments must build/push the derived image and provision a read-only DB
  role for the proxy task (Phase 8 IaC note).
- The `auth-proxy` and `stac-fastapi-pgstac` images move from `:latest` to
  pins; upstream bumps become deliberate, tested changes (the factory
  consumes an internal, undocumented context shape).
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
