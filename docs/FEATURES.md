# Feature catalog

What's built, grouped by delivery phase. Status legend: ✅ done · 🚧 in progress · ⬜ not started. Each area links its detailed reference doc and the ADRs that shaped it. See [`../ROADMAP.md`](../ROADMAP.md) for the plan and [`ISSUES.md`](ISSUES.md) for known gaps.

---

## STAC client application (baseline)

The Astro (SSR) + React 19 STAC client that predates the platform phases
(Astro 6 at the time; migrated to Astro 7 — see "Pre-M5 hardening" below).

| Feature | Status | Entry points |
|---|---|---|
| Collections & Items CRUD | ✅ | `app/src/pages/{collections,items}*`, forms via React Hook Form + Zod (`app/src/lib/stac-api/schemas.ts`) |
| STAC search | ✅ | `app/src/pages/search.astro` |
| Map layers | ✅ | `StacMap`, `FootprintLayer`, `ExtentLayer`, `ItemGeometryEditor` (MapLibre GL via `packages/shared/src/lib/map/`) |
| Multi-catalog management | ✅ | `app/src/pages/catalogs/index.astro`, `app/src/stores/catalogStore.ts` (localStorage; built-in catalog is undeletable). Since UI-10 each non-built-in catalog gets a read-only browser at `/catalogs/[catalogId]/collections*` instead of taking over the product pages |
| Custom STAC extensions | ✅ | `/api/extensions*` routes, RJSF theme in `packages/shared`; import/preview external JSON Schemas |
| CORS proxy | ✅ | `/api/proxy` (`X-Proxy-Target` + `X-Proxy-Endpoint`; rejects cross-site; optional `PROXY_AUTH_TOKEN`) |

State model: Nanostores (cross-island) · TanStack Query (server state, key factory `app/src/lib/query/keys.ts`) · React Hook Form + Zod (forms). See the `project-conventions` skill.

---

## Phase 0 — Foundations ✅

Local-first platform substrate; everything runs under `docker compose up`.

| Feature | Status | Entry points |
|---|---|---|
| npm-workspaces monorepo | ✅ | `app/`, `packages/shared/` (`@stac-higher/shared`), `services/pipeline/` |
| Full local stack | ✅ | `docker-compose.yml`: pgstac (:5433), stac-fastapi (:8082), stac-auth-proxy (:8081), Keycloak (:8180), MinIO (:9000/:9001), pipeline (:8083) |
| Pipeline service | ✅ | `services/pipeline/` — worker + scheduler + `/health` in one process; backend-agnostic `QueueBackend` (Procrastinate default, SQS reserved for Phase 8); no-op heartbeat proves the periodic path |
| Extension storage + migrations | ✅ | `stac_higher.*` schema in pgstac's Postgres; app-owned migrations run via middleware on first request |
| Built-in catalog | ✅ | seeded, undeletable entry pointing at the auth-proxy (`PUBLIC_BUILTIN_CATALOG_URL`) |

Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md).

---

## Phase 1 — Auth, RBAC & audit ✅

| Feature | Status | Entry points |
|---|---|---|
| OIDC login (PKCE) + claims mapping | ✅ | `app/src/lib/auth/*`, `/api/auth/{login,callback,logout,me}`; encrypted chunked session cookie |
| Dev-bypass identity | ✅ | `AUTH_MODE=bypass` (default in dev): static operator in `earth-observation` — unit tests / e2e need no IdP |
| RBAC permission guard | ✅ | `app/src/lib/authz/{permissions,guard}.ts`, wired in `src/middleware.ts`; operator/admin required for API mutations, reads stay open |
| Append-only audit log | ✅ | `stac_higher.audit_log` (trigger-enforced no UPDATE/DELETE/TRUNCATE), `app/src/lib/audit/log.ts` (redacts secrets); `/api/audit` (own-groups / admin-all) |
| Collection ownership/exposure settings | ✅ | `stac_higher.collection_settings` (sparse; unowned+public default), `app/src/lib/collections/settings.ts` |
| Auth-proxy enforcement (opt-in) | ✅ | `infra/compose.auth-enforced.yml` — authenticated transactions + audience check, reads public |

Reference: [`auth.md`](auth.md). Decisions: [ADR 0002 — proxy enforcement scope](decisions/0002-auth-proxy-enforcement.md), [ADR 0003 — pre-existing collection ownership](decisions/0003-preexisting-collections.md). Carried-forward item in [`ISSUES.md`](ISSUES.md).

---

## Cross-phase — BFF for built-in-catalog writes (ADR 0008) ✅

**Post-Phase-5 hardening — resolves I-50.** Under auth enforcement the browser cannot present a bearer
token (it lives in the httpOnly session cookie), so built-in-catalog
mutations route through `/api/catalog/[...path]`
(`app/src/pages/api/catalog/`): transaction endpoints only, writes only; the
route injects the session access token server-side and forwards to the
configured built-in catalog (`BUILTIN_CATALOG_URL` /
`PUBLIC_BUILTIN_CATALOG_URL`). `stacFetch` routes built-in writes there
unconditionally (dev pass-through included); the guard gates the paths
(operator+, `catalog_collection`/`catalog_item` resource types) so every UI
catalog mutation is audited. UI-path enforcement leg:
`tests/integration/bff-catalog-writes.test.mjs`.

---

## Phase 2 — Connections ✅

Group-owned ingest/delivery endpoints the pipeline reads from and writes to. Live-verified end-to-end (SFTP/FTP/S3 test-connections, egress block, TOFU mismatch) on 2026-07-16.

| Feature | Status | Entry points |
|---|---|---|
| Connections CRUD + Zod schemas | ✅ | `stac_higher.connections` (migration 004), `app/src/lib/connections/*`, `/api/connections*`. Anonymous s3 connections (G-1, 2026-09): `config.anonymous` → unsigned requests, no credentials required (`s3-connection-config.json` fixture) |
| Write-only credential envelope | ✅ | `0x01 ‖ 12B nonce ‖ AES-256-GCM(ct+tag)` of UTF-8 JSON; `CREDENTIALS_MASTER_KEY` (base64 32B, shared app↔pipeline). App encrypts (`crypto.ts`), pipeline decrypts (`envelope.py`); API returns only `credentials_set` |
| Protocol adapters | ✅ | `services/pipeline/.../adapters/`: s3 (boto3), sftp/ssh (asyncssh), ftp/ftps (aioftp); `StorageAdapter` ABC (`test/list/get/put/delete`); `stac-api` reserved |
| Egress SSRF policy | ✅ | `egress.py`: deny private/loopback/link-local/metadata + `EGRESS_ALLOW_HOSTS`; IP-pinning (DNS-rebind defence) + FTP PASV data-channel forced to control host |
| TOFU host-key pinning | ✅ | `adapters/tofu.py`: first-pin on success, hard-fail on mismatch; reset via `/api/connections/[id]/host-key/reset` |
| Test-connection bridge + health checks | ✅ | app inserts `connection_checks` → pipeline drain job (`* * * * *`) runs `test`; health sweep (`*/5`). Neither touches `connections.updated_at` |
| `/connections` UI | ✅ | `app/src/pages/connections.astro`, `app/src/components/connections/*`: badges, per-protocol wizard, test+poll, host-key reset |

Reference: [`connections.md`](connections.md). Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md), [ADR 0004 — app→pipeline bridge](decisions/0004-app-pipeline-bridge.md). Residuals/caveats in [`ISSUES.md`](ISSUES.md).

---

## Phase 3 — Object storage & asset service ✅

Item asset bytes live in platform object storage (MinIO locally / S3 in cloud) and are reached only through the app. Live-verified end-to-end on 2026-07-16 (upload → PUT to MinIO → asset route 302 → byte round-trip; staging TTL sweep deletes an expired upload and leaves canonical assets untouched).

| Feature | Status | Entry points |
|---|---|---|
| App storage abstraction | ✅ | `app/src/lib/storage/`: `config` (S3_* env, MinIO defaults), `keys` (§5.3 layout + path-traversal hardening), `client`, `presign` (offline GET/PUT signing), `resolve` (`resolveAssetTarget` — the `reference`-mode seam) |
| Asset access route | ✅ | `GET /api/assets/[collection]/[item]/[asset]` — auth check → 302 to presigned canonical URL (`no-store`); unauthenticated → 403. `{asset}` = filename (ADR 0005) |
| Upload presign route | ✅ | `POST /api/uploads` — operator+ (gated + audited), returns presigned PUT URLs + `/api/assets/...` hrefs; path-traversal rejected |
| Manual asset upload (flow C) | ✅ | `app/src/components/items/AssetUpload.tsx`, wired into `ItemForm.tsx` asset rows: pick file → presign → browser PUT → href written back; disabled until Item ID is set |
| Platform storage (pipeline) | ✅ | `services/pipeline/.../storage/platform.py`: egress-pinned boto3 client for the platform bucket + `cleanup_expired` |
| Staging TTL cleanup job | ✅ | `services/pipeline/.../jobs/staging_cleanup.py` (`0 * * * *`): sweeps `staging/` uploads older than `STAGING_TTL_SECONDS` (24h default); deterministic cutoff from the scheduled tick |

No new tables: the asset route derives keys from URL params; uploads derive from the request body. New deps: `@aws-sdk/client-s3`, `@aws-sdk/s3-request-presigner` (app). Decision: [ADR 0005 — asset service](decisions/0005-asset-service.md). Residuals/caveats in [`ISSUES.md`](ISSUES.md).

---

## Phase 4 — Ingest pipeline ✅

Poll-based ingest of files from source connections into built-in-catalog collections. Delivered in slices: **Slice A (app associations + Data-flow UI) done**; **Slice B (pipeline ingest chain) done** — **B1 (adapter list-metadata + `build_adapter`) done**, **B2+B3 (ingest repo + scheduler + DISCOVER/GROUP/FETCH copy-mode) done and live-verified** (2026-07-16: MinIO source file → poll → DISCOVER → GROUP → FETCH → canonical storage, byte-identical, idempotent), **B4 (EXTRACT/ITEMIZE) done** (pipeline unit tests + a DB integration test; ADR 0006), **B4a (best-effort geometry extraction + collection-extent fallback, ISSUE I-27) done** (226 pipeline unit tests, 2 skipped, after the 3D-bbox fix); a **`/simplify` quality pass** was applied across the B4/B4a slice (behavior-identical cleanups). **B5 (live end-to-end through EXTRACT/ITEMIZE) largely verified (2026-07-17)**: DB integration test, `raster_auto` e2e, netCDF, and collection-inheritance all live-verified. **Slice C (`storage_mode: reference`) done + merged, live-verified** — durably-reachable-source-only (no app decryption, no presigning of source bytes); private-source reference is deferred (ISSUES). **Live end-to-end (2026-07-20, Task 10):** a reference association ran the real scheduler poll → … → itemized (queryable `ST_Polygon` item, no canonical copy, `GET /api/assets` 302→source URL) and an SFTP copy association closed I-4 (first live SFTP `list`/`get` → canonical copy → itemize) — Phase 4's done-when is met. The in-container run found+fixed I-35 (pipeline image missing `libexpat1`).

| Feature | Status | Entry points |
|---|---|---|
| Association + ledger tables | ✅ | migration `005_ingest_associations_and_files`: `stac_higher.collection_connections` (both directions; app writes `ingest` this phase) + `stac_higher.ingest_files` ledger (app owns DDL; pipeline reads/writes rows — ADR 0001) |
| Ingest `config` Zod schema (§5.1) | ✅ | `app/src/lib/associations/schemas.ts` — cross-runtime contract (source_path, include/exclude, poll_frequency, storage_mode, grouping, metadata, post_ingest); nested defaults filled via function-defaults |
| Association CRUD API | ✅ | `GET/POST /api/collections/[id]/connections`, `GET/PUT/DELETE /api/collections/[id]/connections/[assocId]` — operator+ gated & audited; group ownership enforced in-route; `reference` mode restricted to s3 connections; duplicate (collection,connection,direction) → 409 |
| Data-flow tab (ingest half) | ✅ | `app/src/components/collections/IngestSection.tsx` + `IngestFormDialog.tsx` (extracted 2026-08-21 to mirror the delivery half), composed by `DataFlowTab.tsx` in `CollectionDetail.tsx` (built-in catalog only): add/edit ingest sources, enable/disable, remove |
| Adapter list-metadata + `build_adapter` (Slice B1) | ✅ | `services/pipeline/.../adapters/*` `list()` → `FileEntry` (path/size/mtime/etag/is_dir) across s3/sftp/ftp; `connections/build.py::build_adapter` (decrypt→adapter seam the ingest workers consume); `probe` refactored onto it |
| Ingest repo + scheduler + DISCOVER/GROUP/FETCH (Slice B2+B3) | ✅ | `services/pipeline/.../ingest/`: `IngestRepo` (+ `PgIngestRepo`, FakeRepo) mirroring `connections/repo.py`; `config.py` (Python §5.1 mirror + glob matching); `ingest_poll` scheduler (poll_frequency as N whole-minute ticks); `discover.py` settled-check state machine (size/fingerprint unchanged across 2 polls) + adapter-path normalization; `group.py` none/shared_basename + timeout; `fetch.py` copy-mode (buffered `get` → `platform.put_object` at `assets/{collection}/{item}/{filename}`, sha256 checksum); `jobs/ingest.py` chains the stages via the queue |
| EXTRACT + ITEMIZE (Slice B4) | ✅ | `services/pipeline/src/pipeline/ingest/extract.py` (`build_item` dispatcher + `raster_auto`/`sidecar`/`defaults_only` strategies, reading canonical-storage bytes via an in-memory `rasterio.MemoryFile`); `ingest/itemize.py` (`run_itemize` — re-reads `stored` ledger members, EXTRACT → stac-pydantic `validate_item` gate → pgstac upsert → post-ingest, idempotent against the ledger); `ingest/postingest.py` (`apply_post_ingest` — `leave`/`delete`/`move:<path>`, non-fatal); `stac/pgstac_writer.py` (`PgstacWriter` ABC + `PgPgstacWriter`, pypgstac `Methods.upsert`); wired into the queue as `jobs/ingest.py`'s `pipeline.ingest_itemize` task. Part of the full pipeline suite (226 passed, 2 skipped) + a DB integration test (`test_integration_itemize.py`, upsert→query→update, gated on `DATABASE_URL`). No Dockerfile change (bundled-GDAL rasterio wheels); pgstac image pinned to `v0.9.11`. [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md) |
| Best-effort geometry + collection-extent fallback (Slice B4a, ISSUE I-27) | ✅ | `services/pipeline/.../ingest/extract.py`: `is_gdal_candidate`/`GDAL_CANDIDATE_EXTS` (raster + netCDF/GRIB/Zarr/HDF/VRT/IMG), `geometry_from_raster` (best-effort GDAL open → EPSG:4326 bbox polygon, netCDF subdataset-aware, never raises), `bbox_to_polygon`; `build_item` gains `collection_fallback` and resolves geometry strategy → best-effort GDAL → opt-in collection extent → fail-fast (`ExtractError`, never a null-geometry item); `properties["stac_higher:geometry_source"]` provenance. `ingest/itemize.py::_build_collection_fallback` reads `PgstacWriter.get_collection_bbox` when `metadata.defaults.geometry == "collection"`, degrading to a `global_fallback` world polygon for an unset/global extent. Cross-runtime opt-in: `app/src/lib/associations/schemas.ts` `metadataSchema.defaults.geometry`. **RESOLVED ISSUE I-27.** 226 pipeline unit tests, 2 skipped (up from 201; includes a 3D-bbox fix + a `/simplify` cleanup pass, behavior-identical) |
| `storage_mode: reference` (Slice C) | ✅ | Migration `006`: `ingest_files.source_href` (nullable). Schema guard: `ingestConfigSchema` (`app/src/lib/associations/schemas.ts`) rejects `post_ingest` delete/move when `storage_mode: reference` (source bytes are the catalog's asset). Pipeline: `S3Adapter.public_object_url` (credential-free stable URL, `connections/adapters/s3.py`); FETCH reference branch (`ingest/fetch.py` — records `source_href`, no copy, ledger settled→stored); GROUP forms groups identically to copy mode (`ingest/group.py`); EXTRACT byte-source seam (`MemberByteSource`/`CanonicalByteSource`/`SourceAdapterByteSource` in `ingest/extract.py`) so `build_item` reads source bytes without a canonical copy; ITEMIZE unchanged; `post_ingest` skips destructive actions in reference mode as defense-in-depth (`ingest/postingest.py`). App: `resolveAssetTarget` (`app/src/lib/storage/resolve.ts`) branches via `lookupReferenceHref` (`app/src/lib/storage/reference.ts`) to 302 straight to `source_href` — no presigning, no decryption, preserving the "app never decrypts" invariant (`crypto.ts`). **Durably-reachable sources only**; private-source reference is deferred (ISSUES). **Live-verified end-to-end (2026-07-20):** real scheduler-driven reference run → queryable item with no canonical copy, `GET /api/assets` 302→`source_href` (byte-identical on follow); plus an SFTP copy run (I-4). |

No new client deps in Slice A. Slice B1/B2+B3 added no deps (stdlib only). Slice B4 adds pipeline deps: `rio-stac`, `pystac`, `rasterio`, `defusedxml`, `stac-pydantic`, `pypgstac[psycopg]` (§ ADR 0006). Slice B4a adds no new deps — the bundled GDAL 3.12.1 (rasterio 1.5 wheel) already carries the netCDF/HDF5/GRIB/Zarr drivers it uses. Slice C adds no new deps. Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md), [ADR 0005 — asset service](decisions/0005-asset-service.md) (the `resolveAssetTarget` seam Slice C branches), [ADR 0006 — ingest metadata + pgstac upsert](decisions/0006-ingest-metadata-and-upsert.md). Residuals in [`ISSUES.md`](ISSUES.md) (I-17, I-18, I-19, I-20, I-22 through I-26, I-28 through I-31, I-32 through I-34; I-21, I-27, I-35 resolved).

---

## Phase 5 — Delivery pipeline ✅

**Slice A — event outbox + dispatcher skeleton** (done, live-verified). The
event-driven bridge from catalog changes to delivery, matching only (no byte
transfer yet). Entry points:

- **Event outbox** — `stac_higher.item_events` + a row-level trigger on
  `pgstac.items` (`app/src/lib/db/migrate.ts`, migration `007_item_events_outbox`):
  one durable row per item change + a payload-less `NOTIFY item_events`.
  Ownership + mechanism rationale in [ADR 0007](decisions/0007-outbox-trigger-ownership.md).
- **Dispatcher** — `services/pipeline/src/pipeline/dispatcher/`: `repo.py`
  (`DispatchRepo`/`PgDispatchRepo` — claim/mark outbox rows, read deliver
  associations, `pgstac.get_item`), `loop.py` (`dispatch_once`: outbox drain →
  match → log). Registered as the poll-driven `pipeline.dispatch_poll` tick
  (`jobs/dispatch.py`, wired in `main.py`).
- **Delivery matching** — `services/pipeline/src/pipeline/delivery/matcher.py`:
  pure `match_item` applying CQL2 `item_filter` (via the `cql2` bindings,
  hardened against filters referencing absent properties) + `asset_keys`.
- **Delivery config contract (§5.1)** — Zod `deliveryConfigSchema`
  (`app/src/lib/associations/schemas.ts`) mirrored by
  `services/pipeline/src/pipeline/delivery/config.py`; the association create
  route (`/api/collections/[id]/connections`) is a direction-discriminated union,
  so delivery associations are creatable (operator+, group-owned).

**Slice B-i — delivery worker** (done, live-verified 2026-07-21). The byte-moving
core: an item change now lands its canonical asset bytes on an S3/MinIO
destination at a templated path, recorded in `delivery_log`. Entry points:

- **`delivery_log`** — `stac_higher.delivery_log` (migration `008_delivery_log`,
  app-owned DDL): one row per (association, item), `UNIQUE(association_id, item_id)`
  — the idempotency key a redelivery UPSERTs.
- **Path templates** — `services/pipeline/src/pipeline/delivery/path.py`
  (`render_path`): pure renderer over `{collection} {item_id} {filename} {yyyy}
  {mm} {dd}`; date tokens resolve from the item's `datetime`→`start_datetime`
  **in UTC**, and a date-token template against a date-less item fails loudly.
- **Atomic visibility** — `StorageAdapter.move()` + `put_atomic()`
  (`connections/adapters/`): SFTP/FTP `move` = server-side rename, S3 `move` =
  copy+delete; `put_atomic` writes `.part` then moves, but `S3Adapter` overrides
  it to a direct atomic PUT.
- **Delivery worker** — `services/pipeline/src/pipeline/delivery/worker.py`
  (`deliver_item`) + `repo.py` (`DeliveryRepo`/`PgDeliveryRepo` — `delivery_log`
  transitions + destination-target load): reads canonical bytes
  (`platform.get_object`), renders the dest path, `put_atomic`s to the
  destination, records `delivered`/`bytes`; a per-item failure marks `failed`
  and does not abort the batch (retry is B-iii).
- **Dispatcher fan-out** — `dispatcher/loop.py` `dispatch_once` now groups matches
  into one batched `pipeline.deliver` job **per association** (ROADMAP §1/§6.4),
  enqueues **before** draining the outbox (at-least-once), and the `deliver`
  handler (`jobs/dispatch.py`) loads the destination + runs each item through
  `deliver_item`.

Live-verified end-to-end (real code vs live pgstac + MinIO, 16/16 checks): a real
pypgstac upsert fired the outbox trigger → `dispatch_once` matched the deliver
association → `deliver_item` copied the canonical asset to the MinIO destination
at the rendered key **byte-identical** with a `delivered` `delivery_log` row
(`attempts=1`); a changed re-upsert redelivered into the **same** row
(`attempts=2`, overwrite byte-identical); and a delete drained with **no**
delivery (deletions never propagate). Live finding: via **pypgstac
`Loader.load_items(upsert)`** the outbox op is `insert` (new) / `update` (changed,
a single row — not delete+insert) / no-op (identical) / `delete` — the
transaction-API delete+insert of [ADR 0007](decisions/0007-outbox-trigger-ownership.md)
is a different write path; both are benign for delivery (see ISSUES).

**Slice B-ii — payloads, policies, reference source, S3→S3 copy** (done;
live-verified 23/23 on 2026-07-22 vs real pgstac + MinIO). Entry points:

- **`delivered_assets`** — migration `009_delivery_log_delivered_assets`
  (`app/src/lib/db/migrate.ts`) adds `delivery_log.delivered_assets` jsonb: a
  per-asset `{fingerprint, size, filename}` map. Fingerprints are
  `sha256:<hex>` for streamed bytes or `etag:<etag>/<size>` for a server-side
  copy; the two kinds compare unequal, so switching an association's transfer
  path costs at most one redundant redeliver (see [`ISSUES.md`](ISSUES.md)
  I-47). `upsert_pending`'s redelivery conflict branch now resets
  `attempts = 0` — **resolves I-44**.
- **Policy enforcement** — `services/pipeline/src/pipeline/delivery/worker.py`
  (`deliver_item`): an item-level `on_update` gate (`ignore` fires once per
  item, keyed off a prior `delivery_log` row's status, never the outbox
  `op` — I-37) and a per-asset `overwrite` policy (`never`/`always`/`if_newer`
  compared against `delivered_assets`, no destination round-trip).
- **Payload sidecars** — `services/pipeline/src/pipeline/delivery/payload.py`:
  a coreutils-format checksum per written asset (`{filename}.{algo}`), the
  item JSON rewritten on every processed event (`{item_id}.json`), and a
  completion marker (`{item_id}.done`, a JSON manifest) written **last** and
  only when something was actually written.
- **Reference-mode source** — the worker reads reference-mode asset bytes
  through the ingest source connection's adapter: ledger-first lookup
  (`DeliveryRepo.load_reference_sources` over `ingest_files`), the adapter
  built lazily per connection (`build_adapter`, decrypting only when invoked)
  and cached per item — no HTTP client involved.
- **S3→S3 server-side copy** — `services/pipeline/src/pipeline/delivery/transfer.py`
  (`can_server_side_copy`): true for an s3 destination whose endpoint
  normalizes equal to the platform's `STAGING_S3_ENDPOINT` (both `None` means
  real AWS); a malformed endpoint degrades to streaming. Gated once per job in
  `jobs/dispatch.py`; `adapter.copy_object_from` performs the `CopyObject`, and
  a copy failure logs a warning and falls back to streaming. A `sha256`
  payload checksum forces streaming (there's no hash without the bytes); `md5`
  can ride a single-part object's ETag (`platform.head_object`), but a
  multipart ETag isn't an md5 and falls back to streaming too.

**Slice B-iii — retry, dead-letter, crash recovery, concurrency** (done,
live-verified 2026-07-25). Migration `011_retry_deadletter_and_claims`;
delivery retry sweep → dead-letter at `retry.max_attempts`
(`jobs/dispatch.py` `delivery_retry_sweep`); ingest crash-recovery sweeps
(I-52, `ingest/scheduler.py`); atomic outbox claims (`item_events.claimed_at`,
I-40); per-event dispatcher isolation (I-39); per-connection concurrency caps
(S3 bounded-gather; SFTP/FTP serial); SFTP/FTP `put` parent-dir creation.
Live: SFTP + FTP destination delivery (I-45), attempts 1→5 → `dead`,
dead-destination recovery, ledger recovery sweeps (evidence in ROADMAP §9).

**Slice C — NOTIFY-woken low latency + user-initiated backfill** (done,
live-verified 2026-07-25: 89 ms NOTIFY→dispatch, backfill bridge exercised).
Entry points:

- **NOTIFY listener** — `services/pipeline/src/pipeline/dispatcher/listener.py`
  (`run_dispatch_listener`): a dedicated `LISTEN item_events` connection wakes
  a drain-until-empty dispatch (`loop.py` `dispatch_until_empty`) per
  notification — the **primary** wake path (single-digit-second latency),
  run by `main.py` alongside the worker; the minute `dispatch_poll` cron stays
  as fallback. Reconnects with backoff; a catch-up wake fires on every
  (re)connect. Single-instance assumption documented in the module (I-40).
- **I-38 bounded visibility retry** — migration
  `012_dispatch_retry_and_backfills` adds `item_events.dispatch_attempts` +
  `next_dispatch_at`; `dispatch_once` releases a claimed event whose item is
  not yet visible (cool-off keeps it out of the claim window) up to
  `MAX_VISIBILITY_ATTEMPTS` before draining loudly — **resolves I-38**.
- **Backfill bridge** — `stac_higher.delivery_backfills` (migration 012;
  ADR 0004 bridge pattern): `POST
  /api/collections/[id]/connections/[assocId]/backfill` (operator+,
  group-owned, audited `backfill`, 409 on duplicates/disabled) inserts a
  'queued' row; `GET .../backfills/[backfillId]` polls it
  (`app/src/lib/associations/backfills.ts`). The duplicate 409 is
  database-enforced: migration 013's partial unique index is the
  `ON CONFLICT` arbiter, so concurrent requests cannot stack bulk work.
- **Backfill sweep** — `services/pipeline/src/pipeline/delivery/backfill.py`
  (`run_backfill`: page pgstac item ids by cursor → chunked bulk
  `pipeline.deliver` jobs, progress per chunk, stale-running crash resume) +
  `jobs/backfill.py` (`pipeline.delivery_backfill_sweep`, minute cron).

**Slice D — Data-flow tab delivery half (UI)** (done). Entry points:

- **Delivery section** — `app/src/components/collections/DeliverySection.tsx`
  (cards per deliver association: §5.1 config summary, enable/disable,
  backfill request + poll via the Slice C endpoints, delivery status panel
  with per-status counts and recent `delivery_log` rows, redeliver on dead
  rows) + `DeliveryFormDialog.tsx` (create/edit: path template, CQL2 item
  filter, asset keys, payload toggles, `on_update`, `overwrite`, retry,
  concurrency) + the shared `AssociationDeleteDialog.tsx` (ADR 0009 impact
  preview, now used by both halves). `DataFlowTab.tsx` renders both halves.
- **Delivery status API** — `GET
  /api/collections/[id]/connections/[assocId]/deliveries` (member+ with
  association visibility): recent rows + zero-filled per-status counts
  (`app/src/lib/associations/deliveries.ts`). Attempts are surfaced as
  **per-cycle** counts (I-44 semantics — a new item event or a redeliver
  starts a fresh cycle), and the UI labels them that way.
- **Redeliver (dead-letter recovery)** — `POST
  .../deliveries/[deliveryId]/redeliver` (operator+, audited `redeliver`):
  flips a `dead` row to `failed` with `attempts = 0` and a due
  `next_attempt_at`; the pipeline's retry sweep requeues it (ADR 0004 bridge
  pattern — the app never enqueues jobs). Status-guarded UPDATE, so a
  concurrent flip cannot double-fire.
- **Query layer** — `useDeliveries` (15s poll), `useRedeliver`,
  `useRequestBackfill` + `useBackfill` (3s poll to terminal status) in
  `app/src/lib/associations/queries.ts`; client functions in `api.ts`.
- Ride-alongs: FTP `root_path` help text in the connection form (B-iii live
  finding — non-chrooting servers need the absolute path). (`EmptyState`'s
  once-required `icon` prop, which crashed both empty Data-flow halves when
  omitted, has since been made optional with an Inbox default — 2026-08-21.)

Residuals in [`ISSUES.md`](ISSUES.md): I-36, I-40 through I-43, I-46 through I-48 (I-37, I-38, I-39, I-44, I-45, I-49 resolved — archived).

---

## Phase 6 — Operable platform (M2) ✅

Scope + slices: `docs/superpowers/specs/2026-08-18-m2-operable-platform-design.md`.
**All slices M2-0…M2-I complete. The M2-I rehearsal (2026-08-28) closed both
done-when legs live on the auth-enforced stack — evidence under the M2
milestone in ROADMAP §9 — and the phase promoted to `main`.**

- **M2-0 · deliver pre-record durability** — INSERT-only `pre_record` ahead of
  everything fallible in the deliver job, failure recording for config/adapter
  errors, and `sweep_stalled_deliveries` stall recovery. Story: ISSUES I-56.
- **M2-A · flow telemetry substrate** — the pipeline now writes
  `collection_connections.flow_stats` (§6.6): ingest settle/itemize bump
  cumulative `files`/`bytes`/`items`/`failed` + `last_activity_at` /
  `last_error_at` / `last_latency_seconds` (`pipeline/flow/stats.py`, hooks in
  `jobs/ingest.py`), and every `delivery_log` status transition maintains a
  live per-status `counts` snapshot **in the same transaction**
  (`delivery/repo.py`), seeded from the log on first write.
  `listDeliveries` serves its counts from the snapshot instead of aggregating
  the whole log (legacy aggregate kept only as the pre-seed fallback), and the
  app's redeliver flip carries its own dead→failed counter delta in one
  statement. The §5.1 `expectation` is now direction-aware
  (`expect_activity_within_seconds` / `deliver_within_seconds`) and editable
  in **both** Data-flow halves; golden fixtures
  `tests/contract-fixtures/{ingest,delivery}-expectation.json` pin the
  cross-runtime shape against `pipeline/flow/expectation.py`.
- **M2-B · alerts core** — migration **014** (`alerts` +
  `notification_channels`; the spec's numbering shifted by one after the CI
  slice took 013) with an open-alert dedup partial unique index on
  `(source, kind, connection, association)`. The pipeline's periodic
  `pipeline.flow_monitor` (`pipeline/flow/monitor.py` + `flow/repo.py`,
  `jobs/monitor.py`) reconciles the three §6.6 sources each minute — `flow`
  (declared §5.1 expectations vs the M2-A `flow_stats` rollup: ingest
  inactivity, delivery SLO incl. outstanding-late rows), `health` (connections
  in `status='error'`), `job_failure` (dead deliveries, retry-cap ingest
  failures, latest-backfill-failed) — raising `firing` rows, bumping
  `last_seen` on re-observation (acknowledged rows keep tracking), and
  auto-resolving its OWN kinds when the condition clears. App surface:
  `GET /api/alerts` (member+, group-scoped via the alert's connection) and
  audited operator+ `POST /api/alerts/[id]/ack` / `.../resolve`
  (state-guarded; a manual resolve with the condition still true re-fires as
  a NEW row, which is what re-notifies). Notification fan-out is M2-C.
- **M2-C · notification channels** ([ADR 0010](decisions/0010-alerting-notifications.md)) —
  migration **015**: `notification_deliveries` (the per-(alert, channel)
  webhook ledger), `alert_reads` (per-user read watermark),
  `alerts.channel_id` + `notified_at`, and the dedup index/CHECK grown to the
  channel leg (the pipeline's `sync_alerts` conflict target moved in
  lockstep). App: per-group channel CRUD at `/api/channels*`
  (`lib/notifications/`, operator+ mutations audited as
  `notification_channel`, webhook secret write-only → `has_secret`), plus the
  in-app read state (`GET /api/alerts/unread`, `POST /api/alerts/read` —
  member+, ungated). Pipeline: `pipeline/notify/` — the minute
  `pipeline.notify_sweep` fans firing un-notified alerts out to the owning
  group's webhook channels (never through the channel an alert is about) and
  drives ledger retry/stall recovery; `pipeline.webhook_notify` claims a row
  and POSTs `{"event":"alert.firing","alert":{…}}` through the connections
  egress policy (`resolve_pinned`, pinned-IP dial, HMAC-SHA256
  `X-StacHigher-Signature` when a secret is set); terminal failure
  dead-letters and raises a channel-anchored `webhook_failed` alert,
  auto-resolved by the next successful delivery. Webhook `config` contract
  fixture: `tests/contract-fixtures/webhook-channel-config.json`. In-app
  needs no dispatch — the alerts row + watermark IS the delivery (bell UI is
  M2-D). Email is a deferred third `kind`.
- **M2-D · `/monitoring` page + header alert bell** (spec §7) — one island
  (`components/monitoring/`) with three cards: **Alerts** (open/resolved
  views, operator ack/resolve with audited verbs, auto-advances the M2-C read
  watermark on open), **Data flows** (`GET /api/monitoring/flows`, the new
  cross-collection association list — direction, connection health,
  files/items/bytes, activity recency, delivery latency + per-status counts,
  and a late/on-time hint for the declared §5.1 window derived from the open
  alerts list — the monitor's own verdict, withheld when the alerts query
  has no data or its page is full; rows link to
  the collection's Data-flow tab), and **Notification channels** (list /
  add / remove, webhook secret never displayed). Header gains the
  **AlertBell** (30s-polled unread firing count, badge links to
  /monitoring) and a Monitoring nav link. e2e: `app/e2e/monitoring.spec.ts`
  (nav + bell, card render, resolved view, webhook channel create/delete
  round-trip).

- **M2-E · collection Settings tab** (spec §7) — a **Settings** tab on
  built-in-catalog collection pages (`SettingsTab.tsx`) finally exposing the
  migration-003 columns: owning group (ADR 0003 rules — unowned/public by
  default, transfers must target one of the caller's groups, admin excepted),
  `externally_writable`, `retention_days` (empty = keep forever),
  `gc_grace_days`, and `archived` (migration **016** adds the column; ADR
  0009's state — declarative until M2-F's GC honors it, and the UI copy says
  so). API: `GET`/`PUT /api/collections/[id]/settings` (PUT operator+, guard-
  audited as `collection_settings`, group rules via `canManageCollection`).
  e2e: `collection-settings.spec.ts` (persist-across-reload + client-side
  validation). The M2-F dry-run preview lands with the deletion jobs.

- **M2-F · retention & GC** ([ADR 0011](decisions/0011-retention-gc.md), closes
  I-51's GC half) — migration **017**: `stac_higher.asset_gc`, the single
  marked-then-collected queue (key-PREFIX marks; open-key unique index makes
  re-marking idempotent). Pipeline `pipeline/gc/`: `retention_gc` (five-minute
  sweep; mark-first-then-delete per expired item, archive expires everything)
  and `asset_collect` (`delete_prefix` under the platform bucket once the
  grace passes; errors keep the mark open). App: BFF deletes mark item /
  collection prefixes best-effort after upstream success; archived collections
  refuse item writes (409) and new associations (409); the Settings tab shows
  the counted dry-run (`/api/collections/[id]/settings/impact`) in a
  warn-and-proceed dialog before enabling/tightening retention or archiving.
  Reference-mode association delete now removes its items (dialog counts them
  and suggests disable-instead-of-delete).

- **M2-G · high-volume table hygiene** ([ADR 0012](decisions/0012-table-hygiene.md),
  amends I-36/I-11, closes I-12) — migration **018** partitions `item_events`
  + `audit_log` monthly via attach-don't-copy (legacy partition bounded at
  the migration month; PKs grow the partition key; audit append-only row
  triggers on the parent, TRUNCATE guard parent-level — partition DETACH+DROP
  is the sanctioned retention op). `RECONCILE_PARTITIONS_SQL` provisions two
  months ahead on every `runMigrations()`. Pipeline `pipeline/history/`:
  hourly `history_retention` sweep (checks age-out + stranded-running flip;
  soft-deleted-association ledger rows; itemless terminal deliveries).
  Migration validated end-to-end against a real Postgres (fresh AND with
  live rows via the dev DB e2e run).

- **M2-H · service telemetry** (spec §8) — `GET :8083/metrics` (Prometheus
  exposition, dedicated registry in `pipeline/metrics.py`; `prometheus-client`
  is the one new dependency). Every queue task/periodic tick is instrumented
  centrally at Procrastinate registration (`pipeline_job_runs_total` /
  `pipeline_job_seconds`); ingest stage counters ride the M2-A flow-stats
  hooks; delivery terminal transitions count outcomes/bytes/latency; webhook
  and alert counters land in the notify/monitor jobs. Structured-logging
  consistency pass: the two remaining %-style call sites (delivery matcher)
  moved to `extra={}` fields — the codebase now logs data exclusively as
  structured fields.

Residuals in [`ISSUES.md`](ISSUES.md): I-58 (M2-C notification semantics), I-59 (M2-F GC residuals); I-56 resolved (archived). Migration numbering note: the spec's 013/014/015 landed as 014–018 on disk after the CI slice took 013.

---

## Cross-phase — CI/CD (GitHub Actions, 2026-08-18) ✅

| Feature | Status | Entry points |
|---|---|---|
| CI gate | ✅ | `.github/workflows/ci.yml` — app job (the same app-scoped `astro check` + build + vitest that root `npm run verify` runs locally; aligned 2026-08-21 after the M2 promotion exposed the gap), pipeline job (ruff + pytest incl. the pgstac DB integration tests), Storybook build, and a Playwright e2e job against the compose stack |
| Security scanning | ✅ | `.github/workflows/security.yml` (npm audit — high-severity gate on prod deps since the Astro 7 upgrade resolved I-57 — plus gitleaks), `codeql.yml`, Trivy in `containers.yml` |
| Container images + releases | ✅ | `containers.yml` → GHCR images for the app and pipeline; `release.yml` |

---

## Cross-phase — Pre-M5 hardening (2026-08-29) ✅

| Feature | Status | Entry points |
|---|---|---|
| Astro 6 → 7 migration | ✅ | astro 7.2.9 / @astrojs/node 11.1.4 / @astrojs/react 6.0.4; zero source changes (verify + full e2e green). Resolves ISSUES I-57 (6.x XSS/sharp/node advisories); the `security.yml` npm-audit gate tightened critical→high. Gotcha documented in `AGENTS.md`: Astro 7 auto-daemonizes `astro dev` under AI agents — `ASTRO_DEV_BACKGROUND` in `app/playwright.config.ts` keeps the e2e webServer foreground. I-8 amended (root check no longer OOMs, still app-scoped-only) |
| OGC API serving (titiler-pgstac + tipg) | ✅ | `docs/serving.md`. Compose services `titiler` (:8084, per-collection raster tiles off pgstac) + `tipg` (:8085, vector Features/Tiles); migration `019` `collection_settings.serving_enabled`; Settings-tab toggle (operator+, audited) advertising the endpoints — link-level only until I-1 (ISSUES I-69); the tiler's cloud story for app-relative `/api/assets` hrefs is still a Phase 8 decision (ISSUES I-68). e2e: `collection-settings.spec.ts` serving-toggle spec. G-4 (2026-09): derived titiler image (`infra/titiler/`) maps `/api/assets` hrefs → `s3://` in both readers so canonical assets tile locally; compose builds it, CI build-verifies it |

---

## Phase 9 — Processes (M5) ✅ (gate met 2026-08-31)

Design spec approved 2026-08-29
(`docs/superpowers/specs/2026-08-29-phase9-processes-design.md`, slices
M5-0…M5-G + an OGC API — Processes stretch); ADRs 0013/0014 accepted;
I-60…I-66 settled. Implementation started 2026-08-30, after Phase 7's gate
(steering order, ROADMAP §9); **M5-G gate met 2026-08-31** — the §1 done-when
rehearsed live on the auth-enforced stack, evidence in ROADMAP §9 Phase 9.
Process-author contract: [`processes.md`](processes.md).

| Feature | Status | Entry points |
|---|---|---|
| Process data model (migrations 022/023) | ✅ | `app/src/lib/db/migrate.ts` — `processes`, `process_revisions`, `process_sources`, `process_outputs`, `process_runs`, `process_checks` (022) + `flow_stats_daily` (023). App-owned DDL (ADR 0001); the pipeline writes run state, source `flow_stats` and the daily rollup, never DDL. `process_runs` deliberately unpartitioned (ADR 0012 criteria — runs are `rerun` targets, the `delivery_log` argument); shape pinned by `app/src/__tests__/processes-migration.test.ts` |
| §5.6 cross-runtime shapes | ✅ | Zod write gate `app/src/lib/processes/schemas.ts` ↔ lenient reader `services/pipeline/src/pipeline/process/config.py`; golden fixtures `tests/contract-fixtures/process-{trigger,runtime,env,expectation}.json`, both suites consuming |
| `container` runtime refusal (slice 1) | ✅ | `processRuntimeWriteSchema` — the contract CARRIES the arm (pipeline parses it) while the app's write gate refuses it; user-supplied images stay out of the first accreditation scope (spec §4, ADR 0013). Pinned as `app: reject` / `pipeline: accept` in the runtime fixture |
| Process alert kinds declared | ✅ | `process_stalled` / `process_failed` / `process_rate_limited` added to the enum + `ALERT_KIND_LABEL`, and to the fixture's new `declared_kinds` list — declared and labelled, deliberately **unowned**. `MONITOR_KINDS` membership grants auto-resolve authority, so M5-E claims each kind for the writer that raises it (`process_rate_limited` is an enqueue-time event like the notify-owned `webhook_failed`); the partition test forces that to be a decision |
| App CRUD + editor UI (M5-A) | ✅ | `/api/processes*` (9 routes) over `app/src/lib/processes/{storage,access,api,queries,schemas}.ts`; `/processes` + `/processes/[id]` islands (`app/src/components/processes/`) with the **plain-textarea** editor (P9-C — no editor dependency in slice 1). Deploy is one transaction (insert revision + repoint `current_revision`); test runs go through the ADR 0004 `process_checks` bridge, so the app never touches the executor. Gates + audit actions (`deploy`, `test`) in `lib/authz/permissions.ts`; group ownership in-route |
| Executor + run-scoped creds (M5-B) | ✅ | `Executor` seam (`pipeline/process/executor.py`) + `DockerExecutor` over a least-privilege `docker-socket-proxy` (compose; `CONTAINERS=1 POST=1`), `MemoryExecutor` fake for handler tests. Per-run `Memory`/`NetworkMode`/`CapDrop`/`no-new-privileges` at create time, executor-enforced timeout kill, always-reap, plus the `pipeline.process_reap` leg (`process/reaper.py`, M3-W-1) that reconciles containers found by the `stac-higher.run-id` label against the ledger — listing first, ledger second — for the crash that skips the `finally`. STS run-scoped credentials bounded to `staging/runs/{run_id}/` with **no fallback to platform keys** (`process/credentials.py`); capped log capture to `logs/runs/{process_id}/{run_id}.log`, object before row (`process/logs.py`). Platform image: `services/process-runtime/`. Startup self-check refuses a raw-socket `DOCKER_HOST` |
| Triggers, runs, rate ceiling (M5-C) | ✅ | Dispatcher leg (`process/matcher.py` + the `enqueue_process_runs` hook in `dispatcher/loop.py`) batching one run per SOURCE per tick; cron leg (`process/cron.py`, evaluated per tick because sources are user data the periodic registry cannot hold); run ledger + claim/finish/stall SQL (`process/repo.py`, `process/sweep.py`); §7 rate ceiling with defer-and-coalesce (`process/rate.py`, `process/trigger.py` — a partial unique index makes coalescing true under concurrent dispatch); `process/runner.py` joins the executor to the ledger. Jobs: `pipeline.process_{trigger,run_tick,cron,sweep}`. App: `/api/processes/[id]/runs` + the audited `rerun` verb, and the runs panel on `/processes/[id]`. **Env + secrets (M3-W-2)**: the §5.6 `env` editor on `/processes/[id]` (`components/processes/EnvEditor.tsx` — literal rows or a `secret_ref` picker over the process group's connections, keys derived from `CREDENTIAL_KEYS`), and the deploy route re-checks every `secret_ref` against the PROCESS's group (`findUnresolvableSecretRef`). The connection-scoped secret namespace is the documented limit, not a gap — `docs/processes.md` |
| Output path via finalize (M5-D) | ✅ | `finalize/process_run.py` — the resolver/recorder pair the Phase 7 seam left as `NotImplementedError`. Outputs are DISCOVERED from the run's staging prefix (user code decides what it wrote), asset hrefs are sibling filenames, and the **neutral steps were not touched** — the ADR 0014 check criterion is now closed with the real hooks, not a stand-in. Rejection semantics are the process's own (I-80): a rejected output was never in the catalog, so nothing is deleted or restored. Contract for process authors: [`docs/processes.md`](processes.md) |
| Cycle refusal (I-64) | ✅ | `app/src/lib/graph/{edges,storage}.ts` — one edge model shared by the write gate and (in M5-E) `/api/monitoring/graph`. Attaching a source, attaching an output, or RE-ENABLING a source is refused with the path when it would close a loop. Traversal is collection↔process only: a path through a connection is not statically decidable, so delivery→re-ingest stays permitted with the §7 ceiling as its backstop |
| Monitoring + graph (M5-E) | ✅ | The three process kinds claimed by the monitor (each an observed CONDITION, so auto-resolve falls out of absence): `process_stalled` per-SOURCE (I-63), `process_failed` and `process_rate_limited` per-process. Migration **024** adds both alert anchors in the migration-021 lockstep — CHECK, dedup index, BOTH pipeline `ON CONFLICT` targets **and** the auto-resolve comparison. `flow_stats_daily` daily rollup (`flow/daily{,_repo}.py`, delta-not-cumulative, ~400-day prune). `/api/monitoring/graph` (member+ scoped, shares `lib/graph/*` with the M5-D cycle check). `/metrics`: process run outcomes, duration, output items, rate deferrals |
| UI completion (M5-F) | ✅ | `/graph` pipeline view (columns, not a force-directed blob — the graph IS a pipeline; health joined client-side from the open-alert list like M2-D). Collection **lineage panel** in the Data-flow tab, derived from `/api/monitoring/graph` so panel and graph cannot disagree. `FlowStrip` 30-day strip (inline SVG, no charting dep) on the lineage panel and as a `/processes` sparkline. Run **log viewer** (302 to a presigned URL; the app never streams 10 MB of untrusted output). Dashboard **platform rollup**, which renders nothing when there is no platform surface. e2e: `app/e2e/processes.spec.ts` |

---

## Phase 7 — Direct interaction (push ingest) ✅ (gate met 2026-08-30)

All implementation slices P7-B…P7-I merged and the **P7-Z live gate closed**
2026-08-30 (auth-enforced stack: real token → presigned PUT → brokered item
POST → finalize → exactly-once delivery; rejection tiers + `push_rejected`
alert; integration 14/14 non-skipped, e2e 32/32 — evidence in ROADMAP §9
Phase 7; two gate findings fixed on the spot, ISSUES I-80). Remaining
human work: the promotion PR and review of the provisionally-approved
design spec
(`docs/superpowers/specs/2026-08-29-phase7-push-ingest-design.md`).
Client docs: [`push-ingest.md`](push-ingest.md).

| Feature | Status | Entry points |
|---|---|---|
| Bearer-token auth on `/api/*` (P7-B) | ✅ | `app/src/lib/auth/bearer.ts` + `src/middleware.ts` (`resolveRequestAuth`: session wins, bearer only for anonymous `/api/*` in oidc mode, degrade-to-anonymous); JWKS via the cached discovery; same claims mapper → normal `CanonicalIdentity`; `locals.bearerToken` set for bearer identities. Dev realm client `stac-higher-push` (client-credentials). [`auth.md`](auth.md) "Bearer tokens" |
| Staged-upload mint + ledger + poll (P7-C) | ✅ | Migration **020** `stac_higher.staged_uploads`; `POST /api/uploads` staged mode (body without `item` → presigns into `staging/{upload_id}/`, `staging://` hrefs, `expires_at` = the single ledger clock); `GET /api/uploads/[uploadId]` (admin/group/creator; else 404); `app/src/lib/uploads/`; fixtures `staged-asset-href.json` + `push-upload-status.json` (incl. the pinned `PUSH_REJECTION_REASONS` set) |
| Brokered push write path (P7-D) | ✅ | `app/src/pages/api/catalog/[...path].ts` — bearer callers forward their own token; §4.1 precondition set on EVERY bearer write; synchronous staged pre-validation (`app/src/lib/push/prevalidate.ts` — Tier 0 4xxs before pgstac); `prior_item` snapshot on PUT (first-write-wins in SQL, never a staged doc); staged PATCH → 400; bearer collection-create → 403; `X-BFF-Auth` when `CATALOG_BFF_SHARED_SECRET` is set (`lib/push/config.ts`). Session callers byte-identical |
| Finalize (P7-E, ADR 0014 seam) | ✅ | `services/pipeline/src/pipeline/finalize/` (seam/steps/push resolver+recorder/repo/status/store/sweep) — validate (stac-pydantic via the lifted `pipeline/stac/validate.py`, the ITEMIZE gate) → checksum → server-side `copy_object` staging→canonical → rewrite hrefs → pypgstac upsert, **no producer branching in the steps** (pinned by behavioral + structural tests); jobs `pipeline.finalize` + 5-min `finalize_sweep` (stale claims, TTL expiry); §6.3 tiered rejection outcomes (insert-delete / brokered-restore / direct-leave-broken); `pipeline_finalize_items_total{producer,outcome}` + bytes counter |
| Dispatcher staged-gating + delete GC (P7-F) | ✅ | `pipeline/dispatcher/loop.py`: staged-href items enqueue finalize (before drain) and never match delivery — no double-fire on the rewrite; delete events mark `asset_gc` before draining (direct-path deletes join ADR 0011; transient mark failures take the I-38 defer path) |
| Proxy write policy (P7-G, ADR 0015) | ✅ | `services/proxy-policy/` (`ExternallyWritableItemsFilter`: role floor, `externally_writable` IN-set w/ 15s TTL cache, `POST /search` carve-out, `bulk_items` denied, `X-BFF-Auth` exemption — mandatory constant-time secret); derived image `infra/proxy-policy/Dockerfile`; enforced overlay only; pins auth-proxy v1.2.0 + stac-fastapi-pgstac 6.3.1; integration policy legs in `tests/integration/` (skip without the stack) |
| `push_rejected` alerting + hygiene (P7-H) | ✅ | Migration **021** (`alerts.collection_id` anchor + CHECK fourth leg + widened dedup index, both pipeline ON CONFLICT sites in lockstep); `MONITOR_KINDS` gains `push_rejected` (`evaluate_push_rejections`, `PUSH_ALERT_LOOKBACK_SECONDS` default 24 h, resolved-at floor); group via `collection_settings.group_id`; `history_retention` prunes terminal `staged_uploads`; fixture `alert-kinds.json` (both suites) |
| Client docs (P7-I) | ✅ | [`push-ingest.md`](push-ingest.md) — flow, brokered-vs-direct, rejection reasons, snapshot/lost-update semantics, limits |

No UI surface beyond the already-shipped Settings toggle. Decisions:
[ADR 0014 — process output path / finalize seam](decisions/0014-process-output-path.md)
(the P9-B obligation Phase 7's finalize satisfies),
[ADR 0015 — proxy write policy](decisions/0015-proxy-write-policy.md),
[ADR 0008 — BFF catalog writes](decisions/0008-bff-catalog-writes.md)
(amended: bearer brokering). Residuals in [`ISSUES.md`](ISSUES.md):
I-70 through I-79 (+ the I-13/I-14/I-15 amendments).

---

## Cross-phase — Product-centric UI remodel (ADR 0017, 2026-09-01) ✅

Presentation-layer only: no `/api/*` contract change, no migration, no route
rename. De-emphasized surfaces were relocated, not deleted — with ONE
deliberate exception the lead settled on 2026-09-01: UI-10 made the external-
catalog browser read-only, so create/edit/delete against a third-party STAC API
is gone (see I-89). Those writes bypassed the ADR 0008 BFF, the RBAC guard and
the audit log, which is why they were a bug wearing a feature's clothes.
Design brief: `docs/superpowers/specs/2026-08-31-ui-remodel-design.md`;
per-slice log and carried follow-ups: `app/UI-TODO.md`.

| Area | Status | Entry points |
|---|---|---|
| NOAA theme + light default | ✅ | `app/src/styles/global.css` ⇄ `packages/shared/src/styles/global.css` (byte-identical; `@theme static`), navy `#0D2A47` / federal blue `#005EA2`, health tokens, self-hosted Public Sans + IBM Plex Mono. Light is the default; the pre-hydration script moved into `<head>` |
| App shell | ✅ | `app/src/components/layout/{AppShell,SidebarNav,TopBar}.tsx` — navy rail (navy in BOTH themes), collapsed **More** group, stack-status footer, top-bar client-side global search. Replaced `Header.tsx` across all 20 islands |
| Home overview | ✅ | `app/src/components/layout/DashboardPage.tsx` + `overview.ts` (the shared derivations) |
| Product Overview tab | ✅ | `app/src/components/collections/ProductOverview.tsx` (built-in catalogs only) |
| Lineage strip | ✅ | `packages/shared/src/components/shared/LineageStrip.tsx` — one presentational component at mini / medium / full zoom |
| Processes dashboard + editor | ✅ | `app/src/components/processes/{ProcessesPage,ProcessDetailPage,CodeEditor,RunSparkline,health}.tsx` — CodeMirror 6 (closes I-65) |
| Connections restyle | ✅ | `app/src/components/connections/{ConnectionsPage,ConnectionForm}.tsx` — direction filter/badges derived from associations, type cards |
| Pipeline graph | ✅ | `app/src/components/monitoring/PipelineGraph.tsx` — five columns + exact edge list |
| Products pinned to the built-in catalog | ✅ | `app/src/stores/catalogStore.ts` — `$builtInCatalog` replaces the global `$activeCatalogId`/`$activeCatalog`/`setActiveCatalog`; the orphaned `stac-active-catalog` key is dropped on init. Every product surface (home, `/collections*`, items, top-bar search, stack-status footer) reads it, and `stacFetch`'s no-`endpointUrl` fallback points there so no write can inherit a browse selection past the ADR 0008 BFF |
| Shareable browse links | ✅ | `app/src/lib/browse/paths.ts` — browse URLs carry `?src=<catalog url>`, because the path's catalog id is a per-browser `randomUUID`. `resolveBrowseCatalog` matches by URL when `src` is present (ignoring the path id, which belongs to the sender), so a recipient browses under their own id; one who lacks the catalog is ASKED to add it — `BrowseFrame`'s prompt, `CatalogForm`'s `defaults` prop — and nothing is fetched until they accept. `parseSrc` rejects any non-http(s) `src` — UI-15 |
| Read-only catalog browser | ✅ | `app/src/components/browse/*` at `/catalogs/[catalogId]/collections[/[collectionId][/items[/[itemId]]]]` — entered from each catalog card's **Browse** link (the old "Set Active") and from external `/search` hits. No create/edit/delete, no Data flow / Settings. `CollectionMetadata.tsx` and `ItemDetailView.tsx` are the catalog-agnostic renderers both worlds share |

**Terminology seam (deliberate).** UI copy says *product* (built-in-catalog
collection), *source* (ingest association), *destination* (deliver
association), *pipeline graph*. Routes, `/api/*`, schema and code vocabulary
keep the canonical terms. External-catalog collections keep "collection" —
that surface really is a STAC browser, and since UI-10 it is a SEPARATE one
(`/catalogs/[catalogId]/collections*`) rather than a mode of the product
pages, so the product copy no longer branches at all. Read ADR 0017 before
"fixing" it.

**Health is derived once.** `overview.ts` turns the graph, flow and open-alert
reads into the shapes home, the product page and the graph render, so those
surfaces cannot disagree about whether something is healthy. Where the API
cannot answer — `/api/alerts` returns no `process_id`, so process-anchored
alerts cannot be tied to a product — the UI says so instead of guessing.

**Two Tailwind v4 traps worth knowing** (both found by the in-browser pass):
`@theme` tree-shakes variables no generated utility references (which had
deleted the whole `--color-chart-*` ramp — hence `@theme static`), and
`packages/shared` is outside the app's source scan, so a class used only in a
shared component is never generated (hence `app/src/styles/app.css`, which
adds the `@source`). Storybook needed the FONT imports, not a second scan fix —
its Vite root already IS `packages/shared` (UI-12 verified both halves before
believing either).

---

## GOES GeoColor loop (G queue, 2026-09-01) 🔄

Design spec approved 2026-09-01
(`docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md`); runs in
parallel with M3. Slices G-1…G-7 in `TODO.md`.

| Slice | Status | Notes |
|---|---|---|
| Demo pipeline (`pipeline.demo`) | ✅ | `services/pipeline/src/pipeline/demo/` — rebuilds the whole G-queue loop from nothing in ~10s: a `demo-scenes` collection holding a real 1024² COG in canonical storage → `demo-downscale` (inline_python, `isolated`) reading its ADR 0018 input manifest and publishing a 4× thumbnail COG → `demo-thumbnails`, both serving-enabled so both tile. `seed` / `status` / `teardown`, idempotent, host-side against compose ports (the `pipeline.loadgen` split). Use after `docker compose down -v`. README in that directory |
| G-2 · Process inputs + network profile | ✅ | ADR 0018. The dispatcher records `{item_id, collection_id, op}` per triggering item; `run_one` plans (`process/inputs.py`, pure) → stages (`process/staging.py`: remote hrefs through the owning reference-mode association's adapter, else `connections/http_fetch.py`'s egress-checked public GET; manifest written LAST) → mints with **source-collection read grants** (`credentials.session_policy(read_prefixes)`, ≤ 8, I-90) → launches with `STAC_HIGHER_INPUT_PREFIX` / `STAC_HIGHER_INPUT_MANIFEST`. Producer-golden fixture `process-input-manifest.json`. Finalize skips `inputs/`; a relative output href that is not a plain filename now REJECTS the item. `runtime.network {level, hosts}` on both sides (fixture cases), `PROCESS_NETWORK_MAX` cap enforced by the route AND at launch (`check_network_cap`), deploy-card control with the higher levels disabled; slice 1 stores `isolated` only. Author contract: `docs/processes.md` |
| G-3 · Latency posture | ✅ | A triggered run now starts in seconds, not up to a minute. `trigger_run` enqueues `pipeline.process_run_now` for its own row (`repo.claim_run` — the claim_due_runs UPDATE with an id predicate, so the immediate job and the tick race safely); the tick becomes the recovery sweep for deferred, failed and lost rows. Migration **025** widens coalescing from rate-deferred to EVERY queued run per `(process_id, source_id)` (merging pre-existing duplicates first), pinned as a two-party text contract against the pipeline's `ON CONFLICT`. Ingest gains `settle` (`auto` \| `two_polls` \| `immediate`; `effective_settle` → immediate for s3, two polls for ftp/sftp), removing one poll interval from every s3 flow. The item-write → dispatcher hop needed nothing: it was already NOTIFY-driven |
| G-5 · Item raster preview | ✅ | `app/src/lib/serving/{urls,preview,queries}.ts` (one definition of the titiler base + URL shapes; `pickPreviewAsset` prefers the STAC `visual` role) and the shared `RasterTileLayer` (`packages/shared/src/components/map/`, + story). The product item page's Geometry tab overlays the item's tiles beneath its footprint when the collection advertises serving AND the tiler rendered it, with an *Open viewer* link; `retry: false` and no error surface, so every miss is silent. The catalog browser is untouched |

## Phase 8 — Not started ⬜

Cloud deployment, scale gate & visualization. See
[`../ROADMAP.md`](../ROADMAP.md).
