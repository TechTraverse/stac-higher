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

Poll-based ingest of files from source connections into built-in-catalog collections. Delivered in slices: **Slice A (app associations + Data-flow UI) is done**; the pipeline ingest chain (Slice B) and `storage_mode: reference` wiring (Slice C) are pending.

| Feature | Status | Entry points |
|---|---|---|
| Association + ledger tables | ✅ | migration `005_ingest_associations_and_files`: `stac_higher.collection_connections` (both directions; app writes `ingest` this phase) + `stac_higher.ingest_files` ledger (app owns DDL; pipeline reads/writes rows — ADR 0001) |
| Ingest `config` Zod schema (§5.1) | ✅ | `app/src/lib/associations/schemas.ts` — cross-runtime contract (source_path, include/exclude, poll_frequency, storage_mode, grouping, metadata, post_ingest); nested defaults filled via function-defaults |
| Association CRUD API | ✅ | `GET/POST /api/collections/[id]/connections`, `GET/PUT/DELETE /api/collections/[id]/connections/[assocId]` — operator+ gated & audited; group ownership enforced in-route; `reference` mode restricted to s3 connections; duplicate (collection,connection,direction) → 409 |
| Data-flow tab (ingest half) | ✅ | `app/src/components/collections/DataFlowTab.tsx`, wired into `CollectionDetail.tsx` (built-in catalog only): add/edit ingest sources, enable/disable, remove |
| Pipeline ingest chain (DISCOVER→…→ITEMIZE) | ⬜ | Slice B — scheduler + batch job chain, `raster_auto`/sidecar EXTRACT, stac-pydantic validate, **pypgstac** upsert; enriched adapter `list` metadata; copy-mode FETCH |
| `storage_mode: reference` | ⬜ | Slice C — `resolveAssetTarget` branch to source href (persisted in `ingest_files`) |

No new client deps in Slice A. Decisions: [ADR 0001 — migration ownership](decisions/0001-migration-ownership.md). Residuals in [`ISSUES.md`](ISSUES.md) (I-17, I-18).

---

## Phases 5–8 — Not started ⬜

Delivery, retention/GC, observability, push-ingest, cloud/scale. See [`../ROADMAP.md`](../ROADMAP.md).
