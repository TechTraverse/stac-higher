# Push ingest (Phase 7)

How an external client — a script, a satellite downlink processor, another
platform — pushes items and asset bytes into the built-in catalog. The flow:
mint a **staged upload** session, PUT bytes to presigned URLs, POST/PUT the
item through the app's **brokered write path**, then poll the session until
the pipeline's **finalize** step has validated, checksummed, and moved the
bytes into canonical storage and rewritten the item's asset hrefs. From there
the item delivers onward like any other.

Design: `superpowers/specs/2026-08-29-phase7-push-ingest-design.md`;
enforcement: [ADR 0015](decisions/0015-proxy-write-policy.md); auth details:
[`auth.md`](auth.md).

## Prerequisites

1. **A token.** Deployments create a **confidential OIDC client** with the
   `stac-higher` audience mapper and the client-credentials grant — the same
   token the auth-enforced proxy validates. For local dev the realm file
   ships `stac-higher-push` (secret `stac-higher-push-secret`; its service
   account is an operator in `earth-observation` — a dev-only artifact, same
   never-deploy caveat as the ADR 0002 test clients):

   ```sh
   TOKEN=$(curl -s http://localhost:8180/realms/stac-higher/protocol/openid-connect/token \
     -d grant_type=client_credentials \
     -d client_id=stac-higher-push \
     -d client_secret=stac-higher-push-secret | jq -r .access_token)
   ```

   The app accepts `Authorization: Bearer <JWT>` on `/api/*` in `oidc` mode
   (see [`auth.md`](auth.md) "Bearer tokens"); the claims run through the
   normal mapper, so the token's identity needs the **operator** (or admin)
   role and — for an owned collection — membership in the owning group.
2. **An externally-writable collection.** A collection accepts push only
   after an operator flags `externally_writable` in its **Settings** tab
   (M2-E). A missing settings row means `false`; an `archived` collection
   refuses item writes (409). **Set the collection's owning group too if
   your operators should see push alerts** — `push_rejected` alerts derive
   their group from `collection_settings.group_id`, and an unowned
   collection's alerts are admin-only.
3. **Client-reachable object storage.** Presigned PUT URLs are minted
   offline against `S3_ENDPOINT`, so that endpoint must be reachable from
   the **push client's network** — a superset of the browser-reachable
   requirement (ISSUES I-15). `http://localhost:9000` works only for a
   client on the host.

## The four-step flow

### 1. Mint a staged upload session

`POST /api/uploads` with a body **without `item`** selects staged mode (a
body with `item` is the unchanged trusted-UI canonical mode, ADR 0005):

```sh
curl -s http://localhost:4321/api/uploads \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"collection": "sentinel-pushed",
       "files": [{"filename": "B04.tif", "contentType": "image/tiff"}]}'
```

```jsonc
// 200 →
{
  "upload_id": "0d9c…",                       // uuid — names the session
  "uploads": [{
    "filename": "B04.tif",
    "url": "https://…presigned PUT…",          // staging/{upload_id}/B04.tif
    "staged_href": "staging://0d9c…/B04.tif"   // goes in the item verbatim
  }],
  "expires_at": "…"                            // THE staging clock, see below
}
```

Preconditions are checked before any bytes move: operator+ in the owning
group (unowned → any operator, ADR 0003), `externally_writable = true`, not
`archived` (409). The mint inserts one `stac_higher.staged_uploads` ledger
row — the authorization binding finalize checks (staged refs are honored
only for the collection the session was minted for) and where you poll the
async verdict.

**One governing clock**: staging expiry is the ledger row's
`created_at + STAGING_TTL_SECONDS` (default 24 h), everywhere — `expires_at`
is that instant, the finalize sweep flips `pending` rows past it to
`expired`, and the byte-level TTL sweep respects it. A session not pushed
and finalized within the window is gone; mint a new one.

### 2. PUT the bytes

```sh
curl -s -X PUT --data-binary @B04.tif -H "Content-Type: image/tiff" "$PRESIGNED_URL"
```

One presigned PUT per file — **no multipart**, which caps practical file
size (ISSUES I-72).

### 3. POST/PUT the item — the brokered route (the documented default)

Send the item to the app's BFF catalog route with each platform-hosted
asset's `href` set to the `staged_href` **verbatim**:

```sh
curl -s http://localhost:4321/api/catalog/collections/sentinel-pushed/items \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @item.json    # assets.B04.href = "staging://0d9c…/B04.tif"
```

- `POST /api/catalog/collections/{id}/items` — create;
  `PUT …/items/{itemId}` — update; `PATCH`/`DELETE` per the rules below.
- The item **must carry its top-level `collection` field**, matching the
  path (the brokered route 400s helpfully without it; the direct path's
  failure is much worse — see below).
- Assets with ordinary external hrefs (`https://…`) pass through untouched —
  metadata-only or mixed pushes are legal; items with no staged hrefs have
  nothing to finalize and deliver immediately.
- **Strictly one upload session per item, both directions**: an item
  referencing two sessions is rejected (`multi_session`), and a session
  already bound to a different item is rejected (`bound_to_other_item`).
  Multiple staged hrefs from the *same* session = a multi-asset item; fine.

The write's 201/200 is **provisional** — finalize has not run yet. Step 4 is
the required last step of every push that referenced staged assets.

### 4. Poll the session

```sh
curl -s http://localhost:4321/api/uploads/$UPLOAD_ID -H "Authorization: Bearer $TOKEN"
```

`status` walks `pending → finalizing → finalized | rejected | expired`;
`result` carries `{upserted, rejected[{reason}], restored?, checksums}`.
Visibility: admin, a member of the session's group, or the session's creator
(covers unowned collections); anyone else gets a 404.

## What the brokered route does for you

Every **bearer-identity** write on `/api/catalog/[...path]` — item
POST/PUT/PATCH/DELETE, staged hrefs or not — is first held to the
precondition set (`externally_writable`, not `archived`, group rule).
Session-cookie (browser) callers are untouched; the split is on how the
identity arrived, not who it is. On top of that:

- **Synchronous pre-validation** for bodies containing `staging://` hrefs:
  href grammar, the single-session rule, session exists / not terminal /
  minted for this collection / not bound to another item, top-level
  `collection` present and matching the path, and a light structural check
  (type/id/geometry/properties presence — deliberately *not* full STAC
  validation; stac-pydantic at finalize is the single authoritative gate).
  Failures are 4xxs before anything reaches pgstac — nothing created,
  nothing destroyed.
- **A restore point on updates**: a brokered `PUT` to an existing item
  snapshots the current stored document into the session's ledger row
  (first write wins; a document already containing staged hrefs is never
  snapshotted). If finalize later rejects the push, the snapshot is
  restored.
- **Audit**: brokered writes go through the app's guard like every BFF
  write — one `audit_log` row per mutation. Direct-path writes get none
  (ISSUES I-70).

Rules that surprise:

- **Bearer collection-create is refused (403)** — external clients do not
  create collections; that is UI/session work (ADR 0015 posture).
  Collection-level PUT/DELETE by a bearer caller are held to the same
  `externally_writable`/group precondition set as item writes.
- **A PATCH involving `staging://` hrefs is rejected (400)** — staged-asset
  updates require a full `PUT` (merge semantics would make both the
  snapshot and the finalize target ambiguous). Metadata-only PATCHes pass
  through with the precondition set, nothing more. A bearer PATCH via the
  direct path is un-snapshotted by design.
- On an `archived` collection, item writes are refused (409) while
  collection-metadata edits and deletes stay allowed.

## Rejection outcomes — what happens to the catalog document

When finalize rejects a push (`status: rejected`), the outcome depends on
the triggering operation and the path the write took:

| Case | Outcome |
|---|---|
| **Insert** (the item did not exist before the push) | finalize **deletes** the item — the catalog never retains an item whose platform assets will never exist. Prior state (absence) is restored exactly. |
| **Update, brokered** (a `prior_item` snapshot exists) | finalize **restores** the snapshot — the pre-push version returns (`result.restored: true`). Caveat: the restore can clobber a *concurrent* write that landed between snapshot and restore — the same lost-update window any racing writers already have. **The same window applies to a client PUT racing an in-flight finalize**: finalize's upsert of the rewritten document can overwrite a PUT that landed after the staged event was claimed. Serialize your own writers per item. |
| **Update, direct** (no snapshot exists — your own PUT already replaced the stored version) | finalize **leaves the stored document as-is**: a visibly **broken item requiring manual fix** (staged hrefs that dangle once the TTL sweep runs). The alert wording flags it distinctly; recovery is a corrected re-push or an operator delete. This is the direct path's sharpest edge. |

Staged bytes of a rejected upload are left to the TTL sweep (one cleaner,
not two). Operators see rejections without polling via the `push_rejected`
alert (collection-anchored; `PUSH_ALERT_LOOKBACK_SECONDS`, default 24 h).

## Rejection reasons

The closed, machine-readable `reason` vocabulary
(`PUSH_REJECTION_REASONS`, `app/src/lib/uploads/schemas.ts`; contract
fixture `tests/contract-fixtures/push-upload-status.json`). "Sync" reasons
surface as immediate 4xx `{ error, code }` responses on the brokered path
(and only in the ledger via the direct path); "async" reasons are knowable
only at finalize time and always land in the ledger + alert.

| Reason | Surfaces | Meaning |
|---|---|---|
| `multi_session` | sync 400 / ledger | item's staged hrefs reference more than one upload session |
| `unknown_session` | sync 400 / — | staged href names no ledger row (direct path: metrics + logs only — ISSUES I-78) |
| `session_terminal` | sync 409 / ledger no-op | session already finalized/rejected/expired — mint a new one |
| `wrong_collection` | sync 403 / ledger | session was minted for a different collection |
| `bound_to_other_item` | sync 409 / ledger | session already bound to a different item id |
| `gc_pending` | async (ledger) | an open `asset_gc` mark covers the item's canonical prefix (grace-window id reuse) — retryable once the mark collects |
| `collection_archived` | async (ledger) | collection archived between mint and finalize |
| `missing_bytes` | async (ledger) | a referenced staged object was never PUT, or the TTL swept it |
| `checksum_mismatch` | async (ledger) | staging→canonical copy verification failed |
| `invalid_item` | async (ledger) | stac-pydantic validation failed on the post-rewrite document |

Non-reason sync 400s (no `code`): missing/mismatched top-level
`collection`, malformed staged href grammar, the light structural check,
staged hrefs outside an item write, staged PATCH.

## The direct path (supported, discouraged)

POSTing straight to the proxy (`:8081`) with a valid token remains possible
and is what the ADR 0015 write policy gates in the auth-enforced overlay:
the token's roles must include `operator`/`admin`, and item writes are
validated against the `externally_writable` collection set (a Settings flip
takes effect within the policy's ~15 s cache TTL). Its sharper edges:

- **No pre-validation, no snapshot, no audit row** (ISSUES I-70). Staged
  items flow through the same async finalize with the **leave-broken**
  update-rejection semantics above.
- **Omitting the top-level `collection` field fails 500-shaped, not 403** —
  the upstream CQL2 `matches()` raises on a missing property. Always send
  it.
- **`bulk_items` is denied outright** — bulk push is unsupported (ISSUES
  I-76).
- Pass-through mode (default compose) enforces nothing at the proxy; the
  app-side staged-mint preconditions still apply in every mode.

## Deletes

A brokered `DELETE` is audited and marks the item's canonical prefix for GC
app-side (ADR 0011). A direct-path DELETE is covered by the dispatcher: on
claiming the delete event it marks `asset_gc` before draining (the marks are
idempotent, so the dual write is harmless). Delete events never propagate to
delivery destinations. Edge: a delete event that exhausts the dispatcher's
retry budget while the mark keeps failing is drained with a loud log and the
bytes orphan (ISSUES I-79).

## Update pushes & delivery

A finalized push upserts via pypgstac and emits an ordinary outbox event, so
delivery associations fire from it like any other item — the dispatcher
defers staged items until finalize rewrites their hrefs (no delivery ever
streams from staging, no double-fire). Update pushes are subject to each
association's `on_update` policy; a brokered-update restore re-emits the
prior version's event (checksums unchanged ⇒ `redeliver` moves nothing).

## Operating it (auth-enforced stack)

The enforced overlay requires the BFF shared secret on **both** sides —
export it before `up` (the overlay covers the proxy; the app process reads
it from the shell/`.env`):

```sh
export CATALOG_BFF_SHARED_SECRET=$(openssl rand -hex 32)   # or put it in .env
docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --build --wait
```

See [`auth.md`](auth.md) for why an unset app-side secret silently breaks UI
writes to non-externally-writable collections in enforced mode.

## Limits (honest)

- Single presigned PUT per file — no multipart, large-file ceiling (I-72).
- No rate limiting or per-group quota on push (I-71).
- Core-spec validation only — no extension-schema validation (I-73).
- Push alerts require collection ownership to be group-visible (see
  Prerequisites).
- Direct-path writes are unaudited (I-70); brokered is the mitigation.
- Replacing an item's assets with different filenames leaves the old
  canonical files until item delete/retention (I-74).
- Asset reads stay authentication-only (I-13): any authenticated caller can
  mint a download URL for any asset.

All cross-referenced in [`ISSUES.md`](ISSUES.md).
