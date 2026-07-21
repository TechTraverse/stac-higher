# Feature catalog

What's built, grouped by delivery phase. Status legend: ✅ done · 🚧 in progress · ⬜ not started. Each area links its detailed reference doc and the ADRs that shaped it. See [`../ROADMAP.md`](../ROADMAP.md) for the plan and [`ISSUES.md`](ISSUES.md) for known gaps.

---

## STAC client application (baseline)

The Astro 6 (SSR) + React 19 STAC client that predates the platform phases.

| Feature | Status | Entry points |
|---|---|---|
| Collections & Items CRUD | ✅ | `app/src/pages/{collections,items}*`, forms via React Hook Form + Zod (`app/src/lib/stac-api/schemas.ts`) |
| STAC search | ✅ | `app/src/pages/search.astro` |
| Map layers | ✅ | `StacMap`, `FootprintLayer`, `ExtentLayer`, `ItemGeometryEditor` (MapLibre GL via `packages/shared/src/lib/map/`) |
| Multi-catalog management | ✅ | `app/src/pages/catalogs.astro`, `app/src/stores/catalogStore.ts` (localStorage; built-in catalog is undeletable) |
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

## Phase 2 — Connections ✅

Group-owned ingest/delivery endpoints the pipeline reads from and writes to. Live-verified end-to-end (SFTP/FTP/S3 test-connections, egress block, TOFU mismatch) on 2026-07-16.

| Feature | Status | Entry points |
|---|---|---|
| Connections CRUD + Zod schemas | ✅ | `stac_higher.connections` (migration 004), `app/src/lib/connections/*`, `/api/connections*` |
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

## Phase 4 — Ingest pipeline 🚧

Poll-based ingest of files from source connections into built-in-catalog collections. Delivered in slices: **Slice A (app associations + Data-flow UI) done**; **Slice B (pipeline ingest chain) in progress** — **B1 (adapter list-metadata + `build_adapter`) done**, **B2+B3 (ingest repo + scheduler + DISCOVER/GROUP/FETCH copy-mode) done and live-verified** (2026-07-16: MinIO source file → poll → DISCOVER → GROUP → FETCH → canonical storage, byte-identical, idempotent), **B4 (EXTRACT/ITEMIZE) done** (pipeline unit tests + a DB integration test; ADR 0006), **B4a (best-effort geometry extraction + collection-extent fallback, ISSUE I-27) done** (226 pipeline unit tests, 2 skipped, after the 3D-bbox fix); a **`/simplify` quality pass** was applied across the B4/B4a slice (behavior-identical cleanups). **B5 (live end-to-end through EXTRACT/ITEMIZE) largely verified (2026-07-17)**: DB integration test, `raster_auto` e2e, netCDF, and collection-inheritance all live-verified. **Slice C (`storage_mode: reference`) done + merged, live-verified** — durably-reachable-source-only (no app decryption, no presigning of source bytes); private-source reference is deferred (ISSUES). **Live end-to-end (2026-07-20, Task 10):** a reference association ran the real scheduler poll → … → itemized (queryable `ST_Polygon` item, no canonical copy, `GET /api/assets` 302→source URL) and an SFTP copy association closed I-4 (first live SFTP `list`/`get` → canonical copy → itemize) — Phase 4's done-when is met. The in-container run found+fixed I-35 (pipeline image missing `libexpat1`).

| Feature | Status | Entry points |
|---|---|---|
| Association + ledger tables | ✅ | migration `005_ingest_associations_and_files`: `stac_higher.collection_connections` (both directions; app writes `ingest` this phase) + `stac_higher.ingest_files` ledger (app owns DDL; pipeline reads/writes rows — ADR 0001) |
| Ingest `config` Zod schema (§5.1) | ✅ | `app/src/lib/associations/schemas.ts` — cross-runtime contract (source_path, include/exclude, poll_frequency, storage_mode, grouping, metadata, post_ingest); nested defaults filled via function-defaults |
| Association CRUD API | ✅ | `GET/POST /api/collections/[id]/connections`, `GET/PUT/DELETE /api/collections/[id]/connections/[assocId]` — operator+ gated & audited; group ownership enforced in-route; `reference` mode restricted to s3 connections; duplicate (collection,connection,direction) → 409 |
| Data-flow tab (ingest half) | ✅ | `app/src/components/collections/DataFlowTab.tsx`, wired into `CollectionDetail.tsx` (built-in catalog only): add/edit ingest sources, enable/disable, remove |
| Adapter list-metadata + `build_adapter` (Slice B1) | ✅ | `services/pipeline/.../adapters/*` `list()` → `FileEntry` (path/size/mtime/etag/is_dir) across s3/sftp/ftp; `connections/build.py::build_adapter` (decrypt→adapter seam the ingest workers consume); `probe` refactored onto it |
| Ingest repo + scheduler + DISCOVER/GROUP/FETCH (Slice B2+B3) | ✅ | `services/pipeline/.../ingest/`: `IngestRepo` (+ `PgIngestRepo`, FakeRepo) mirroring `connections/repo.py`; `config.py` (Python §5.1 mirror + glob matching); `ingest_poll` scheduler (poll_frequency as N whole-minute ticks); `discover.py` settled-check state machine (size/fingerprint unchanged across 2 polls) + adapter-path normalization; `group.py` none/shared_basename + timeout; `fetch.py` copy-mode (buffered `get` → `platform.put_object` at `assets/{collection}/{item}/{filename}`, sha256 checksum); `jobs/ingest.py` chains the stages via the queue |
| EXTRACT + ITEMIZE (Slice B4) | ✅ | `services/pipeline/src/pipeline/ingest/extract.py` (`build_item` dispatcher + `raster_auto`/`sidecar`/`defaults_only` strategies, reading canonical-storage bytes via an in-memory `rasterio.MemoryFile`); `ingest/itemize.py` (`run_itemize` — re-reads `stored` ledger members, EXTRACT → stac-pydantic `validate_item` gate → pgstac upsert → post-ingest, idempotent against the ledger); `ingest/postingest.py` (`apply_post_ingest` — `leave`/`delete`/`move:<path>`, non-fatal); `stac/pgstac_writer.py` (`PgstacWriter` ABC + `PgPgstacWriter`, pypgstac `Methods.upsert`); wired into the queue as `jobs/ingest.py`'s `pipeline.ingest_itemize` task. Part of the full pipeline suite (226 passed, 2 skipped) + a DB integration test (`test_integration_itemize.py`, upsert→query→update, gated on `DATABASE_URL`). No Dockerfile change (bundled-GDAL rasterio wheels); pgstac image pinned to `v0.9.11`. [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md) |
| Best-effort geometry + collection-extent fallback (Slice B4a, ISSUE I-27) | ✅ | `services/pipeline/.../ingest/extract.py`: `is_gdal_candidate`/`GDAL_CANDIDATE_EXTS` (raster + netCDF/GRIB/Zarr/HDF/VRT/IMG), `geometry_from_raster` (best-effort GDAL open → EPSG:4326 bbox polygon, netCDF subdataset-aware, never raises), `bbox_to_polygon`; `build_item` gains `collection_fallback` and resolves geometry strategy → best-effort GDAL → opt-in collection extent → fail-fast (`ExtractError`, never a null-geometry item); `properties["stac_higher:geometry_source"]` provenance. `ingest/itemize.py::_build_collection_fallback` reads `PgstacWriter.get_collection_bbox` when `metadata.defaults.geometry == "collection"`, degrading to a `global_fallback` world polygon for an unset/global extent. Cross-runtime opt-in: `app/src/lib/associations/schemas.ts` `metadataSchema.defaults.geometry`. **RESOLVED ISSUE I-27.** 226 pipeline unit tests, 2 skipped (up from 201; includes a 3D-bbox fix + a `/simplify` cleanup pass, behavior-identical) |
| `storage_mode: reference` (Slice C) | ✅ | Migration `006`: `ingest_files.source_href` (nullable). Schema guard: `ingestConfigSchema` (`app/src/lib/associations/schemas.ts`) rejects `post_ingest` delete/move when `storage_mode: reference` (source bytes are the catalog's asset). Pipeline: `S3Adapter.public_object_url` (credential-free stable URL, `connections/adapters/s3.py`); FETCH reference branch (`ingest/fetch.py` — records `source_href`, no copy, ledger settled→stored); GROUP forms groups identically to copy mode (`ingest/group.py`); EXTRACT byte-source seam (`MemberByteSource`/`CanonicalByteSource`/`SourceAdapterByteSource` in `ingest/extract.py`) so `build_item` reads source bytes without a canonical copy; ITEMIZE unchanged; `post_ingest` skips destructive actions in reference mode as defense-in-depth (`ingest/postingest.py`). App: `resolveAssetTarget` (`app/src/lib/storage/resolve.ts`) branches via `lookupReferenceHref` (`app/src/lib/storage/reference.ts`) to 302 straight to `source_href` — no presigning, no decryption, preserving the "app never decrypts" invariant (`crypto.ts`). **Durably-reachable sources only**; private-source reference is deferred (ISSUES). **Live-verified end-to-end (2026-07-20):** real scheduler-driven reference run → queryable item with no canonical copy, `GET /api/assets` 302→`source_href` (byte-identical on follow); plus an SFTP copy run (I-4). |

No new client deps in Slice A. Slice B1/B2+B3 added no deps (stdlib only). Slice B4 adds pipeline deps: `rio-stac`, `pystac`, `rasterio`, `defusedxml`, `stac-pydantic`, `pypgstac[psycopg]` (§ ADR 0006). Slice B4a adds no new deps — the bundled GDAL 3.12.1 (rasterio 1.5 wheel) already carries the netCDF/HDF5/GRIB/Zarr drivers it uses. Slice C adds no new deps. Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md), [ADR 0005 — asset service](decisions/0005-asset-service.md) (the `resolveAssetTarget` seam Slice C branches), [ADR 0006 — ingest metadata + pgstac upsert](decisions/0006-ingest-metadata-and-upsert.md). Residuals in [`ISSUES.md`](ISSUES.md) (I-17, I-18, I-19, I-20, I-22 through I-26, I-28 through I-31, I-32 through I-34; I-21, I-27, I-35 resolved).

---

## Phases 5–8 — Not started ⬜

Delivery, retention/GC, observability, push-ingest, cloud/scale. See [`../ROADMAP.md`](../ROADMAP.md).
