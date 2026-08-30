# Phase 7 — Direct interaction (push ingest) — design

- **Date:** 2026-08-29 · **Status:** draft (P7-A; provisional approval recorded in
  `TODO.md` after adversarial review)
- **Scope sources:** ROADMAP §9 Phase 7 (done-when), §6.2 (push flow), §6.4
  (finalize gating), §5 (`collection_settings.externally_writable`, §5.3 layout),
  §6.5 (GC); ADR 0014 ("Finalize seam sketch" — the P9-B obligation), ADR 0001 /
  0002 / 0004 / 0005 / 0008 / 0011; the P9 scoping notes
  (`2026-08-29-phase9-scoping-notes.md`); ISSUES I-13/I-14/I-15/I-17;
  `tests/contract-fixtures/README.md`.
- **New ADR:** [ADR 0015 — proxy write policy via a custom filter factory]
  (../../decisions/0015-proxy-write-policy.md) (proposed; accepted/revised by
  this spec's review).

---

## 1. Gate

ROADMAP Phase 7 done-when, verbatim: *an external client with a token can
upload a file and POST an item, the item finalizes into canonical storage, and
delivery fires from it like any other item.* The live proof is the existing
**P7-Z** gate task in `TODO.md` (auth-enforced stack, real token, presigned
PUT, POST → finalize → delivery, finalize-failure alert, full e2e) — this spec
references P7-Z and does not redefine it.

Two structural obligations sit on top of the done-when:

1. **The ADR 0014 seam.** The finalize slice must accept a
   `FinalizeRequest` naming a different producer and staging prefix **without
   code changes** — push-ingest is merely the first caller; Phase 9 process
   runs are the second. §6 is written against that check criterion.
2. **The BFF keeps working.** Whatever gates external writes at
   stac-auth-proxy must not break the ADR 0008 browser write path, which
   flows through the same proxy with a session token.

## 2. What Phase 7 inherits

Every hard piece already has a seam waiting:

| Existing piece | Where | What Phase 7 does with it |
|---|---|---|
| `collection_settings.externally_writable` | migration 003, Settings tab PUT (M2-E) | starts enforcing it (it has never gated anything) |
| Staging key layout + TTL sweep | `stagingKey` in `app/src/lib/storage/keys.ts` (§5.3 `staging/{upload_id}/{filename}`); `pipeline/jobs/staging_cleanup.py` (hourly, `STAGING_TTL_SECONDS`) | staged uploads land here; abandoned/rejected staging is already swept |
| Presign mint | `POST /api/uploads` (`app/src/pages/api/uploads/index.ts`) — canonical-only today (ADR 0005) | gains a staged mode for untrusted external bytes |
| Outbox + dispatcher | `pipeline/dispatcher/` — the loop's header comment (`loop.py:10-13`) marks the §6.4 finalize-gating seam explicitly | staged-item events route to finalize instead of delivery |
| BFF catalog route (ADR 0008) | `app/src/pages/api/catalog/[...path].ts` — session-token injection, guard + audit, archived-409, GC marks on delete | grows bearer-caller forwarding + push pre-validation (§4.3) — the **documented** push write path |
| stac-pydantic validation | `validate_item` in `pipeline/ingest/itemize.py` (the ITEMIZE gate) | reused verbatim as the finalize gate |
| pypgstac upsert | `pipeline/stac/pgstac_writer.py` | finalize's upsert step |
| Platform storage client | `pipeline/storage/platform.py` (`put/get/head_object`, `delete_prefix`), `pipeline/storage/keys.py` | staging→canonical move |
| Mark-first GC queue | `stac_higher.asset_gc` (ADR 0011), idempotent open-key marks | external deletes join the marked-then-collected path (§7.3) |
| Alerts + monitor | `pipeline/flow/monitor.py` `MONITOR_KINDS`, dedup index (migration 014) | gains a `push_rejected` kind (§8) |
| Central metrics | `pipeline/metrics.py` `instrument_handler` at registration | finalize job auto-instrumented; per-item counters added (§9) |
| Auth-enforced overlay | `infra/compose.auth-enforced.yml` (ADR 0002) | the per-collection write policy lands there (§5) |
| Contract fixtures | `tests/contract-fixtures/` (README rule) | three new/pulled-forward fixtures (§10) |

## 3. Auth for non-browser clients: bearer tokens on `/api/*`

**Problem.** External push clients are not browsers: no PKCE login, no session
cookie. But the push flow *requires* them to call the app —
`POST /api/uploads` is the only presign mint (ADR 0005: presigning is offline,
the app never streams bytes), and `locals.auth` today comes only from the
session cookie or dev-bypass. Without this, the staged-upload contract is
unreachable for exactly the clients it exists for.

**Decision.** The auth middleware (`app/src/lib/auth/` + `src/middleware.ts`)
accepts `Authorization: Bearer <JWT>` on API routes in `oidc` mode:

- Validated with `jose` (already a dependency) against the issuer's JWKS
  (`OIDC_ISSUER_INTERNAL` discovery — same config the session path uses),
  `aud` must contain `stac-higher` (mirroring `ALLOWED_JWT_AUDIENCES`), plus
  standard `exp`/`iss` checks. JWKS fetched through the existing discovery
  cache, not per-request.
- Claims run through the **same claims mapper** (`claims.ts`) as session
  identities → `locals.auth` is a normal `CanonicalIdentity` with mapped
  groups/roles. Every downstream consumer — the permission guard, group
  scoping, audit rows (`actor` = token `sub`) — works unchanged.
- **Precedence:** a valid session cookie wins; the bearer path is tried only
  when no session is present. Bypass mode is unchanged (everything is already
  the static operator, so unit tests and e2e need nothing).
- An invalid/expired bearer token degrades to anonymous exactly like a failed
  session refresh (`docs/auth.md` posture: degrade, never error-page) — the
  gated route then 401s normally.

*(Review-verified against the code, 2026-08-29: `jose` is already in use, the
discovery cache at `auth/oidc.ts:44-61` is reusable, the claims mapper
consumes access-token claims, degrade-to-anonymous matches the session
posture, and CSRF is no obstacle for header-carried credentials.)*

**Alternatives rejected.** (a) A separate API-key system — a second credential
plane to audit and rotate, when the IdP already mints exactly the right
credential (client-credentials grant on a confidential client) and the proxy
already validates the same tokens. (b) Requiring push clients to talk only to
the proxy — impossible; the presign mint is app-side by design (ADR 0005).

**Deployment note (honest):** the local realm has only the ADR 0002 *test*
clients, which must never ship. `docs/push-ingest.md` (§12) documents the
requirement — a deployment-owned confidential client with the
`stac-higher` audience mapper and client-credentials grant. For local dev we
add a committed `stac-higher-push` client to the realm file mirroring the test
clients (dev-only artifact, same caveat as ADR 0002 §3; realm edits need
`docker compose down -v`).

## 4. Staged-upload contract

### 4.1 The mint (`POST /api/uploads`, staged mode)

The existing route keeps its canonical mode untouched (body with `collection`
+ `item` + `files` → canonical presigns; the trusted UI path, ADR 0005). A
body **without `item`** selects staged mode:

```jsonc
// POST /api/uploads     (Authorization: Bearer …, operator+)
{ "collection": "sentinel-pushed", "files": [{ "filename": "B04.tif", "contentType": "image/tiff" }] }
// 200 →
{
  "upload_id": "0d9c…",                    // uuid, server-generated
  "uploads": [{
    "filename": "B04.tif",
    "url": "https://…presigned PUT…",       // staging/{upload_id}/B04.tif
    "staged_href": "staging://0d9c…/B04.tif" // goes in the item's asset href, verbatim
  }],
  "expires_at": "…"                         // ledger created_at + STAGING_TTL — THE clock, see below
}
```

**One governing clock.** Staging expiry is governed by the ledger row's age
(`staged_uploads.created_at + STAGING_TTL_SECONDS`), everywhere: `expires_at`
in the mint response is that instant; the finalize sweep (§6.4) flips
`pending` rows past it to `expired`; and the byte-level TTL sweep
(`staging_cleanup.py`) is amended to **skip prefixes whose ledger row is
non-terminal and younger than the TTL** (prefixes with no ledger row — legacy
debris, future Phase 9 runs — keep the object-mtime rule). Without this, three
clocks (mint promise, object mtime, row age) could each expire a session at a
different moment; the client-visible window is now a single documented number.

Preconditions enforced at mint (fail fast, before any bytes move):

- caller is operator+ (existing guard; the route is already in the gated
  table) **and** in the collection's owning group per the ADR 0003/M2-E
  rules (unowned → any operator);
- `collection_settings.externally_writable = true` — a missing settings row
  defaults to `false`, so a collection must be explicitly flagged in the
  Settings tab before it accepts push (this also sidesteps I-17 for the push
  path: an unknown collection id has no settings row and is refused);
- not `archived` (ADR 0011: archived collections take no item writes) — 409.

The mint inserts one **`stac_higher.staged_uploads`** ledger row (migration
020, §11): the binding `{upload_id, collection_id, created_by, group_id,
filenames, status: 'pending'}`. This row is (a) the authorization binding
finalize checks — staged refs are only honored for the collection their
upload session was minted for, so one tenant cannot reference another's
staging prefix — and (b) where the client sees the async outcome (§6.4).

### 4.2 Referencing staged assets in the POSTed item

The client PUTs bytes to the presigned URLs, then POSTs the item (§4.3 for
*where*) with each platform-hosted asset's `href` set to the `staged_href`
**verbatim**:

```
staging://{upload_id}/{filename}
```

The grammar is deliberately unmistakable: no real scheme collides with it, the
dispatcher detects it with a prefix check, and the finalize step parses
`upload_id`/`filename` from it with the same segment validation as the key
builders. It is a cross-runtime contract → golden fixture (§10).

**Contract rules** (documented in `docs/push-ingest.md`; enforced
synchronously on the brokered path (§4.3) and again at finalize):

- **strictly one upload session per item, both directions**: an `upload_id`
  already bound to a different `item_id` is a rejection, and an item whose
  staged hrefs reference **more than one** session is a rejection (reason
  `multi_session`) — so the ledger claim (§6.4) is always singular and needs
  no multi-row semantics;
- every `staging://` href in an item must resolve to a `staged_uploads` row
  minted for that item's collection;
- the item must carry its top-level `collection` field (without it the proxy
  policy's CQL2 match fails closed → 403; the brokered path 400s with a
  useful message);
- assets with ordinary external hrefs (`https://…`) are allowed and pass
  through untouched — metadata-only or mixed pushes are legal; such items
  have nothing to finalize and deliver immediately (§7);
- multiple staged hrefs from the same session = a multi-asset item; fine.

`GET /api/uploads/{uploadId}` (new route; authenticated, creator's group or
admin) polls the ledger row — the ADR 0004 request-table poll shape, reused.

### 4.3 Where the item write goes: brokered by default, direct as fallback

The adversarial review made the right structural point: **proxy enforcement
and app brokering are not mutually exclusive** — and an app-mediated write
path turns most async rejections into synchronous 4xxs. Phase 7 therefore
ships **both**, with distinct jobs:

1. **Brokered path (the documented default).** External clients POST/PUT
   items to the existing ADR 0008 BFF route —
   `/api/catalog/collections/{id}/items[/{itemId}]` — which P7-D extends for
   bearer callers: when `locals.auth` came from a bearer token (§3), the
   route forwards the **caller's own** bearer token instead of a session
   token (the proxy remains the token-enforcement point either way).

   **Every bearer-identity write on this route** — item POST/PUT/PATCH/
   DELETE, staged hrefs or not — is first held to the §4.1 precondition set:
   `externally_writable = true` (missing settings row = `false`), not
   `archived`, and the ADR 0003/M2-E group rule. Without this, the
   `X-BFF-Auth` exemption (§5.2) would let any bearer operator push
   metadata-only items or deletes into *any* collection through the
   brokered route — inverting `externally_writable` on the documented
   default path (review residual R1). **Session-cookie callers are
   untouched**: the browser UI keeps writing to every collection it manages,
   preserving ADR 0008's obligation. The split is on how the identity
   arrived (§3's precedence makes that unambiguous), not on who it is.

   Before forwarding a body containing `staging://` hrefs, the route
   additionally **pre-validates synchronously**:
   - staged-href grammar, single-session rule, session exists / not
     terminal / minted for this collection / not bound to another item
     (the §4.2 rules — a DB lookup, cheap and decisive);
   - top-level `collection` present and matching the path;
   - a light structural check (type/id/geometry/properties presence — Zod).
     Deliberately **not** the full STAC gate: stac-pydantic at finalize
     remains the single authoritative validator (§6.2); duplicating it in
     TypeScript would fork the gate.
   Failures return 4xx **before anything reaches pgstac** — nothing is
   created, nothing is destroyed, the client sees the reason immediately.
   Additionally, on a **PUT to an existing item**, the route snapshots the
   current stored item into the session's ledger row
   (`staged_uploads.prior_item`, migration 020) — the restore point §6.3
   uses — under two guards (review residual R2): **first write wins** (a
   second brokered PUT in the same session never overwrites an existing
   snapshot, so a rejection cannot "restore" the first PUT's own staged
   document), and **a document containing `staging://` hrefs is never
   snapshotted** (a staged-href document is by definition not a restorable
   good version). A **PATCH whose merge would involve `staging://` hrefs is
   rejected on the brokered path — staged-asset updates require PUT**
   (residual R3: PATCH merge semantics would make both the snapshot and the
   finalize target ambiguous); metadata-only PATCHes pass through with the
   precondition set above, nothing more. Brokered writes inherit the BFF's
   guard, audit rows, and archived-409 for free (this closes most of the
   push audit gap — §13). The route stays thin in ADR 0008's sense:
   preconditions + pre-validation + snapshot + forwarding; STAC semantics
   still live in the catalog plane and finalize.
2. **Direct path (supported, discouraged).** POSTing straight to the proxy
   (`:8081`) with a valid token remains possible — ROADMAP §6.2's original
   picture — and is what the §5 policy exists to gate. Direct writes get no
   pre-validation, no snapshot, no audit row; their staged items flow
   through the same async finalize with the §6.3 direct-path rejection
   semantics. `docs/push-ingest.md` documents the brokered path as the
   supported contract and the direct path's sharper edges explicitly.

What the brokered path *cannot* catch stays async by nature: missing staged
bytes, checksum mismatches, and deep stac-pydantic failures beyond the light
check. §6.3 defines those outcomes.

## 5. Proxy write policy for `externally_writable`

### 5.1 The problem, honestly

ADR 0002 pinned two facts: (1) the proxy's config alone cannot consult
`stac_higher.collection_settings`, and (2) role-gating writes is not
config-expressible for Keycloak realm roles — today **any** valid
`stac-higher`-audience token can write anywhere, and an integration test pins
that limitation loudly. Meanwhile the BFF (ADR 0008) forwards browser writes
through the same proxy and must keep writing to *any* collection the UI
manages — `externally_writable` gates **external** clients, not the app.

### 5.2 Decision (ADR 0015, proposed)

**A custom items filter factory** — ADR 0002's named option (b): a small
in-repo Python package (`services/proxy-policy/`), shipped as a **thin
derived image** (`infra/proxy-policy/Dockerfile`: `FROM` the pinned upstream
image + `pip install psycopg` + the package — the upstream 1.2.0 wheel ships
no Postgres driver, so a bare volume mount cannot work), enabled **only in
the auth-enforced overlay**. Landing it also **pins** the `auth-proxy` and
`stac-fastapi-pgstac` images (both `:latest` today, `docker-compose.yml:43,86`)
— the factory builds against an internal, undocumented context shape, so
version drift must be deliberate. Per request the factory receives
`{req, payload}` and returns CQL2:

- **Read methods → no restriction** (`true`) — **including `POST /search`**,
  which is a read served by POST and must be carved out explicitly (a
  method-only branch would apply write policy to every search). Path-based
  discrimination: the write branch applies only to transaction-shaped paths.
  Read-visibility remains I-1; this factory deliberately does not filter
  reads (but it is the seam where I-1's filter will later land — one policy
  point, not two). NOTE the shape change anyway: once *any* items filter is
  configured, enforced-mode reads pass through it (`filter=true&…` appended
  on GET items; single-item reads response-validated) — the integration
  suite gains read legs asserting reads are unchanged.
- **Write methods (POST/PUT/PATCH/DELETE on transaction paths):**
  - requests carrying the BFF's shared-secret header (`X-BFF-Auth`, env
    `CATALOG_BFF_SHARED_SECRET` on both app and proxy) → no restriction —
    app-mediated writes are RBAC-gated and audited app-side and must reach
    any collection. The secret is **mandatory in the enforced overlay**:
    both sides fail fast at startup if unset, the comparison is
    constant-time, and the header value is never logged — "when configured"
    semantics would silently break every UI write to
    non-externally-writable collections on a misconfigured deployment;
  - otherwise the token's roles (claim path env, default
    `realm_access.roles`) must include `operator` or `admin` — closing ADR
    0002's member-can-write limitation for the catalog plane — and the write
    is validated against `collection IN (<externally-writable set>)`, the
    set read from `collection_settings` over a read-only Postgres connection
    with a short TTL cache (~15 s; a Settings flip takes effect within it).
    An **empty** set renders a constant-false CQL2-JSON expression, never
    `IN ()` (the ADR 0002 Template hazard).
- **`bulk_items` is closed off** (review finding, source-verified): the
  proxy's default `items_filter_path` regex does not match
  `POST /collections/{id}/bulk_items`, and the validate middleware passes
  requests through when no filter was built — so without action, any
  valid-audience **member** token could bulk-insert anywhere. The overlay
  overrides `ITEMS_FILTER_PATH` to also match `bulk_items`, and the factory
  returns constant-false for bulk requests without the BFF header — **bulk
  push is unsupported in Phase 7** (deny, not validate; the factory never
  parses the bulk body shape). An integration leg asserts the denial. If the
  validate middleware's rejection of a filtered bulk body proves ugly
  (500-shaped instead of 4xx), the fallback is blocking `bulk_items` via a
  PRIVATE_ENDPOINTS scope no token carries — same effect, uglier config.
- Collection-level transactions (`POST /collections` etc.) are untouched:
  they stay PRIVATE_ENDPOINTS-token-gated; external clients do not create
  collections (that is UI/BFF work), and role-gating them can ride the same
  factory later if wanted.

**Verify-first — both probes settled favorably from fetched source**
(stac_auth_proxy 1.2.0 wheel, reviewed 2026-08-29):

1. *Can the factory branch on method/headers?* **Yes** —
   `Cql2BuildFilterMiddleware.__call__` passes the factory
   `{"req": {"path", "method", "query_params", "path_params", "headers"},
   **scope["state"]}` (`middleware/Cql2BuildFilterMiddleware.py:83-94`).
   The method branch, the `POST /search` carve-out, and the `X-BFF-Auth`
   check are all directly implementable.
2. *Does `CHECK_CONFORMANCE` tolerate an items filter?* **Almost certainly**
   — with `items_filter` set the proxy requires ogcapi-features filter
   conformance classes at startup (`Cql2BuildFilterMiddleware.__post_init__:52-61`,
   `lifespan.check_conformance`), and stac-fastapi-pgstac 6.3.1 enables
   SearchFilterExtension / ItemCollectionFilterExtension /
   CollectionSearchFilterExtension by default (`stac_fastapi/pgstac/app.py:63,72,88`;
   our compose sets no override).

The residual risk is version drift, not design: P7-G keeps a **one-line live
confirm** (start the enforced stack against the pinned images, assert
startup + one policy leg) as a slice step — no fallback fork. The OPA sidecar
remains the *growth path* (§5.3), not a contingency. The integration suite
(`tests/integration/`) grows the policy legs regardless (§15) — P7-Z proves
it live.

### 5.3 Alternatives rejected

- **OPA sidecar** (ADR 0002's preferred order (a)): still needs the
  collection-settings data pushed or queried, plus a whole new service and a
  Rego surface, to evaluate one boolean and a role list. Kept as the fallback
  if the filter-factory probes fail, and as the growth path if policy ever
  outgrows this.
- **Mirror the flag into collection documents + `Template`:** item write
  validation checks the *item* body against CQL2 — it cannot join to the
  collection document — and it would make catalog documents an authorization
  source of truth (contradicts §5.5).
- **App-mediated push writes as a REPLACEMENT for proxy policy** — rejected,
  but narrowly: a brokered route by itself enforces nothing (the proxy would
  still accept any valid token writing anywhere directly), so the flag must
  bind at the enforcement point. The first draft of this spec over-rotated
  and rejected brokering wholesale; the review correctly pointed out that
  enforcement and brokering are **complementary**, and §4.3 now ships the
  brokered path as the documented client contract (synchronous rejections,
  snapshots, audit) with this proxy policy as the enforcement floor beneath
  it. ADR 0008's revisit note about brokering push under the BFF family is
  satisfied literally.

### 5.4 Modes

Pass-through mode (default compose) is byte-for-byte unchanged — no factory,
no policy, dev/e2e untouched. App-side staged-mint checks (§4.1) still apply
in every mode, so the dev-loop behavior matches enforced behavior on the
cooperative path.

## 6. Finalize

### 6.1 The seam (ADR 0014 — verbatim obligation)

One pipeline module, `pipeline/finalize/`, exposing:

```
finalize(req: FinalizeRequest) -> FinalizeResult

FinalizeRequest:
  producer:            "push_ingest" | "process_run"    # extensible enum
  staging_prefix:      str      # push: staging/{upload_id}/ ; P9: staging/runs/{run_id}/
  output_collections:  [str]    # the ONLY collections upsert may touch
  items:               [ItemRef]
  provenance:          dict     # producer-specific: {upload_id, event_op} | {run_id}

ItemRef (ref kind, orthogonal to producer):
  {kind: "pgstac", collection_id, item_id}   # item already in the catalog (push)
  {kind: "staged", key}                      # item JSON document in staging (P9)

FinalizeResult:
  upserted: [{collection_id, item_id}]
  rejected: [{item_ref, reason}]
```

The steps — **platform pre-flight → validate → checksum → move
staging→canonical → rewrite hrefs → upsert (restricted to
`output_collections`) → ordinary outbox events** — contain **no producer
branching**. Producer differences live only in the request (where staging is,
which ledger row records the outcome, which collections are writable) plus
two thin per-producer layers registered against the `producer` value,
**outside the steps**: a *resolver* (load the item for each `ItemRef` AND
perform producer-specific admission — for push, the §4.2 session
binding/one-session rule against `staged_uploads`; for P9, run-ledger checks)
and an *outcome recorder* (push: stamp `staged_uploads` and apply §6.3's
rejection outcome; P9: stamp `process_runs`). The first draft put session
binding between resolve and validate as a numbered step — that was push-only
logic inside the pipeline, a seam violation the review caught; it now lives
in the push resolver, and "no producer branching in the steps" is literally
true. Check criterion (ADR 0014 /
P9-B): a `FinalizeRequest` naming `process_run` and `staging/runs/{id}/` must
be acceptable with no changes to the step code. Honest scoping on
`ItemRef.kind: "staged"` (loading item JSON from storage — the Phase 9 ref
kind push never uses): the resolver is an interface with only the `pgstac`
resolver implemented in Phase 7; the `staged` resolver is Phase 9's first
task and its absence is a `NotImplementedError`, not a seam violation — the
seam obligation is the request shape and step neutrality, not dead code.

Flow in detail, for push (resolver and recorder bracket the neutral steps):

- **Resolve (push resolver)** — load the item from pgstac; collect its
  `staging://` hrefs; enforce the §4.2 admission rules against
  `staged_uploads` (single session, minted for this collection, unbound or
  bound to this `item_id`) and bind the session. Violations → reject.
1. **Platform pre-flight** — producer-neutral safety re-checks at execution
   time, defending the ADR 0011 / I-59 races the review flagged now that
   external writers exist: refuse (reject) when the target collection is
   `archived` (a pending session must not dodge the BFF's archived-409 by
   upserting via pypgstac after an archive flip), and refuse when an **open
   `asset_gc` mark** covers the item's canonical prefix (grace-window id
   reuse: an external DELETE marks the prefix; a re-push of the same id
   would otherwise move bytes under the open mark and the collector would
   delete them — reason `gc_pending`, retryable once the mark collects).
2. **Validate** — stac-pydantic core `Item` (§6.2), on the item *as it will
   be after rewrite* (hrefs substituted in-memory first, so the validated
   document is the one that lands).
3. **Checksum** — sha256 each staged object (`head_object` for size, `get` +
   hash; recorded on the ledger row). A referenced object missing from
   staging → reject (client never PUT the bytes, or TTL swept them).
4. **Move** — server-side `CopyObject` of each staged object to
   `assets/{collection}/{item_id}/{filename}` (existing canonical key
   builders; `storage/platform.py` has no copy primitive today — P7-E adds
   `copy_object`, same-bucket server-side so no bytes stream through the
   worker and the I-19 buffering ceiling does not apply to the move), verify
   checksum, then delete the staged object. Copy-then-delete: a crash
   between the two leaves a duplicate the TTL sweep removes — never a lost
   byte (§6.4).
5. **Rewrite + upsert** — asset hrefs become `/api/assets/…`
   (`storage/keys.py` already has the `asset_href` mirror helper) and the
   item is upserted via `pgstac_writer` — only into `output_collections`.
   The upsert emits the ordinary outbox `update` event that drives delivery
   (§7).
- **Record (push recorder)** — stamps the ledger row and, on rejection,
  applies the §6.3 outcome (delete / restore / leave-broken by op and
  snapshot). The triggering event's `op` reaches the recorder **inside
  `provenance`** (`{upload_id, event_op}` — producer-specific by design,
  residual R5): the Tier-2 discrimination needs no step-level parameter,
  and the seam's step signatures stay producer-free.

### 6.2 Validation library: stac-pydantic vs stac-validator

| | stac-pydantic 3.6.0 | stac-validator |
|---|---|---|
| Already a dependency | **yes** — pinned, it *is* the ITEMIZE gate | no (brings the jsonschema stack) |
| Network at validate time | none — offline structural models | **fetches remote JSON schemas** for core + every `stac_extensions` URL |
| Egress posture | fits (no egress) | would need schema hosts allowlisted through the pipeline egress policy — the exact hole §5.5 exists to avoid |
| Determinism | pinned-version semantics | remote schema edits silently change accept/reject |
| Per-item cost | ~ms, in-process | HTTP round-trips per schema (cacheable, still slow and fallible) |
| Extension validation | **no** — core spec only | yes (its one real advantage) |

**Decision: stac-pydantic**, reusing the exact ITEMIZE gate — `validate_item`
is lifted from `pipeline/ingest/itemize.py` into `pipeline/stac/validate.py`
and imported by both (same core-`Item`-not-`api.Item` choice, same rationale:
finalized items are catalog items, not API page entries). This also keeps
§6.1's "validation before upsert" claim *uniform*: pushed and polled items
pass the identical gate. Extension-schema validation (stac-validator's value)
is **deferred** — §6.1's "+ stac-validator on demand" remains an aspiration
for both ingest paths, logged in ISSUES (§13). Anything else would make the
pushed-item gate stricter than the polled-item gate, which is indefensible.

### 6.3 Failure semantics — where the client sees a rejection

*(Reworked after review findings 1–2: the first draft keyed the rejection
outcome on "never previously finalized" per the LEDGER, which (a) would have
deleted pre-existing poll-ingested/BFF items that a push update touched, and
(b) pretended a "prior version" existed for updates when pgstac keeps no
history — the proxy upsert has already replaced the stored document before
finalize ever runs. The redesign discriminates on the triggering outbox
event's `op` and on an app-side snapshot, never on ledger history.)*

**Tier 0 — synchronous (brokered path, §4.3).** Grammar, session-binding,
collection, and gross-shape failures are 4xxs before anything reaches pgstac.
Nothing created, nothing destroyed, no ledger churn, no alert. This is where
the review's hybrid pays off: the async machinery below handles only what is
unknowable at POST time (missing bytes, checksum mismatch, deep validation)
plus whatever arrives via the direct path.

**Tier 1 — async verdict, always in the ledger.** `staged_uploads.status` →
`finalized` (with `item_id`, checksums) or `rejected` (with
`result.rejected[].reason`, machine-readable). The client polls
`GET /api/uploads/{uploadId}` — the API-visible outcome, documented as the
required last step of a push. A push client's 201/200 from the write is
**provisional** either way.

**Tier 2 — what happens to the catalog document, by `op`:**

- **`op = insert`** (the item did not exist before this push — the outbox
  event already carries the op): finalize **deletes** the item
  (`pgstac.delete_item`). This restores the exact prior state — absence —
  and the catalog never retains an item whose platform assets will never
  exist. The delete's outbox event drains without delivery like every delete.
- **`op = update`, brokered** (the ledger row has a `prior_item` snapshot,
  §4.3): finalize **restores** the snapshot — upserts it back via the same
  writer. The pre-push version returns; its outbox update event redispatches
  (checksums unchanged ⇒ `on_update: redeliver` moves nothing). `result`
  records `restored: true`. Caveat (client docs): the restore can clobber a
  *concurrent* write that landed between snapshot and restore — the same
  lost-update window any racing writers already have (§12).
- **`op = update`, direct** (no snapshot exists; the prior version is
  unrecoverable — the client's own PUT already replaced it): finalize
  **leaves the stored document as-is** and never deletes it — deleting would
  destroy an item that existed before the push (finding 2's exact hazard).
  The item is now visibly broken (staged hrefs that will dangle once the TTL
  sweep runs); the ledger row is `rejected`, the `push_rejected` alert
  message flags it as *requires manual fix* (distinct wording from insert
  rejections), and the documented recovery is a corrected re-push (or an
  operator delete). This is the direct path's sharpest edge and one of the
  two reasons `docs/push-ingest.md` steers clients to the brokered path.

**No zombie loop** (finding 1's second half): each staged event is consumed
exactly once — enqueue-finalize-then-drain (§6.4). Later events for a
terminal-`rejected` session (e.g. the client PUTs again reusing the dead
session) route to finalize, whose **claim on the already-terminal row fails
and the job no-ops** — a structured log line and a
`pipeline_finalize_items_total{outcome="stale_claim"}`-style count, not a
fresh rejection stamp (the row's verdict is already written and stays
untouched) — and nothing is re-enqueued. On the brokered path the same
condition never gets that far: the resolver's admission rule 400s
synchronously with reason `session_terminal`. A broken direct-path item is
therefore alerted, ledgered, and inert — never silently retried forever.

**Tier 3 — an alert** — `push_rejected` (§8), so operators see rejections
without polling.

Staged bytes of a rejected upload are **not** proactively deleted — they are
left to the existing TTL sweep, exactly as ADR 0014 specifies for abandoned
uploads (one cleaner, not two).

### 6.4 Trigger, idempotency, crash-safety

- **Trigger:** the dispatcher (§7) enqueues one finalize job per staged item
  event, *before* draining the event (the delivery pattern's
  enqueue-before-drain, at-least-once). The job claims the ledger row
  (`pending` → `finalizing`, `FOR UPDATE SKIP LOCKED` — the ADR 0004 drain
  idiom) so concurrent duplicates no-op.
- **Idempotent steps:** re-running finalize re-copies identical bytes over
  identical keys (checksums verify), re-rewrites to the same hrefs, and
  re-upserts the same document — pypgstac upsert is the ingest path's proven
  idempotent write. A crash at any step leaves a state a re-run walks through
  harmlessly.
- **Crash recovery sweep:** a periodic `finalize_sweep` (piggybacking the
  scheduler cadence conventions) re-queues `finalizing` rows older than a
  stale threshold (the `STALE_CLAIM_SECONDS` idiom) and flips `pending` rows
  past `created_at + STAGING_TTL_SECONDS` to `expired` — the §4.1 governing
  clock (minted but never pushed: the ledger's answer to the byte sweep).
- **Mark-first where bytes move (ADR 0011):** finalize *adds* bytes; the
  paths that *remove* them stay marked-then-collected — rejection leaves
  staging to the TTL sweep; and once canonical, the item's bytes are under
  the ordinary `asset_gc` regime (retention, delete, archive — §7.3 closes
  the external-delete corner), with the §6.1 pre-flight refusing to move
  bytes under an open mark. One honest asterisk (review note): the
  post-move delete of the staged *originals* is a new staging-byte deletion
  call site — safe by construction (only after the canonical copy is
  checksum-verified), but it is a deletion path and is named as such rather
  than hidden under "no new deletion path".

## 7. Dispatcher: deferral of staged items

### 7.1 The gate (§6.4)

`dispatch_once` (the marked seam in `pipeline/dispatcher/loop.py`) gains one
check after `get_item`: **if any asset href starts with `staging://`, the
item is not deliverable** — enqueue a finalize job for it (batched per
collection like delivery batches) and drain the event without matching
associations. No delivery ever streams from staging.

### 7.2 No double-fire

The original *insert* event (staged hrefs) is consumed by the finalize
enqueue. Finalize's upsert emits a fresh *update* event; by the time the
dispatcher claims it, `get_item` returns canonical `/api/assets/…` hrefs and
it dispatches to delivery **once**. A second client PUT while still staged
produces another staged event → another finalize enqueue → the ledger claim
makes it a no-op. There is no state machine to add: the href content *is* the
readiness marker, and the outbox ordering does the rest. (Visibility-retry
I-38 machinery is untouched — a staged item *is* visible; it is just routed
to finalize instead of delivery.) The review traced this logic against the
code and confirmed it sound: `on_update: ignore` keys on `delivery_log`
prior state (`delivery/worker.py:149`), so the finalize-emitted update event
cannot double-deliver under either `on_update` policy.

Items in externally-writable collections with **no** staged hrefs
(metadata-only pushes, BFF writes, ingest-produced items) dispatch exactly as
today — the gate is per-item-content, not per-collection, so it costs nothing
on the hot path for ordinary collections.

### 7.3 External deletes join GC (closing an I-51-class corner)

An external client may DELETE an item through the proxy (policy permitting,
§5). Nothing app-side runs, so nobody marks `asset_gc` — the exact orphan
shape ADR 0011 killed. Fix, in the same dispatcher pass: on claiming a
`delete` event for a built-in collection, **mark the item's canonical prefix**
(`reason: item_delete`) before draining the event. The mark is idempotent
against app/pipeline marks (the partial unique index on open keys), and
prefixes with no objects collect as a normal zero. Delete events still never
match delivery associations.

**Crash-safety detail the review caught:** `dispatch_once`'s per-event
poison-isolation (`loop.py:130-142`, I-39) catches exceptions and drains the
event anyway — a *transient* DB error on the GC mark would drain the delete
event with no mark written, orphaning bytes (the I-51 shape again). Mark
failures are therefore routed through the **I-38 defer path** instead of the
poison-drain: release the event with the cool-off (`release_for_retry`) so a
later wake retries the mark; only the bounded-attempts cap drains it with a
loud log. The at-least-once guarantee then genuinely holds: the event is not
drained until the mark commits or the retry budget is spent.

## 8. Alerting hooks

`push_rejected` joins **`MONITOR_KINDS`** — the flow monitor stays the single
writer (raise / re-fire / auto-resolve) for it, per the M2-B ownership rule;
finalize itself writes **no** alert rows (it writes the ledger; the monitor
observes state — the same division as `ingest_failed`).

- **Condition** (evaluated per collection each tick): `staged_uploads` rows
  with `status = 'rejected'` and `finalized_at > now() − lookback`
  (`PUSH_ALERT_LOOKBACK_SECONDS`, default 24 h) **and newer than the dedup
  key's most recent `resolved_at`** — rejected ledger rows are terminal, so
  without the resolved-at floor a manual *resolve* would re-fire (and
  re-notify) on the very next tick for the remainder of the lookback, a
  wart the review flagged. With it: fires while unresolved recent rejections
  exist; auto-resolves when the window empties; a manual resolve sticks
  until a **new** rejection arrives; `last_seen` bumps on continued
  rejections; `ack` suppresses notification as usual.
- **Anchor:** alerts today anchor on connection / association / channel —
  push has none of these. Migration 021 adds a nullable
  `alerts.collection_id` anchor, extends the open-dedup index with it
  (mirroring how M2-C added the `channel_id` anchor), and the app derives the
  alert's group through `collection_settings.group_id` (unowned collection →
  null group → admin-only, the existing gone-group rule in
  `lib/alerts/access.ts`; the client docs say: set collection ownership if
  your operators should see push alerts).
- The closed alert-kind enum becomes a golden fixture now (§10) — Phase 7 is
  the first change to the enum since the fixture was deferred in M2, so it
  pays the debt (P9-D expected this).

`webhook`/`in_app` fan-out needs nothing: a `push_rejected` alert is an
ordinary alert row.

## 9. `/metrics` (M2-H pattern)

- `pipeline_job_runs_total` / `pipeline_job_seconds` cover the finalize job
  and sweep automatically — they are registered through the queue backend
  like every job, so `instrument_handler` wraps them centrally. Nothing to
  do.
- New counters in `pipeline/metrics.py`, incremented at finalize's terminal
  choke points (the flow-stats-hook idiom):
  - `pipeline_finalize_items_total{producer, outcome}` — `upserted` |
    `rejected`;
  - `pipeline_finalize_bytes_total{producer}` — bytes moved
    staging→canonical.
  Labeled by `producer` so Phase 9 gets its telemetry for free — the M2-H
  central pattern applied to the ADR 0014 seam.
- Log lines carry structured `extra={upload_id, collection_id, item_id,
  producer, …}`, never interpolated (M2-H rule).

## 10. Cross-runtime contracts → golden fixtures

Per the `tests/contract-fixtures/README.md` rule (shapes land WITH the schema
code, in the same slice):

| Fixture | Shape | Writer / reader |
|---|---|---|
| `staged-asset-href.json` | the `staging://{upload_id}/{filename}` grammar | app mints (`staged_href`), pipeline parses (dispatcher detect + finalize resolve). Cases: valid; traversal segments (`..`, `/`) reject/reject; unknown scheme ignored-by-dispatcher (app n/a); missing filename reject/reject |
| `push-upload-status.json` | `staged_uploads.status` enum (`pending → finalizing → finalized \| rejected \| expired`) + the `result` jsonb (`{upserted, rejected[{reason}], restored?, checksums}`) incl. the closed `reason` string set | pipeline writes, app serves on the poll route — the asymmetric write-gate/lenient-reader contract inverted (pipeline writer, Zod reader on the poll response) — encode direction in cases |
| `alert-kinds.json` | the closed alert `kind` enum: `MONITOR_KINDS` (six today, **seven once `push_rejected` joins**) + notify's `webhook_failed` | pinned by pipeline writers, the app's `EXPECTATION_BREACH_KIND` branch, and the monitoring UI labels — pulled forward from the P9-D plan (Phase 9 appends its two kinds later) |

None of the three fits the README's `minimal`/`defaults`/`cases[]` config
format as-is (an href grammar, an inverted-direction status contract, a bare
enum) — the slices that land them (P7-C, P7-H) **extend
`tests/contract-fixtures/README.md`** with the two extra fixture styles
(grammar-cases; pinned-enum) in the same commit, so the format doc never
drifts from the files.

`FinalizeRequest` itself is **not** a fixture: it never crosses the
app/pipeline boundary in Phase 7 (built and consumed pipeline-side). When
Phase 9's UI test-run bridge makes it cross-runtime, it becomes one — noted
in the P9 spec's fixture plan.

## 11. Migrations (app owns all DDL — ADR 0001)

- **020 `staged_uploads`:**

  ```sql
  CREATE TABLE stac_higher.staged_uploads (
    id            uuid PRIMARY KEY,            -- the upload_id in staged hrefs
    collection_id text NOT NULL,
    item_id       text,                        -- bound at finalize
    created_by    text NOT NULL,
    group_id      text,
    filenames     jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','finalizing','finalized','rejected','expired')),
    result        jsonb,                       -- FinalizeResult subset + checksums
    prior_item    jsonb,                       -- §4.3 snapshot (brokered PUTs) — §6.3 restore point
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,                 -- stale-claim recovery
    finalized_at  timestamptz
  );
  CREATE INDEX ON stac_higher.staged_uploads (collection_id, status);
  CREATE INDEX ON stac_higher.staged_uploads (status, created_at);
  ```

  App INSERTs (`pending`) and reads; the pipeline writes status/result
  columns and never DDL — the `ingest_files` split, verbatim. Hygiene: NOT
  partitioned (verb target: poll; ADR 0012 criteria). Pruning is **assigned,
  not just claimed** (review finding): P7-H extends the `history_retention`
  sweep (`pipeline/history/sweep.py`) to prune terminal
  (`finalized`/`rejected`/`expired`) rows past the conservative age
  threshold, and appends `staged_uploads` to ADR 0012's swept-tables list as
  a status-update amendment in the same slice.
- **021 `alerts.collection_id` anchor:** `ALTER TABLE … ADD COLUMN
  collection_id text` (no FK — matching `collection_settings`, whose
  `collection_id` is itself unconstrained text), recreate
  `alerts_open_dedup_idx` with the fourth column, add
  `alerts_collection_idx`, **and recreate migration 015's
  `alerts_anchor_check` CHECK constraint with a fourth leg** — as shipped it
  requires connection_id OR association_id OR channel_id
  (`migrate.ts:661-666`), so without this every `push_rejected` insert
  would violate it (review finding; the first draft missed it). The
  pipeline's `sync_alerts` ON CONFLICT target moves to the widened dedup
  index in lockstep. (Recreating index + constraint on a near-empty local
  table is safe; ordering matters at scale — same argument as M2-G's
  "partition while near-empty".)

No pipeline DDL anywhere (ADR 0001 holds).

## 12. API client docs (`docs/push-ingest.md`)

New doc, linked from `docs/README.md` and AGENTS.md; contents:

1. prerequisites — a confidential OIDC client with the `stac-higher`
   audience + client-credentials grant (deployment realm work; local dev
   client provided), a collection flagged externally-writable by an operator;
2. the four-step flow with curl: token → `POST /api/uploads` (staged) → PUT
   bytes → **POST/PUT the item to the brokered route**
   (`/api/catalog/collections/{id}/items…`, §4.3) with `staged_href`s →
   poll `GET /api/uploads/{id}` until `finalized`/`rejected`. The direct
   `:8081` path documented separately, flagged discouraged, with its sharper
   edges (§6.3 leave-broken on update rejection; no audit trail);
3. semantics that surprise, each stated in bold: the write's 201/200 is
   **provisional**; insert rejections **delete** the item; brokered update
   rejections **restore** the pre-push version (and can clobber a concurrent
   racing write — the §6.3 lost-update window, which also applies to a
   client PUT racing an in-flight finalize); direct update rejections leave
   a **broken item requiring manual fix**; strictly one upload session per
   item; the item **must carry top-level `collection`** (without it the
   proxy policy fails closed → 403); the single staging-expiry clock and its
   window (§4.1); `bulk_items` is **unsupported** for push; metadata-only
   pushes; update pushes (`on_update` delivery semantics apply); delete
   (brokered delete is audited + GC-marked app-side; direct delete relies on
   §7.3);
4. failure table: every `rejected.reason` string (`multi_session`,
   `session_terminal`, `gc_pending`, `collection_archived`, `missing_bytes`,
   `checksum_mismatch`, `invalid_item`, …), which tier surfaces it
   (synchronous 4xx vs ledger), plus where alerts land;
5. limits (honest): single presigned PUT per file (no multipart — large-file
   ceiling), no rate limiting/quota, core-spec validation only (no extension
   schemas), push alerts require collection ownership to be group-visible —
   each cross-referenced to its ISSUES entry.

AGENTS.md route table gains the `GET /api/uploads/[uploadId]` row, the
uploads row's staged-mode note, and the catalog BFF row's push note;
`docs/auth.md` gains the bearer section (P7-B). P7-I also carries three
cross-doc amendments the review demanded: the **approved Phase 9 spec**
(`2026-08-29-phase9-processes-design.md`, slice M5-0) claims migrations
020–021 and `alert-kinds.json` — renumber M5-0's migrations and change its
fixture task to "append `process_failed`/`process_stalled`" (Phase 7 lands
first and takes both, correctly — but the P9 spec must say so); an
**ADR 0008 status-quo update** — its "the BFF is for browser sessions only /
external clients present their tokens directly to the proxy" text is
superseded by §4.3's brokered push path (exactly what its own revisit note
anticipated); and a **ROADMAP §6.2 diagram note** — the diagram shows the
href rewrite going back through stac-fastapi, while finalize upserts via
pypgstac (the ITEMIZE precedent).

## 13. Deferred, and logged as such (new ISSUES entries at merge)

- **No rate limiting or per-group quota on push** — an external client can
  fill staging / hammer finalize; the TTL sweep bounds storage but not churn.
  (The Phase 9 run-rate-ceiling requirement is the analogous control; push
  gets one when either demands it.)
- **No multipart upload** — single presigned PUT caps practical file size;
  large-asset push waits for a real need (same class as ingest streaming,
  I-19).
- **Extension-schema validation** ("stac-validator on demand", §6.1) —
  deferred for both ingest and push (§6.2).
- **Asset filename orphans on update pushes** — replacing an item's assets
  with *different filenames* leaves the old canonical files until item
  delete/retention (same class as re-ingest replacement; bytes are GC'd at
  item deletion since the prefix mark covers them).
- **Proxy policy DB coupling** — the enforced-mode proxy now reads the app
  DB (read-only, one table). Acceptable locally; the cloud deployment must
  grant it a read-only role (Phase 8 IaC note in ADR 0015).
- **Audit gap on the direct path** (new ISSUES entry) — ROADMAP §5.5
  promises every mutation audited, but external writes straight to the
  proxy land no `audit_log` row (only the mint and poll are audited
  app-side), and finalize's own catalog actions (rejection delete, restore)
  run pipeline-side where no audit writer exists. The brokered path (§4.3)
  closes the gap for cooperative clients — its writes go through the guard
  like any BFF write — and is the honest mitigation; proxy-side audit (or a
  pipeline audit writer) is the deferred remainder.
- **I-15 escalates** (amend I-15) — `S3_ENDPOINT` must now be reachable
  from **external push clients' networks**, a superset of the
  browser-reachable requirement I-15 records today; presigned PUT URLs are
  minted offline against that endpoint.
- **`bulk_items` push unsupported** — denied outright in enforced mode
  (§5.2); supporting bulk push means teaching the factory (and finalize)
  the bulk body shape. Deferred until someone needs it.
- **I-13 unchanged** — asset reads stay authentication-only; push clients
  can read any asset any authenticated user can. Still I-1's problem.
- **I-14 unchanged** — manual UI uploads stay direct-to-canonical (ADR
  0005's revisit stands; Phase 7 does not force the UI through staging).
- **Claims mapping duplicated at the proxy** — the policy factory maps roles
  from a raw claim path independently of the app's mapper config; a
  deployment with exotic claims must configure both. Logged; convergence is
  an OPA-era problem.

## 14. Slices

Worked by the standard loop: worktree off `ai/main`, `npm run verify` (+
`uv run pytest` / `ruff` when the pipeline is touched) before merge; no e2e,
dev server, or Docker in teammate slices (P7-Z is the lead's live pass).

| # | Slice | Spec § | Approximate file footprint | Ordering |
|---|---|---|---|---|
| P7-B | Bearer-token auth for `/api/*` | §3 | `app/src/lib/auth/` (new `bearer.ts`, `claims.ts` touch), `src/middleware.ts`, `docs/auth.md`, `infra/keycloak/realm-stac-higher.json` (dev push client), `.env.example`, unit tests | **PARALLEL** (owns `middleware.ts` and `docs/auth.md` exclusively) |
| P7-C | Staged-upload mint + ledger + poll route | §4.1–4.2, §11 (020) | migration 020 in `app/src/lib/db/migrate.ts`, `app/src/pages/api/uploads/` (index + `[uploadId].ts`), `app/src/lib/storage/keys.ts` (staged-href helpers), `app/src/lib/uploads/` (new), `authz/permissions.ts` (gated rows), fixtures `staged-asset-href.json` + `push-upload-status.json` + `tests/contract-fixtures/README.md` (grammar/status styles) + vitest consumers. Uses the **route-local `runMigrations()` pattern** (`gc/marks.ts:42`), NOT a `middleware.ts` prefix-list edit — that file is P7-B's | **PARALLEL** with B and G |
| P7-D | Brokered push write path on the BFF route | §4.3 | `app/src/pages/api/catalog/[...path].ts` (bearer-caller forwarding, staged pre-validation, `prior_item` snapshot, `X-BFF-Auth` header — this slice owns the file), `app/src/lib/push/` (new), env plumbing, unit tests | **SEQUENTIAL** after B (bearer identities) and C (ledger + href module) |
| P7-E | Finalize module + job + sweep + metrics | §6, §9 | `services/pipeline/src/pipeline/finalize/` (new: seam, steps, resolvers, recorders, repo), `pipeline/stac/validate.py` (lift from `ingest/itemize.py`), `pipeline/jobs/finalize.py`, `pipeline/metrics.py`, `pipeline/storage/platform.py` (**`copy_object`**), `pipeline/jobs/staging_cleanup.py` (§4.1 ledger clock), pytest incl. fixture consumers | **SEQUENTIAL** after C (ledger schema + fixtures) |
| P7-F | Dispatcher staged-gating + delete-event GC mark (defer-on-failure) | §7 | `pipeline/dispatcher/loop.py` + `repo.py`, `pipeline/jobs/dispatch.py`, pytest | **SEQUENTIAL** after E (enqueues the finalize job) |
| P7-G | Proxy write policy: derived image, pins, factory, overlay, integration legs | §5 | `services/proxy-policy/` (new package + tests), `infra/proxy-policy/Dockerfile`, `infra/compose.auth-enforced.yml` (factory config, `ITEMS_FILTER_PATH` incl. `bulk_items`, mandatory secret), `docker-compose.yml` (image pins for auth-proxy + stac-fastapi-pgstac), `tests/integration/` (policy legs incl. bulk-deny + read-shape legs; updates the pinned member-can-write test), ADR 0015 acceptance edit. No app files — the header lives in P7-D | **PARALLEL** with B/C/D/E/F (fully disjoint files; its integration legs skip without the enforced stack and go green at P7-Z) |
| P7-H | `push_rejected` alerting + ledger hygiene | §8, §11 (021) | migration 021 in `migrate.ts` (anchor + CHECK + dedup index), `pipeline/flow/monitor.py` + `flow/repo.py`, `pipeline/history/sweep.py` (`staged_uploads` pruning), `docs/decisions/0012-table-hygiene.md` (amendment), `app/src/lib/alerts/` (collection anchor + scoping), monitoring UI kind label, fixture `alert-kinds.json` + `tests/contract-fixtures/README.md` (enum style) + both consumers | **SEQUENTIAL** after E (observes the ledger) and after C (migration numbering: C=020, H=021) |
| P7-I | `docs/push-ingest.md` + doc catch-up: AGENTS route table, FEATURES, ISSUES entries from §13 (incl. the audit-gap entry and the I-15 amendment), **Phase 9 spec amendment** (M5-0 renumber + alert-kinds "append"), **ADR 0008 status-quo update** (§4.3 supersedes its browser-only BFF scope), ROADMAP §6.2 diagram note + phase row | §12, §13 | docs only | **SEQUENTIAL** last, before the gate |
| P7-Z | **Live gate check — the existing task in `TODO.md`; referenced, not redefined here** | §1 | lead only | last |

Dependency spine: C → {D (also after B), E} → F → H → I; B and G float in
parallel; Z closes. (Nothing in C/E–H imports B — dev-bypass stands in until
P7-D and P7-Z, which are the legs that prove it.)

## 15. Testing & risk

- Per slice: vitest + pytest with the shared fixtures; `npm run verify`
  before every merge; e2e only if a slice touches UI flows (none should —
  Phase 7 has no UI surface beyond the already-shipped Settings toggle).
- **Riskiest edge: the async rejection outcomes** (§6.3) — now tiered and
  op-discriminated after review findings 1–2: insert-delete restores
  absence, brokered-update restores the snapshot, direct-update
  leaves-broken with a distinct alert. The brokered pre-validation shrinks
  the async surface to byte/checksum/deep-validation failures; the
  remaining sharp edge (direct-path update rejection) is documented, not
  hidden.
- **The proxy filter factory is no longer the fragile unknown** — both
  verify-first probes were settled favorably from fetched 1.2.0 source
  (§5.2); residual risk is version drift, held by the image pins and P7-G's
  one-line live confirm. The `bulk_items` denial's rejection *shape* is the
  one remaining in-slice verification.
- **Seam discipline:** P7-E's review must apply the ADR 0014 check criterion
  literally before merge (a `process_run` request with a different staging
  prefix compiles and routes through the same steps — with the §6.1
  resolver/recorder layers carrying all producer-specific logic).
