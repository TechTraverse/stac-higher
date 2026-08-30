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
| Outbox + dispatcher | `pipeline/dispatcher/` — the loop's header comment marks the §6.4 finalize-gating seam explicitly | staged-item events route to finalize instead of delivery |
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
  "expires_at": "…"                         // mint time + STAGING_TTL
}
```

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

The client PUTs bytes to the presigned URLs, then POSTs the item to the
**proxy** (`:8081`, ROADMAP §6.2 — the catalog plane, not the app) with each
platform-hosted asset's `href` set to the `staged_href` **verbatim**:

```
staging://{upload_id}/{filename}
```

The grammar is deliberately unmistakable: no real scheme collides with it, the
dispatcher detects it with a prefix check, and the finalize step parses
`upload_id`/`filename` from it with the same segment validation as the key
builders. It is a cross-runtime contract → golden fixture (§10).

**Contract rules** (documented in `docs/push-ingest.md`, enforced at
finalize):

- one upload session per item — an upload_id already finalized for a
  different `item_id` is a rejection (the ledger stores the bound item);
- every `staging://` href in an item must resolve to a `staged_uploads` row
  minted for that item's collection;
- assets with ordinary external hrefs (`https://…`) are allowed and pass
  through untouched — metadata-only or mixed pushes are legal; such items
  have nothing to finalize and deliver immediately (§7);
- multiple staged hrefs from the same session = a multi-asset item; fine.

`GET /api/uploads/{uploadId}` (new route; authenticated, creator's group or
admin) polls the ledger row — the ADR 0004 request-table poll shape, reused.

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
in-repo Python package (`services/proxy-policy/`) mounted into the proxy
container and enabled **only in the auth-enforced overlay**. Per request it
receives `{req, payload}` and returns CQL2:

- **Read methods → no restriction** (`true`). Read-visibility remains I-1;
  this factory deliberately does not touch it (but it is the seam where I-1's
  filter will later land — one policy point, not two).
- **Write methods (POST/PUT/PATCH/DELETE on items):**
  - requests carrying the BFF's shared-secret header (`X-BFF-Auth`, env
    `CATALOG_BFF_SHARED_SECRET` on both app and proxy; the BFF route adds it
    when configured) → no restriction — app-mediated writes are RBAC-gated
    and audited app-side and must reach any collection;
  - otherwise the token's roles (claim path env, default
    `realm_access.roles`) must include `operator` or `admin` — closing ADR
    0002's member-can-write limitation for the catalog plane — and the write
    is validated against `collection IN (<externally-writable set>)`, the
    set read from `collection_settings` over a read-only Postgres connection
    with a short TTL cache (~15 s; a Settings flip takes effect within it).
    An **empty** set renders a constant-false CQL2-JSON expression, never
    `IN ()` (the ADR 0002 Template hazard).
- Collection-level transactions (`POST /collections` etc.) are untouched:
  they stay PRIVATE_ENDPOINTS-token-gated; external clients do not create
  collections (that is UI/BFF work), and role-gating them can ride the same
  factory later if wanted.

**Verify-first (the "transaction filtering is v1.0-era" stance):** two things
this design assumes about upstream must be probed in the implementing slice
*before* building on them, with the OPA sidecar as the recorded fallback:
(1) the factory can branch on the request method from `req`; (2)
`CHECK_CONFORMANCE` with an items filter configured does not demand
Filter-Extension conformance our stac-fastapi-pgstac doesn't advertise (or
`CHECK_CONFORMANCE=false` is acceptable in the overlay with the risk noted).
The integration suite (`tests/integration/`) grows policy legs either way
(§14) — P7-Z proves it live.

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
- **App-mediated push writes** (POST items via an app route that forwards,
  BFF-style): attractive reuse, but it does not *enforce* anything — the
  proxy would still accept any valid token writing anywhere directly, so
  `externally_writable` would gate only cooperative clients. The flag must
  bind at the enforcement point (the proxy), per ROADMAP §7's plane split.
  (ADR 0008's revisit note about brokering push under the BFF family is
  satisfied differently: the app-side surface push clients need is the
  uploads mint, §4.)

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
  provenance:          dict     # producer-specific ledger key: {upload_id} | {run_id}

ItemRef (ref kind, orthogonal to producer):
  {kind: "pgstac", collection_id, item_id}   # item already in the catalog (push)
  {kind: "staged", key}                      # item JSON document in staging (P9)

FinalizeResult:
  upserted: [{collection_id, item_id}]
  rejected: [{item_ref, reason}]
```

The steps — **resolve refs → validate → checksum → move staging→canonical →
rewrite hrefs → upsert (restricted to `output_collections`) → ordinary outbox
events** — contain **no producer branching**. Producer differences live only
in the request (where staging is, which ledger row records the outcome, which
collections are writable) plus one thin per-producer *outcome recorder*
(push: stamp `staged_uploads`; P9: stamp `process_runs`) registered against
the `producer` value, outside the steps. Check criterion (ADR 0014 /
P9-B): a `FinalizeRequest` naming `process_run` and `staging/runs/{id}/` must
be acceptable with no changes to the step code. Honest scoping on
`ItemRef.kind: "staged"` (loading item JSON from storage — the Phase 9 ref
kind push never uses): the resolver is an interface with only the `pgstac`
resolver implemented in Phase 7; the `staged` resolver is Phase 9's first
task and its absence is a `NotImplementedError`, not a seam violation — the
seam obligation is the request shape and step neutrality, not dead code.

Steps in detail, for push:

1. **Resolve** — load the item from pgstac; collect its `staging://` hrefs.
2. **Authorize/bind** — every referenced `upload_id` must have a
   `staged_uploads` row for this item's collection, unbound or bound to this
   `item_id`; bind it. Violations → reject.
3. **Validate** — stac-pydantic core `Item` (§6.2), on the item *as it will
   be after rewrite* (hrefs substituted in-memory first, so the validated
   document is the one that lands).
4. **Checksum** — sha256 each staged object (`head_object` for size, `get` +
   hash; recorded on the ledger row). A referenced object missing from
   staging → reject (client never PUT the bytes, or TTL swept them).
5. **Move** — copy each staged object to
   `assets/{collection}/{item_id}/{filename}` (existing canonical key
   builders), verify checksum, then delete the staged object. Copy-then-
   delete: a crash between the two leaves a duplicate the TTL sweep removes —
   never a lost byte (§6.5).
6. **Rewrite + upsert** — asset hrefs become `/api/assets/…` (`assetHref`
   semantics; pipeline `storage/keys.py` grows the mirror helper) and the
   item is upserted via `pgstac_writer` — only into `output_collections`.
   The upsert emits the ordinary outbox `update` event that drives delivery
   (§7).
7. **Record** — the producer's outcome recorder stamps the ledger.

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

A push client gets a **provisional 201** from the proxy (pgstac accepted the
document; the platform has not). Finalize is asynchronous; its verdict lands
in three places:

1. **The ledger** — `staged_uploads.status` → `finalized` (with
   `item_id`, checksums) or `rejected` (with `result.rejected[].reason`,
   machine-readable). The client polls `GET /api/uploads/{uploadId}` — this
   is *the* API-visible outcome, documented as the required last step of a
   push in `docs/push-ingest.md`.
2. **The catalog** — on rejection of a **never-previously-finalized** item,
   finalize deletes the item from pgstac (`pgstac.delete_item`): the catalog
   never retains an item whose platform assets will never exist (its staged
   hrefs would dangle forever once the TTL sweep runs). The delete's outbox
   event is drained without delivery like every delete (§6.4 rule). If the
   item **had** a previously finalized version (a push *update* that failed
   validation), the prior version is left intact — rejection must never
   destroy good data; the ledger records the rejected attempt.
   *Flag for review:* deleting a client-submitted document on async
   validation failure is deliberate (the alternative is a permanently broken
   item) but is the sharpest edge in this spec — the client docs state it in
   bold, and the provisional-201 framing exists exactly for this.
3. **An alert** — `push_rejected` (§8), so operators see rejections without
   polling.

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
  older than `STAGING_TTL_SECONDS` to `expired` (minted but never pushed —
  the ledger's answer to the TTL sweep deleting their bytes).
- **Mark-first where bytes move (ADR 0011):** finalize *adds* bytes; the
  paths that *remove* them stay marked-then-collected — rejection leaves
  staging to the TTL sweep; the moved staged originals are deleted only
  after the canonical copy is checksum-verified; and once canonical, the
  item's bytes are under the ordinary `asset_gc` regime (retention, delete,
  archive — §7.3 closes the external-delete corner). No new deletion path is
  invented.

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
to finalize instead of delivery.)

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
against app/pipeline marks (the partial unique index on open keys), the
outbox row's at-least-once semantics make it crash-safe (event not drained
until the mark commits), and prefixes with no objects collect as a normal
zero. Delete events still never match delivery associations.

## 8. Alerting hooks

`push_rejected` joins **`MONITOR_KINDS`** — the flow monitor stays the single
writer (raise / re-fire / auto-resolve) for it, per the M2-B ownership rule;
finalize itself writes **no** alert rows (it writes the ledger; the monitor
observes state — the same division as `ingest_failed`).

- **Condition** (evaluated per collection each tick): `staged_uploads` rows
  with `status = 'rejected'` and `finalized_at > now() − lookback`
  (`PUSH_ALERT_LOOKBACK_SECONDS`, default 24 h). Fires while recent
  rejections exist; auto-resolves when the window empties; `last_seen` bumps
  on continued rejections; `ack` suppresses notification as usual.
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
| `push-upload-status.json` | `staged_uploads.status` enum (`pending → finalizing → finalized \| rejected \| expired`) + the `result` jsonb (`{upserted, rejected[{reason}], checksums}`) | pipeline writes, app serves on the poll route — the asymmetric write-gate/lenient-reader contract inverted (pipeline writer, Zod reader on the poll response) — encode direction in cases |
| `alert-kinds.json` | the closed alert `kind` enum: the six `MONITOR_KINDS` + `webhook_failed` + **`push_rejected`** | pinned by pipeline writers, the app's `EXPECTATION_BREACH_KIND` branch, and the monitoring UI labels — pulled forward from the P9-D plan (Phase 9 appends its two kinds later) |

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
  partitioned (verb target: poll; ADR 0012 criteria), pruned by the existing
  `history_retention` sweep once terminal and old.
- **021 `alerts.collection_id` anchor:** `ALTER TABLE … ADD COLUMN
  collection_id text`, recreate `alerts_open_dedup_idx` with it, add
  `alerts_collection_idx`. (Recreating a unique index on a near-empty local
  table is safe; ordering matters at scale — same argument as M2-G's
  "partition while near-empty".)

No pipeline DDL anywhere (ADR 0001 holds).

## 12. API client docs (`docs/push-ingest.md`)

New doc, linked from `docs/README.md` and AGENTS.md; contents:

1. prerequisites — a confidential OIDC client with the `stac-higher`
   audience + client-credentials grant (deployment realm work; local dev
   client provided), a collection flagged externally-writable by an operator;
2. the four-step flow with curl: token → `POST /api/uploads` (staged) → PUT
   bytes → POST item to `:8081` with `staged_href`s → poll
   `GET /api/uploads/{id}` until `finalized`/`rejected`;
3. semantics that surprise: the provisional 201 and rejection-deletes-item
   rule (§6.3, bold), one-upload-per-item, metadata-only pushes, update
   pushes (`on_update` delivery semantics apply), delete via proxy;
4. failure table: every `rejected.reason` string, plus where alerts land;
5. limits (honest): single presigned PUT per file (no multipart — large-file
   ceiling), no rate limiting/quota, core-spec validation only (no extension
   schemas) — each cross-referenced to its ISSUES entry.

AGENTS.md route table gains the `GET /api/uploads/[uploadId]` row and the
uploads row's staged-mode note; `docs/auth.md` gains the bearer section
(P7-B).

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
| P7-B | Bearer-token auth for `/api/*` | §3 | `app/src/lib/auth/` (new `bearer.ts`, `claims.ts` touch), `src/middleware.ts`, `docs/auth.md`, unit tests | **PARALLEL** (disjoint from C/F) |
| P7-C | Staged-upload mint + ledger + poll route | §4, §11 (020) | migration 020 in `app/src/lib/db/migrate.ts`, `app/src/pages/api/uploads/` (index + `[uploadId].ts`), `app/src/lib/storage/keys.ts` (staged-href helpers), `app/src/lib/uploads/` (new), `authz/permissions.ts` (gated rows), fixtures `staged-asset-href.json` + `push-upload-status.json` + vitest consumers | **PARALLEL** with B and F |
| P7-D | Finalize module + job + sweep + metrics | §6, §9 | `services/pipeline/src/pipeline/finalize/` (new: seam, steps, repo, recorder), `pipeline/stac/validate.py` (lift from `ingest/itemize.py`), `pipeline/jobs/finalize.py`, `pipeline/metrics.py`, `pipeline/storage/keys.py` (asset-href helper), pytest incl. fixture consumers | **SEQUENTIAL** after C (ledger schema + fixtures) |
| P7-E | Dispatcher staged-gating + delete-event GC mark | §7 | `pipeline/dispatcher/loop.py` + `repo.py`, `pipeline/jobs/dispatch.py`, pytest | **SEQUENTIAL** after D (enqueues the finalize job) |
| P7-F | Proxy write policy + BFF header + integration legs | §5 | `services/proxy-policy/` (new package), `infra/compose.auth-enforced.yml`, `docker-compose.yml` (volume mount, off by default), `app/src/pages/api/catalog/[...path].ts` (one header), `tests/integration/` (policy legs; updates the pinned member-can-write test), ADR 0015 acceptance edit | **PARALLEL** with B/C/D/E (disjoint files; verify-first probes are in-slice) |
| P7-G | `push_rejected` alerting | §8, §11 (021) | migration 021 in `migrate.ts`, `pipeline/flow/monitor.py` + `flow/repo.py`, `app/src/lib/alerts/` (collection anchor + scoping), monitoring UI kind label, fixture `alert-kinds.json` + both consumers | **SEQUENTIAL** after D (observes the ledger) and after C's 020 (migration numbering) |
| P7-H | `docs/push-ingest.md` + doc catch-up (AGENTS route table, FEATURES, ISSUES §13 entries, ROADMAP phase row) | §12, §13 | docs only | **SEQUENTIAL** last, before the gate |
| P7-Z | **Live gate check — the existing task in `TODO.md`; referenced, not redefined here** | §1 | lead only | last |

Dependency spine: C → D → E → G → H; B and F float in parallel; Z closes.
(B is required for a *real* external client but nothing in C–G imports it —
dev-bypass stands in until P7-Z, which is exactly the leg that proves B.)

## 15. Testing & risk

- Per slice: vitest + pytest with the shared fixtures; `npm run verify`
  before every merge; e2e only if a slice touches UI flows (none should —
  Phase 7 has no UI surface beyond the already-shipped Settings toggle).
- **Riskiest edge: rejection deletes a never-finalized item** (§6.3) —
  deliberate, ledger-recorded, docs-flagged; the adversarial review should
  pressure-test it (the alternative — permanent items with dangling staged
  hrefs — was judged worse).
- **Most fragile dependency: the proxy filter factory** — v1.0-era write
  validation, two verify-first probes with a recorded OPA fallback (§5.2);
  everything else in the phase stands even if F lands on the fallback.
- **Seam discipline:** P7-D's review must apply the ADR 0014 check criterion
  literally before merge (a `process_run` request with a different staging
  prefix compiles and routes through the same steps).
