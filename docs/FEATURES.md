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

No new client deps in Slice A. Slice B1/B2+B3 added no deps (stdlib only). Slice B4 adds pipeline deps: `rio-stac`, `pystac`, `rasterio`, `defusedxml`, `stac-pydantic`, `pypgstac[psycopg]` (§ ADR 0006). Slice B4a adds no new deps — the bundled GDAL 3.12.1 (rasterio 1.5 wheel) already carries the netCDF/HDF5/GRIB/Zarr drivers it uses. Slice C adds no new deps. Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md), [ADR 0005 — asset service](decisions/0005-asset-service.md) (the `resolveAssetTarget` seam Slice C branches), [ADR 0006 — ingest metadata + pgstac upsert](decisions/0006-ingest-metadata-and-upsert.md). Residuals in [`ISSUES.md`](ISSUES.md) (I-17, I-18, I-20, I-22 through I-25, I-28 through I-31, I-32 through I-34; I-21, I-27, I-35 resolved; I-19/I-26 resolved for object stores by M3-C).

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
  a NEW row, which is what re-notifies). Notification fan-out is M2-C. Since
  A-1 (2026-09-09) the shape carries `process_id` (effective) + `source_id`,
  so process alerts attribute to products, process pages and graph nodes
  (I-84).
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
  and suggests disable-instead-of-delete). **W-2 (2026-09-02)** adds a second retention rule beside the age one: `collection_settings.retention_max_items` (migration 026) keeps the newest N items by item `datetime` (`ORDER BY datetime DESC, id DESC OFFSET N` — the id tiebreak keeps `OFFSET` deterministic); the two rules UNION in `list_expired_items`, `archived` overrides both, and nothing about the `retention` reason, the mark-then-collect order, the grace window or the collector changed — ADR 0011 is not amended.

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
| Pipeline graph | ✅ | `app/src/components/monitoring/PipelineGraph.tsx` — **Pipelines \| Graph** (`?view=`, default Pipelines) + exact edge list. Pipelines is one searchable row per product, each drawing that collection's whole lineage as real edges (P-3) |
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
surfaces cannot disagree about whether something is healthy. Since A-1
(I-84), `/api/alerts` carries the effective `process_id`, so process-anchored
alerts tie to a product like any other anchor; the home page's residual "not
shown against a product" line now covers only channel-anchored alerts and
processes wired to no product.

**Two Tailwind v4 traps worth knowing** (both found by the in-browser pass):
`@theme` tree-shakes variables no generated utility references (which had
deleted the whole `--color-chart-*` ramp — hence `@theme static`), and
`packages/shared` is outside the app's source scan, so a class used only in a
shared component is never generated (hence `app/src/styles/app.css`, which
adds the `@source`). Storybook needed the FONT imports, not a second scan fix —
its Vite root already IS `packages/shared` (UI-12 verified both halves before
believing either).

---

## GOES GeoColor loop (G queue, 2026-09-01) ✅

Design spec approved 2026-09-01
(`docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md`); runs in
parallel with M3. Slices G-1…G-8 all merged; owed live looks are GitHub issues labelled `queue:G`.

| Slice | Status | Notes |
|---|---|---|
| Demo pipeline (`pipeline.demo`) | ✅ | `services/pipeline/src/pipeline/demo/` — rebuilds the whole G-queue loop from nothing in ~10s: a `demo-scenes` collection holding a real 1024² COG in canonical storage → `demo-downscale` (inline_python, `isolated`) reading its ADR 0018 input manifest and publishing a 4× thumbnail COG → `demo-thumbnails`, both serving-enabled so both tile. `seed` / `status` / `teardown`, idempotent, host-side against compose ports (the `pipeline.loadgen` split). Use after `docker compose down -v`. README in that directory |
| G-2 · Process inputs + network profile | ✅ | ADR 0018. The dispatcher records `{item_id, collection_id, op}` per triggering item; `run_one` plans (`process/inputs.py`, pure) → stages (`process/staging.py`: remote hrefs through the owning reference-mode association's adapter, else `connections/http_fetch.py`'s egress-checked public GET; manifest written LAST) → mints with **source-collection read grants** (`credentials.session_policy(read_prefixes)`, ≤ 8, I-90) → launches with `STAC_HIGHER_INPUT_PREFIX` / `STAC_HIGHER_INPUT_MANIFEST`. Producer-golden fixture `process-input-manifest.json`. Finalize skips `inputs/`; a relative output href that is not a plain filename now REJECTS the item. `runtime.network {level, hosts}` on both sides (fixture cases), `PROCESS_NETWORK_MAX` cap enforced by the route AND at launch (`check_network_cap`), deploy-card control with the higher levels disabled; slice 1 stores `isolated` only. Author contract: `docs/processes.md` |
| G-3 · Latency posture | ✅ | A triggered run now starts in seconds, not up to a minute. `trigger_run` enqueues `pipeline.process_run_now` for its own row (`repo.claim_run` — the claim_due_runs UPDATE with an id predicate, so the immediate job and the tick race safely); the tick becomes the recovery sweep for deferred, failed and lost rows. Migration **025** widens coalescing from rate-deferred to EVERY queued run per `(process_id, source_id)` (merging pre-existing duplicates first), pinned as a two-party text contract against the pipeline's `ON CONFLICT`. Ingest gains `settle` (`auto` \| `two_polls` \| `immediate`; `effective_settle` → immediate for s3, two polls for ftp/sftp), removing one poll interval from every s3 flow. The item-write → dispatcher hop needed nothing: it was already NOTIFY-driven |
| G-5 · Item raster preview | ✅ | `app/src/lib/serving/{urls,preview,queries}.ts` (one definition of the titiler base + URL shapes; `pickPreviewAsset` prefers the STAC `visual` role) and the shared `RasterTileLayer` (`packages/shared/src/components/map/`, + story). The product item page's Geometry tab overlays the item's tiles beneath its footprint when the collection advertises serving AND the tiler rendered it, with an *Open viewer* link; `retry: false` and no error surface, so every miss is silent. The catalog browser is untouched |
| G-6 · Extractors | ✅ | Migration **027**: `processes.kind` (`transform` \| `extractor`, create-only), the `extracting` ledger status plus `reason`/`extract_run_id`/`source_mtime` columns on `ingest_files` (the I-100 fix — DISCOVER persists the listed `mtime`, `file_mtime` prefers it), and `process_runs.association_id` with a second partial unique index for association-keyed coalescing (extractor runs coalesce like transforms, keyed on the association instead of a source). Ingest `metadata.strategy: "extractor"` names a `kind = 'extractor'` process in the same group; an association-triggered ITEMIZE now splits at the seam — a `complete_item` tail shared with an ordinary catalogue write, or a hand-off to the extractor as an unpublished draft (id/collection/assets fixed, `datetime`/`geometry` nullable). The extract finalize branch (`pipeline/finalize/extract_run.py`) validates each output document's immutable fields, calls `complete_item` per item, and fails the batch's remaining ledger rows with the run's error on a dead run; the `extracting` stall sweep (`sweep_stuck_extracting`) recovers rows whose run never finished. Reference-mode inputs are staged from the ingest ledger's `source_href` rather than granted a canonical read that would not resolve (Task 9b — closes a gap in G-2's planner, both run kinds). A display-only `extractor` graph edge runs process → collection (`lib/graph/edges.ts`) so the process appears on `/monitoring`. UI: process `kind` at create + a badge, an extractor detail card, and an extractor-process picker on the ingest form. `pipeline.loadgen --metadata extractor` seeds the pass-through extractor profile. Design record: spec §15 addendum. Author contract: `docs/processes.md` "Extractors" |
| D-1 · `derived_from` lineage on process outputs | ✅ | The process-run finalize resolver (`finalize/process_run.py`) stamps one `derived_from` link per item in the run's input batch onto every output that carries none — the batch rides in the request's `provenance.input_items` (`jobs/process.py` reads the run row), inputs the planner recorded as `skipped` are excluded, an output that republishes an input never self-links, and a document that already has a `derived_from` link keeps its link set verbatim (the many-in/many-out override). Hrefs come from `storage/keys.py` `item_href` under `CATALOG_HREF_BASE` (default root-relative `/collections/…/items/…`, mirroring `ASSET_HREF_BASE`). Extractor and cron runs are untouched by construction. Neutral steps unchanged (ADR 0014). Author contract: `docs/processes.md` "Lineage" |
| D-2 · `derived_from` on the item page | ✅ | A "Derived from" chip row on the Properties tab of `ItemDetailView` (nothing rendered without such a link; upstream only). The view stays catalog-agnostic: it takes a `resolveLink` prop and the product page (`ItemDetail`) and the catalog browser (`BrowseItemPage`) each pass `resolveItemLink` from their own catalog context. `lib/browse/paths.ts` `resolveItemLink` maps a root-relative `/collections/{c}/items/{i}` href (D-1's default) onto the page's catalog and an absolute href onto any configured catalog by URL prefix, then hands `itemHref` the match (built-in → product page, other → browse page with `?src=`); anything unmatched is a plain external anchor, never a dead route. Tests: `browse-paths.test.ts`, `item-detail-lineage.test.tsx`. Downstream lineage (items derived from THIS one) is out of scope — a STAC API cannot search by link |
| G-7 · GOES worked example + live-gated e2e | ✅ | `goes-abi-metadata` (extractor) and `goes-geocolor` (process) in `services/pipeline/src/pipeline/demo/goes/`, on the CURRENT runtime image — rasterio's bundled GDAL reads the netCDF, numpy composites it, GDAL's COG driver writes it, rio-stac items it. `pipeline.demo goes-seed`/`goes-status`/`goes-teardown` (README GOES section) seed the same loop by hand against the live `noaa-goes19` bucket, `--include`-pinned to one granule or full-hour; `--deliver` adds the `stac-higher-deliveries` MinIO bucket (`minio-init`, `docs/connections.md`). `app/e2e/goes-loop.spec.ts`, gated on `E2E_LIVE_NODD=1` (`run-e2e` skill), drives the whole thing through the product's own API against a single newest-hour granule and polls four gates: the source item's scan-time datetime + `goes:*` properties, the output item's `visual` COG asset, a rendered tile, and delivery to the second bucket. Three corrections the plan didn't anticipate (spec §15 addendum): the runtime image's GDAL netCDF driver cannot open `/vsis3` paths at all, so both scripts download to local disk first; the built-in netCDF path derives no footprint for MCMIPC (I-101), so the extractor computes one from the `CMI_C02` subdataset itself; and the e2e spec scopes ingestion to one file with the ingest config's existing `include` glob rather than any new mechanism. Design record: spec §9, §15 addendum. Author contract: `docs/processes.md` "The GOES worked example" |
| G-8 · GeoColor night side | ✅ | The night branch of `geocolor.py`'s `compose()`, which G-7 shipped as spec §9 scoped it: the inverted C13 written to all three channels as grey and merged with the day side by `np.maximum`. Measured 2026-09-04, that made a night COG ~1 % colour pixels against 57 % for a daytime one — the "black and white" in the lead's feedback, and the true-colour path was never at fault. Two changes, both inside `compose()`: the inverted brightness temperature now interpolates a **NOAA-style ramp** (`NIGHT_WARM_RGB = (0.02, 0.05, 0.18)` deep blue for warm surfaces → `NIGHT_COLD_RGB` white for the coldest tops) instead of a grey, and day and night are blended by the **per-pixel solar zenith angle** across an 80°–96° twilight band instead of a maximum, so dusk fades rather than flips. `solar_zenith()` supplies the angle with no new dependency: pixel centres every 64 pixels reprojected to WGS84 — bisecting the batch to isolate the off-limb samples GDAL refuses outright (the failure `footprint()` already works around), then nearest-filling them so nothing meaningless smears into the on-disk pixels of the same cell — the NOAA low-precision sun position at the granule's **scan time**, and a bilinear upsample; one degree of zenith is ~110 km, so the sampling is two orders of magnitude finer than the band it feeds. `compose()` takes the angle as a fifth argument, since it cannot be derived from the radiances. City lights stay out (they need a static reference-asset input — I-106). Measured 2026-09-04 on three real `noaa-goes19` granules with the pre-G-8 `compose()` alongside for a same-granule before/after: night **0.0 % → 100.0 %** colour pixels, terminator (a third of the scene inside the twilight band) 13.9 % → 91.4 %, day 62.5 % → 62.5 % and **bit-identical** — zero pixels changed, so the true-colour path is provably untouched. Design record: spec §16 (§9 addendum). |
| Collection raster preview (Preview tab) | ✅ | `docs/serving.md` "Collection preview". A **Preview** tab on serving-enabled product pages animates the product one frame per timestep, off titiler-pgstac's collection mosaic with `datetime` pinned (`app/src/lib/serving/frames.ts` builds the axis from `GET /items?limit=N&sortby=-datetime`, keeping the catalog's datetime string VERBATIM — the filter matches the instant exactly; `collectionTileUrlTemplate` in `urls.ts` builds the templates with `{z}/{x}/{y}` left literal). Playback (shared `TimeSlider`, + story) waits on `map.areTilesLoaded()` rather than dropping frames — first pass at the tiler's pace, replays at full rate off the browser cache, 10 s cap — with the outgoing frame painted beneath the incoming one so a step never blanks, and exactly one frame warmed ahead (a deeper window starves the visible frame's own tile requests). Zoom limits come from the newest item's TileJSON, since the mosaic advertises 0–24. The tab is hidden unless serving is on and the recent items carry a tileable asset; frame count (25/50/100/200) and the asset (when several render) are pickable, plus an *Open viewer* link pinned to the current frame. Live-verified against the standing `goes-geocolor` demo (2026-09-04). Its frame window is the shared `RasterFrameStack` (V-1, 2026-09-04), alongside the id-namespaced `FootprintLayer` and the new `VectorTileLayer` the `/map` page builds on. |
| Map page (`/map`) | ✅ | V-2 (2026-09-07), spec `docs/superpowers/specs/2026-09-04-map-page-design.md`. `app/src/pages/map.astro` + `app/src/components/map/` — built-in-catalog products added as map layers from an Add-layer popover, with a layer list (visibility, opacity, order, remove), footprint hover tooltips and click-through to the item page. Layer state is the `app/src/lib/map/state.ts` reducer; draw order is chained through each layer's stable bottom layer id (a footprints fill, a raster stack's `${id}-anchor`), never a raster frame. V-3 (2026-09-14) added imagery layers (titiler-pgstac's collection mosaic with `datetime` pinned, the Imagery option offered only when the product advertises serving AND a five-item probe finds a tileable asset, with a per-row asset select) and the docked time bar: `app/src/lib/map/axis.ts` builds one axis from the union of every time-aware layer's frame instants and resolves each layer's own frame by hold-last, so a slow product stays on screen through a fast one's ticks; footprints draw the items of their current frame, playback waits on `map.areTilesLoaded()` with the shared 10 s cap, and the page-wide span (25/50/100/200) returns the axis to the newest tick. I-112 (backward scrub draw order) is fixed inside `RasterFrameStack` by chaining `beforeId` between mounted frames. V-4 (2026-09-14) added the Vector tiles section: `useTipgCollections` (`serving/queries.ts`, quiet, fetched only while the picker is open) lists tipg's `/collections`, each adding a `vector` layer that `VectorTileLayer` draws from `tipgTileJsonUrl(id)` (source layer `default`, verified against tipg 1.0.1); vector layers keep the camera and ignore the time axis. An unreachable tipg reads "no vector tiles published". `docs/serving.md` records the TileJSON path; spec §8's deferrals are I-126 |

## Ingest window + retention cap (W queue, 2026-09-02) 🔄

Design spec approved 2026-09-02
(`docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md`).
Measured that day: one GOES product on `noaa-goes19` is ~250,000 objects /
~12 TB and nothing in the ingest path bounded a listing, a fetch, or what was
kept. The window governs what comes **in** (W-1); retention governs what
**stays** (W-2, ADR 0011) — the two rules never contend for the same object.

| Slice | Status | Notes |
|---|---|---|
| W-1 · Ingest date window + prefix expansion + per-poll cap | ✅ | Three optional fields on the ingest `config` (cross-runtime: `ingestConfigSchema` ↔ `ingest/config.py` ↔ the `ingest-config.json` fixture; absent = today's behaviour). `window.begin`/`end` — RFC3339 or `-<n>[smhd]`, re-resolved every poll, half-open on `FileEntry.mtime`; `path_template` — `{Y}{m}{d}{j}{H}` expanded from the window into the prefixes to list (`INGEST_MAX_WINDOW_PREFIXES`, default 1000, refuses rather than truncates); `max_files_per_poll` — admits NEW files oldest-first. All the arithmetic is the pure `ingest/window.py` (takes `now`, no clock). DISCOVER lists per expanded prefix, gates on mtime (`undateable` entries skipped + logged once a window is set), caps admissions; counters `prefixes_listed`/`out_of_window`/`undateable`/`deferred_by_cap`. Zod checks shape + template tokens, the pipeline checks the bound grammar. Data flow form: a *Date window* fieldset + *Max files per poll*. NODD worked example: `source_path "ABI-L2-MCMIPC/"`, `path_template "{Y}/{j}/{H}/"`, `window {begin: "-6h"}` → six listings of ~12 keys instead of one of ~250,000. **Live gate met 2026-09-02** against the real `noaa-goes19` bucket: two hourly prefixes listed for a -1h window, 21 keys seen, 9 outside the window, 4 admitted per 60 s poll (8 → 4 → 1 deferred), 12 items catalogued in three polls. Findings logged as I-100 (`file_mtime` = settle time) and I-101 (`raster_auto` on GOES netCDF falls back to the collection extent) |
| W-2 · Retention count cap | ✅ | Migration **026**: `collection_settings.retention_max_items` (nullable, `CHECK >= 1` — "keep zero" is `archived`, not a cap). Pipeline `gc/repo.py`: `list_gc_collections` also visits count-capped collections; `list_expired_items` UNIONs "older than `retention_days`" with "beyond the newest N by `datetime DESC, id DESC`" (assembled per rule — `OFFSET NULL` is an error), `archived` clears the cap in that one place; the `retention` reason, grace and collector untouched. App: `collectionSettingsUpdateSchema` + storage + PUT route carry `retention_max_items`; the impact dry-run accepts `?retention_max_items=N` and answers `count(DISTINCT id)` over the union when both rules are given, so the dialog's number equals what the sweep deletes. Settings tab: a *Maximum items* input on the existing warn-and-proceed path (newly set or lowered → dry-run; raised or cleared → direct save). K-3's planned migration renumbered to 028 (027 was taken by G-6's extractors). **Live check met 2026-09-02**: cap 1 on `demo-scenes` with a second scene → dry-run `1 of 2`, the sweep marked the older prefix with reason `retention` and deleted the item, the collector removed the object (`errors 0`) |

## Built-in extractor library (X queue, 2026-09-04) 🔄

Design spec approved 2026-09-04
(`docs/superpowers/specs/2026-09-04-stactools-extractor-library-design.md`),
from the lead's feedback: an operator pointing a connection at a public
archive should pick "GOES ABI" from a list, not write a parser. The community
already maintains stactools packages for most of NOAA's archives; the platform
ships a curated set on a second runtime image and turns one pick into a
group-owned, read-only extractor process.

| Slice | Status | Notes |
|---|---|---|
| X-1 · Registry + both readers | ✅ | `tests/contract-fixtures/builtin-extractors.json` (style `registry` — the fixture is the DATA, not a shape, versioned with the image that installs it), read by `app/src/lib/extractors/schemas.ts` (strict Zod: a registry typo fails CI at review time) and `services/pipeline/src/pipeline/process/builtin.py` (lenient: a registry that grows a key cannot brick an older pipeline image). The import name both sides derive from `package` (`stactools-goes-glm` → `stactools.goes_glm`) is part of the contract — the image's build-time smoke test imports exactly that string. `pin_drift` compares registry and `Dockerfile.stactools` in **both** directions (an entry the image lacks, a pin the registry lacks, a version that differs), so the two cannot disagree about what a run imports; it is unit-tested against sample text and its assertion against the real Dockerfile arms itself when X-2 creates it. **The set is eleven, not the spec's fourteen**: `noaa-nwm`, `noaa-sst` and `hls` have never been published to PyPI — untagged repos only — so nothing can pin them (lead's call, I-107, spec §14). Packaging is deliberately open: both readers take a document, because neither build context contains `tests/` |
| X-2 · stactools image + wrapper + adapters | ✅ | `services/process-runtime/Dockerfile.stactools` extends the base runtime image with `stactools` + the eleven pins (a uv `--override pystac==1.15.1` because three packages cap pystac below the platform pin yet all eleven work on it — I-109; `setuptools<81` for `pkg_resources`); the build runs the registry-driven smoke `python -m stac_higher_stactools.smoke` (import every entry's module + adapter, installed version == pin), so a stale package fails the BUILD. **Built and proved locally 2026-09-04** (1.18 GB; seven adapters produced items inside the image as `runner`). `docker-bake.hcl` chains base → variant and supplies the `fixtures` context — **the packaging decision**: the registry is `COPY --from=fixtures` into the pipeline and runtime images (compose `additional_contexts`, CI `build-contexts`), path in `STAC_HIGHER_BUILTIN_REGISTRY`, `load_builtin_registry()` falling back to the checkout. Wrapper `stac_higher_stactools`: `run(builtin_id)` reads the manifest, stages each draft's assets under their basenames, calls `adapters.<adapter>.create(paths, draft)`, MERGES under §6.1 (keep id/collection/asset keys/hrefs; overlay properties, copy geometry/bbox/extensions; asset metadata by staged path or basename; added assets dropped and logged; `datetime` ← `start_datetime` when null), writes `{item_id}.json`; per-item failure, exit 1 only when nothing landed. Eleven adapters; nine unit-tested against the package's own fixture through the real `check_extract_output` (`tests/data/stactools/`), viirs + sentinel1 with doubles. `containers.yml` builds both runtime images via `docker/bake-action` |
| X-3 · Image alias | ✅ | `runtime.runtime_image: "default" \| "stactools"` on `runtimeLimits` (Zod enum, default `default`; lenient Python reader — every stored revision lacks it and reads as `default`), `PROCESS_RUNTIME_IMAGE_STACTOOLS` in settings/compose/.env.example, `resolve_runtime_image` at launch (`launch.py`) feeding `build_run_spec`, and the `PROCESS_NETWORK_MAX` dual pattern: the schema refuses an alias outside the set at the form, the pipeline reader refuses it as an unusable revision, and a KNOWN alias whose image the deployment left empty dies in `runner.py` naming the alias and the variable before anything is staged or minted. `runtime.image` stays `null` (ADR 0013 intact). `process-runtime.json` gains six cases + `runtime_image` in both golden defaults. K-1 note added to the K spec (profile `image` is a base; the alias selects the variant) |
| X-4 · Built-in processes in the app | ✅ | Migration **028** (`processes.builtin_id` + the live partial unique index per `(group_id, builtin_id)`); `GET /api/extractors/builtin` serves the registry BUNDLED at build time (`lib/extractors/registry.ts` imports the fixture; `app/Dockerfile` gets it through the `fixtures` build context like the other images); `POST /api/processes/builtin` create-or-reuse (operator+, audited `create`) inserts the extractor process + deploys revision 1 from `lib/extractors/template.ts` (two-line body on `runtime_image: "stactools"`, parsed through the write gate) in one transaction, converging on the index winner under a race; "Update to current" is `POST …/revisions {from_builtin: true}` (same `deploy` audit row) and a code deploy on a built-in is a 409. UI: read-only **Built-in** card replacing the code editor, `built-in` badges, and the ingest form's **Built-in** picker group (registry minus the group's instantiated ones, filtered by `supports` vs grouping) that resolves to a `process_id` before saving |
| X-5 · Live gates (lead) | ⬜ | Gate A against the standing GOES demo; gate B across the four remaining anonymous packages |

### Process compute — Kubernetes + Kueue + hardware profiles (K queue)

| Slice | Status | Notes |
|---|---|---|
| K-1 · hardware-profile contract | ✅ | `infra/hardware-profiles/local.json` — one JSON document both runtimes read via `PROCESS_HARDWARE_PROFILES_FILE` (unset: the checkout), packaged into both the app and pipeline images through a `hardware` named build context (the repo-root `.dockerignore` excludes `infra/`, same pattern as `fixtures`/X-2). `pipeline/process/hardware.py` (lenient) and `app/src/lib/processes/hardware.ts` (strict) parse the same shape: named profiles with `cpu`/`memory_mb`/optional `gpu_count` bounds, a queue-wait promise, an optional base `image`, and an opaque per-executor `backend` block (K-3 Docker, K-5 Kubernetes). The app's write gate rejects a revision's `hardware` block outside its profile's bounds or naming an unknown profile at deploy time (`hardwareBoundsError`, 400) — on every deploy path, hand-written and built-in alike; the pipeline re-checks the same bounds at launch (`check_hardware_bounds`) — a bounds violation or unknown profile dies naming the bound, an unreadable/missing profile document requeues without spending an attempt instead (Task 4 review; the file path comes from `Settings.process_hardware_profiles_file`). `GET /api/processes/hardware-profiles` serves the set minus each profile's `backend` (member+). `RunSpec` already carries `cpu`, `gpu_count`, the resolved `profile` and a `priority` (`interactive`/`triggered`) that nothing reads yet |

## Pipeline graph views (P queue, 2026-09-04) ✅

Design spec approved 2026-09-04
(`docs/superpowers/specs/2026-09-04-pipeline-graph-views-design.md`), from the
lead's feedback against the standing GOES demo: the extractor was invisible,
two pipelines with the same column shape were indistinguishable, and "Not
wired" listed ghosts. A rendering change over data the platform already
serves — no new endpoint, no new dependency (`elkjs`, `dagre` and React Flow
were offered and declined).

| Slice | Status | Notes |
|---|---|---|
| P-1 · Ghost nodes | ✅ | `loadGraph`'s collection union joins `processes` and requires `deleted_at IS NULL` on the `process_sources` and `process_outputs` branches, matching `loadGraphEdges` — so the node set and the edge set can no longer disagree about what a live process is, and a soft-deleted process stops leaving degree-0 orphans. Test: `app/src/__tests__/graph-storage.test.ts`. Closes I-104 |
| P-2 · Lineage + layout | ✅ | `packages/shared/src/lib/graph/` — `lineage(graph, id)` (transitive closure both ways over the INDUCED edges; `extractor` followed upstream only, so two collections sharing an extractor stay separate) and `layeredLayout(graph, opts)` (longest-path ranks, an extractor ranked one column left of the product it points into, barycenter ordering in two sweeps, orthogonal paths with a fixed elbow). Total over a cycle — the M5-D gate only refuses the cycles it can see — with the back edge dropped from ranking and still drawn. Determinism is a tested property. `FIXTURE_GRAPH` = the GOES loop + a chained demo pipeline. 27 unit tests, no React |
| P-3 · Pipelines view | ✅ | `PipelineDag` (shared): SVG `<path>` per edge under absolutely-positioned `NodeChip`-style HTML nodes — text in SVG would mean hand-rolled truncation and no links or keyboard focus. `/graph` gains the **Pipelines \| Graph** switch (`?view=`, default Pipelines per spec §9.1) and a searchable one-row-per-product list, each row the product's WHOLE chain with the extractor badged and drawn into its product; rows cap downstream at 8 with "+k more" (`capLineage`, spec §8). `LineagePanel` swaps its two one-hop lists for the same row, keeping the 30-day strips beneath — one component, two surfaces. Storybook: `PipelineDag.stories.tsx`. e2e in `processes.spec.ts` |
| P-4 · Graph view | ✅ | The Graph tab is the same `PipelineDag` over the whole group-scoped graph, replacing M5-F's five columns. Clicking a node fades everything outside `lineage(node)` and offers an Open link; clicking the background clears. Degree-0 nodes stay out of the SVG and keep their "Not wired" row (spec §9.4) — a chip attached to nothing reads as a layout bug. Rendering the real platform exposed a layout defect fixed here: the barycenter passes batched their position update to the end of a sweep, so the backward pass ordered a rank against the forward pass's stale positions and crossed two independent chains' first hop for no reason (regression test in `graph-layout.test.ts`) |

## NOAA-scale readiness (M3 queue, 2026-09-01) 🔄

Spec: `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md`; evidence
`2026-08-31-m3-scoping-notes.md`; remaining slices M3-D…M3-I are GitHub issues under epic #1 (`queue:M3`).

| Feature | Status | Notes |
|---|---|---|
| M3-A · pgstac write path | ✅ | The writer's own pooled connections carry `pgstac.use_queue` as a SESSION GUC (`pipeline/db/pgstac_session.py`, `stac/pgstac_writer.py` — pypgstac's `PgstacDB(pool=…, use_queue=True)` seam; nothing in deployment config), so every item write queues its partition's statistics refresh instead of scanning the partition inline (measured 2–3.5 → ~22 items/s, S-A §1). `pgstac.update_collection_extent` is the OPPOSITE pairing, carried instead on the DRAINER's connection (`stac/query_queue.py`'s `DRAIN_CONNECTION_SQL`: `update_collection_extent` ON, `use_queue` explicitly FALSE) — `update_partition_stats`'s nested extent refresh runs in whichever session drains the queue, not the one that writes, so collection extents are maintained for the first time since Phase 4. `pipeline.pgstac_queue_drain` (`stac/query_queue.py`, `jobs/pgstac_drain.py`) drains the queue every minute locally (`PGSTAC_QUEUE_DRAINER=pipeline`) or only samples it where pg_cron does (`database`); depth + oldest age are gauges on `/metrics`, a stale queue is a WARNING, `query_queue_history` is pruned. Loadgen samples the queue and the partition count. ADR 0020 records the two pairings as an invariant. Live numbers: `TODO.md` follow-ups. |
| M3-B · connection pool | ✅ | Repo database access runs on a process-wide `psycopg_pool.AsyncConnectionPool` (`pipeline/db/pool.py`, M3-B, 2026-09-14): lazily opened on first use (`open(wait=True)` bounded by a 10 s warm-up timeout so the min-size fill cannot race the first checkout), keyed by DSN, sized by `DB_POOL_MIN`/`DB_POOL_MAX` (2/16 — at least `WORKER_CONCURRENCY + 4`), its `configure` hook carrying the pgstac session GUCs (ADR 0020). Twelve repos + two one-offs check out of it; Procrastinate's connector, the LISTEN connection, `setup()`/`check_connection()` and the pgstac queue drainer stay direct by design. `main.run()` closes both pools before the queue with each close isolated; pool stats are on `GET :8083/health` under `db_pool`, keyed `host:port/dbname` (no credentials). Measured: 19.5 → 0.10 new backend sessions per item, `ingest_itemize` 32 → 9 ms (README "Connection pooling"). |
| M3-C · bounded-memory byte path | ✅ | EXTRACT opens rasters in place (`/vsis3` `RasterLocation`, `ingest/raster_io.py`); copy-mode FETCH server-side-copies when the platform can read the source, else streams a bounded multipart upload (`ingest/transfer.py`); the envelope is `GDAL_CACHEMAX` + `FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY` per job (README "Memory envelope"). Closes I-19/I-26 for object stores; I-83 remains. Measured: `TODO.md` follow-ups. |

## Phase 8 — Not started ⬜

Cloud deployment, scale gate & visualization. See
[`../ROADMAP.md`](../ROADMAP.md).

**Planned (2026-09-02, ADR 0019 proposed):** process compute moves to
**Kubernetes Jobs + Kueue** (Fargate has no GPU) with operator-chosen
**hardware profiles** in the UI and a submit-then-reconcile executor. The
local-buildable half (K-1…K-7: profile contract, UI picker, Docker parity,
async executor + cancel, `KubernetesExecutor`, Kueue manifests + kind CI,
CUDA image) is the K queue — GitHub epic #2 (`queue:K`); the EKS deployment (K-8) and the
gate (K-9) are Phase 8. Spec:
`docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md`.
