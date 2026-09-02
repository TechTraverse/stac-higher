# AGENTS.md

Canonical instructions for all AI coding agents working in this repo, per the
[AGENTS.md](https://agents.md/) open standard. Harness-specific additions live in
that harness's own file (e.g. `CLAUDE.md` for Claude Code) — everything here
applies to every agent.

## Monorepo Layout

npm-workspaces monorepo:

- `app/` — the Astro 7 (SSR) + React 19 STAC client
- `packages/shared/` — `@stac-higher/shared`: reusable components, hooks, types, stores, RJSF theme, and Storybook

Facts:
- Run `npm install` at the **repo root** — not `app/` — to wire workspace symlinks. Single lockfile at the root.
- **Shared components are the source of truth.** UI primitives (Button, Card, Badge, Input, Label, Select, Skeleton, Switch, Textarea, Tooltip), `shared/*` utilities (BboxInput, EmptyState, ErrorBoundary, ErrorState, JsonViewer, LoadingState), layout (ThemeToggle), collection/item cards, all `map/` components, and the RJSF theme live in `packages/shared/` and are imported from `@stac-higher/shared`. Most `app/src/lib/` and `app/src/stores/` files are thin re-export proxies.
- App-only shadcn primitives (dialog, dropdown-menu, popover, separator, sheet, sonner, table, tabs) remain in `app/src/components/ui/`.
- Path aliases: `@/*` → `app/src/*` (app-local); `@shared/*` → `packages/shared/src/*` (used *inside* the shared package only — app code imports from `@stac-higher/shared`).

## Commands

- **Install**: `npm install` (repo root)
- **Verify**: `npm run verify` (repo root — app-scoped typecheck + app build + unit tests, the same gates CI runs; **must pass before declaring any task done**)
- **Dev**: `npm run dev` (from `app/`, http://localhost:4321)
- **Build**: `npm run build` (from `app/`, outputs to `app/dist/`)
- **Unit tests**: `npm test` (from `app/`); `npm run test:watch` for watch mode
- **Pipeline tests**: `uv run pytest` and `uv run ruff check .` (from `services/pipeline/` — run both whenever the pipeline is touched; `DATABASE_URL=…` enables the DB-gated integration tests. Full pipeline reference: `services/pipeline/README.md`)
- **E2E**: `npm run test:e2e:ci` (from `app/` — list reporter, agent-friendly). Read the `run-e2e` skill first; the suite has real preconditions and gotchas.
- **Proxy integration tests**: `npm run test:integration` (repo root — needs the Docker stack in auth-enforced mode, so lead/human only; skips cleanly otherwise. See `tests/integration/README.md`.)
- **Storybook**: `npm run storybook` (from `packages/shared/`, http://localhost:6006)
- **Backend**: `docker compose up -d` (repo root — full local stack: pgstac (:5433), stac-fastapi (:8082), stac-auth-proxy (:8081, pass-through), Keycloak (:8180, admin/admin), MinIO (:9000 API / :9001 console), pipeline service (:8083 `/health`), titiler-pgstac (:8084) + tipg (:8085) OGC serving (`docs/serving.md`))

## Architecture

**Astro + React islands**: Astro pages (`app/src/pages/*.astro`) are thin routing
shells; each mounts a single React island via `client:only="react"`. The only
cross-island split is Header vs. page content.

**Three-tier state**:
1. **Nanostores** — cross-island persistent state (the configured catalog list, theme), persisted to localStorage. Catalog state: `app/src/stores/catalogStore.ts` (`$catalogs`, `$builtInCatalog`). There is **no global "active catalog"** (UI-10): product surfaces read `$builtInCatalog`; the catalog browser takes its catalog from the route (`/catalogs/[catalogId]/collections*`, resolved by the shareable `?src=` catalog URL when present — UI-15) and `/search` keeps a local, non-persistent selection. `stacFetch` REFUSES a write whose catalog is not the built-in one (`POST /search` excepted — it is a read); external catalogs are read-only (I-89).
2. **TanStack Query** — server state. Query keys include the catalog URL, so switching catalogs invalidates all cached data. Key factory: `app/src/lib/query/keys.ts`.
3. **React Hook Form + Zod** — form state. Schemas in `app/src/lib/stac-api/schemas.ts`.

**Data flow**: `useStore($builtInCatalog)` → TanStack Query hook → API function
(`app/src/lib/stac-api/*.ts`) → `stacFetch()` → STAC API. Mutations invalidate
query keys; forms redirect via `window.location.href` on success.

**Map**: MapLibre GL JS via `react-map-gl/maplibre`; CartoDB basemaps
(dark-matter / positron). Components: `StacMap`, `FootprintLayer`, `ExtentLayer`,
`ItemGeometryEditor`. Utilities in `packages/shared/src/lib/map/`.

For the full conventions (island pattern rationale, form pattern, import rules),
read the `project-conventions` skill before any non-trivial change.

## Backend & API Routes

docker-compose runs the full local platform stack:

- **pgstac** (PostgreSQL + PostGIS, host :5433) and **stac-fastapi-pgstac** with
  the Transaction extension (full CRUD) at `http://localhost:8082`. The
  `pgstac-migrate` one-shot migrates a persisted volume's pgstac schema to the
  pinned version on every `up` (the pgstac image only installs its schema on
  fresh volumes — ADR 0001, I-54).
- **stac-auth-proxy** at `http://localhost:8081` in front of stac-fastapi —
  pass-through by default (`DEFAULT_PUBLIC=true`, no login needed). Opt-in
  enforcement (authenticated transactions + audience check, reads still
  public, plus — since Phase 7 — the ADR 0015 per-collection
  `externally_writable` write policy via a derived proxy image) via
  `export CATALOG_BFF_SHARED_SECRET=$(openssl rand -hex 32)` (or `.env`;
  mandatory — and the app process needs it too, from the shell/`.env`) then
  `docker compose -f docker-compose.yml -f
  infra/compose.auth-enforced.yml up -d --build --wait` — see
  `docs/decisions/0002-auth-proxy-enforcement.md` and
  `docs/decisions/0015-proxy-write-policy.md`.
  The client's **built-in catalog** points here
  (`PUBLIC_BUILTIN_CATALOG_URL`, default `http://localhost:8081`) and is
  seeded as an undeletable entry in the `/catalogs` page.
- **Keycloak** at `http://localhost:8180` (admin/admin, realm `stac-higher`
  imported from `infra/keycloak/realm-stac-higher.json`). Realm import is
  skipped once the realm exists in the persisted volume — edits to the realm
  file only take effect after `docker compose down -v`.
- **MinIO** at :9000 (console :9001, minioadmin/minioadmin, bucket
  `stac-higher`).
- **pipeline service** (`services/pipeline`, Python) — queue worker +
  scheduler with `/health` on :8083. See
  `docs/decisions/0001-migration-ownership.md` for schema ownership.
- **docker-socket-proxy** (Phase 9 M5-B, ADR 0013) — the least-privilege
  seam through which the pipeline launches process-run containers
  (`CONTAINERS=1 POST=1`, everything else 403). The pipeline never holds
  `/var/run/docker.sock`; `DOCKER_HOST` must be the proxy or the executor
  refuses to start. Runs attach to the internal `process-runs` network (or
  `none`, the default) and execute the platform image built from
  `services/process-runtime/`.
- **OGC serving**: **titiler-pgstac** at :8084 (OGC API Tiles for rasters,
  per STAC collection off pgstac) and **tipg** at :8085 (OGC API
  Features/Tiles for vector tables in the shared PostGIS). LINK-LEVEL
  exposure: the Settings tab's per-collection `serving_enabled` toggle
  (migration 019) only controls whether the collection page advertises the
  endpoints — nothing gates the services until I-1. Platform
  `/api/assets/...` hrefs are not resolvable by the tiler (I-68). Full
  design + caveats: `docs/serving.md`.

Users configure additional catalogs in the `/catalogs` page (localStorage).
The same PostgreSQL instance (port 5433) backs the Astro app's extension
storage (`stac_higher.extensions`); `DATABASE_URL` overrides the default
connection string. Migrations run on the first API request via middleware.

Astro server routes:

| Route | Method(s) | Purpose |
|---|---|---|
| `/api/proxy` | ALL | CORS proxy (`X-Proxy-Target` + `X-Proxy-Endpoint` headers) |
| `/api/extensions` | GET, POST | List / create extensions |
| `/api/extensions/[id]` | GET, PUT, DELETE | Get / update / delete an extension |
| `/api/extensions/[id]/schema` | GET | Serve extension as JSON Schema |
| `/api/extensions/import` | POST | Fetch + store external JSON Schema |
| `/api/extensions/preview` | POST | Preview external schema metadata |
| `/api/extensions/resolve-schema` | POST | Fetch + cache a JSON Schema (5-min TTL) |
| `/api/auth/login` | GET | Start OIDC login (PKCE redirect to the IdP) |
| `/api/auth/callback` | GET | OIDC redirect URI — code exchange, sets the session cookie |
| `/api/auth/logout` | GET | Clear session + IdP end-session redirect |
| `/api/auth/me` | GET | Current canonical identity (`locals.auth`) |
| `/api/audit` | GET | Paginated audit log (operator: own groups; admin: all) |
| `/api/connections` | GET, POST | List (member+: own groups; admin: all) / create (operator+) connections — credentials write-only, never returned |
| `/api/connections/[id]` | GET, PUT, DELETE | Get / update / delete a connection (group-owned; PUT replaces credentials wholesale) |
| `/api/connections/[id]/test` | POST | Request a connectivity test (inserts a `connection_checks` row the pipeline drains — ADR 0004) |
| `/api/connections/[id]/checks/[checkId]` | GET | Poll a test request |
| `/api/connections/[id]/host-key/reset` | POST | Clear the TOFU host-key pin so the next test re-pins (ssh/sftp) |
| `/api/uploads` | POST | Mint presigned PUT URLs for asset uploads (operator+). Body **with** `item` = canonical mode (trusted UI, ADR 0005 — returns `/api/assets/...` hrefs); body **without** `item` = staged push mode (Phase 7 §4.1 — presigns into `staging/`, inserts a `staged_uploads` ledger row, returns `staging://` hrefs; requires `externally_writable`, not `archived`, owning-group membership) |
| `/api/uploads/[uploadId]` | GET | Poll a staged-upload session (`pending → finalizing → finalized \| rejected \| expired` + `result`) — the API-visible outcome of a push. Authenticated; admin, session group, or creator; others 404 — Phase 7 |
| `/api/assets/[collection]/[item]/[asset]` | GET | Authorize → 302 to a short-lived presigned URL for the canonical asset object (`{asset}` = filename) |
| `/api/collections/[id]/settings` | GET, PUT | Collection platform settings (ownership, `externally_writable`, retention/GC knobs, `archived`): GET member+, PUT operator+ audited (`collection_settings`) with the ADR 0003 group rules — M2-E |
| `/api/collections/[id]/settings/impact` | GET | Counted dry-run for the Settings warn-and-proceed dialog (`?retention_days=N` \| `?archived=true` → total/expired item counts; null counts when pgstac is absent) — M2-F, ADR 0011 |
| `/api/collections/[id]/connections` | GET, POST | List / create ingest associations for a built-in-catalog collection (member+ scoped list; operator+ create, group-owned — Phase 4) |
| `/api/collections/[id]/connections/[assocId]` | GET, PUT, DELETE | Get / update (enabled, `config`, expectation) / delete an ingest association |
| `/api/collections/[id]/connections/[assocId]/backfill` | POST | Request a backfill of existing items into a deliver association (operator+, audited; inserts a `delivery_backfills` row the pipeline drains — Slice C) |
| `/api/collections/[id]/connections/[assocId]/backfills/[backfillId]` | GET | Poll a backfill request |
| `/api/collections/[id]/connections/[assocId]/deliveries` | GET | Delivery status for a deliver association (member+): recent `delivery_log` rows + per-status counts — Slice D |
| `/api/collections/[id]/connections/[assocId]/deliveries/[deliveryId]/redeliver` | POST | Dead-letter recovery (operator+, audited `redeliver`): flip a `dead` row back into the retry path; the pipeline's retry sweep requeues it — Slice D |
| `/api/alerts` | GET | List alerts (member+: own groups via the alert's connection; admin: all). Filters: `?state=firing\|acknowledged\|resolved\|open`, `?limit` — M2-B |
| `/api/alerts/[id]/ack` | POST | Acknowledge a firing alert (operator+, audited `ack`; suppresses notification, not detection — the pipeline keeps bumping `last_seen`) |
| `/api/alerts/[id]/resolve` | POST | Manually resolve an open alert (operator+, audited `resolve`); if the condition persists the monitor raises a NEW row, which re-notifies |
| `/api/alerts/unread` | GET | The caller's unread firing-alert count (member+; feeds the M2-D header bell) — M2-C |
| `/api/alerts/read` | POST | Advance the caller's own read watermark (member+; deliberately NOT operator-gated/audited — personal UI state) — M2-C |
| `/api/channels` | GET, POST | List (member+: own groups; admin: all) / create (operator+) per-group notification channels (`in_app` \| `webhook`); webhook signing secret is write-only (`has_secret`) — M2-C, ADR 0010 |
| `/api/channels/[id]` | GET, PUT, DELETE | Get / replace-config / delete a channel (group-owned; PUT replaces `config` wholesale, kind+group immutable) |
| `/api/monitoring/history` | GET | Daily flow-stats strip from `flow_stats_daily` (`?subject_kind=association\|process&subject_id&days`, window clamped server-side); member+ via the subject's owner — M5-F |
| `/api/processes/[id]/runs/[runId]/log` | GET | Authorize → 302 to a short-lived presigned URL for the run log (member+ of the OWNING GROUP — stricter than the asset route, since a run log is arbitrary operator output). A run with no `log_ref` is a 404 — M5-F |
| `/api/monitoring/graph` | GET | The pipeline graph: typed nodes (connection / collection / process) + edges (ingest, deliver, process_source, process_output), member+ scoped. Shares `lib/graph/*` with the M5-D cycle check, so the picture and the write gate cannot disagree — M5-E |
| `/api/monitoring/flows` | GET | Cross-collection association list with `flow_stats` + expectation (member+: own groups; admin: all) — feeds `/monitoring` (M2-D) |
| `/api/processes` | GET, POST | List (member+: own groups; admin: all) / create (operator+, audited) group-owned processes — Phase 9 M5-A |
| `/api/processes/[id]` | GET, PUT, DELETE | Get / update / soft-delete a process. `current_revision` is NOT updatable — only a deploy moves it |
| `/api/processes/[id]/revisions` | GET, POST | List immutable revision snapshots / **deploy** (operator+, audited `deploy`): insert a revision + repoint `current_revision` in one transaction. `runtime.kind: container` is refused this slice (ADR 0013) |
| `/api/processes/[id]/sources` | GET, POST | List / attach a trigger source (operator+ who can also manage the collection; archived collections refused) |
| `/api/processes/[id]/sources/[sourceId]` | PUT, DELETE | Update trigger/expectation/enabled, or detach. `collection_id` is immutable (unique key + M5-D cycle edge) |
| `/api/processes/[id]/outputs` | GET, POST | List / attach an output collection (operator+, same collection rules) |
| `/api/processes/[id]/outputs/[outputId]` | DELETE | Detach an output; already-published items are untouched |
| `/api/processes/[id]/test` | POST | Request a test run (operator+, audited `test`) — inserts a `process_checks` row the pipeline drains (ADR 0004); 409 when nothing is deployed |
| `/api/processes/[id]/checks/[checkId]` | GET | Poll a test-run request (member+ of the owning group) |
| `/api/processes/[id]/runs` | GET | The run ledger (member+), newest first; `?limit` clamped server-side |
| `/api/processes/[id]/runs/[runId]/rerun` | POST | Dead-run recovery (operator+, audited `rerun`): flip a `dead` row back to `queued` for the pipeline's run tick. Only dead rows; a non-dead run is a 409 naming its status. The pinned revision is NOT changed — a re-run re-executes what failed — M5-C |
| `/api/catalog/[...path]` | POST, PUT, PATCH, DELETE | BFF for built-in-catalog writes (ADR 0008): transaction endpoints only; operator+, audited. Session callers get the session access token injected server-side; **bearer callers get their own token forwarded — the Phase 7 §4.3 brokered push path** (precondition set on every bearer write, synchronous staged pre-validation, `prior_item` snapshot on PUT, staged PATCH → 400, bearer collection-create → 403; `X-BFF-Auth` stamped when `CATALOG_BFF_SHARED_SECRET` is set). Reads stay direct. Client docs: `docs/push-ingest.md` |

**Auth**: OIDC login with a claims-mapping layer and a dev-bypass mode
(static identity, default in dev — unit tests/e2e need no IdP). Middleware
exposes `locals.auth` to all server routes. Full env-var reference and flow
details: `docs/auth.md`.

**RBAC & audit**: the permission guard in `src/middleware.ts` requires the
`operator`/`admin` role for API mutations (extensions + connections CRUD;
reads stay open except the auth-required connections/audit surfaces) and
writes one append-only `stac_higher.audit_log` row per gated mutation
(allowed or denied) plus login/logout. The dev-bypass identity is an
operator, so existing flows keep working without login. Details:
`docs/auth.md` ("RBAC & audit").

**Connections (Phase 2)**: group-owned ingest/delivery endpoints in
`stac_higher.connections` with write-only AES-256-GCM-encrypted credentials
(`CREDENTIALS_MASTER_KEY` env — see `docs/connections.md` for the dev key
command) and TOFU host-key pinning. Test-connection bridges to the pipeline
through `stac_higher.connection_checks`
(`docs/decisions/0004-app-pipeline-bridge.md`).

**Object storage & asset service (Phase 3)**: item asset bytes live in
platform object storage (MinIO locally, `stac_higher` bucket / S3 in cloud) and
are reached only through the app. `app/src/lib/storage/` signs URLs **offline**
(the app never streams bytes): `GET /api/assets/{collection}/{item}/{filename}`
authorizes then 302s to a presigned URL (via `resolveAssetTarget`, the
`reference`-mode seam); `POST /api/uploads` (operator+) mints presigned PUTs into
canonical storage. Key layout is §5.3. App env: `S3_ENDPOINT` (must be
**browser-reachable** — presigning is offline), `S3_BUCKET`, `S3_ACCESS_KEY_ID`,
`S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_FORCE_PATH_STYLE` (MinIO defaults work
locally). The pipeline sweeps abandoned `staging/` uploads via a TTL cleanup job
(`STAGING_*` env). Details: [ADR 0005](docs/decisions/0005-asset-service.md).

**Ingest associations (Phase 4, Slice A)**: `stac_higher.collection_connections`
(migration 005) wires a connection to a built-in-catalog collection as an ingest
source with a §5.1 `config`; `stac_higher.ingest_files` is the per-file ledger
the pipeline maintains. The app owns both tables' DDL and writes associations
(`app/src/lib/associations/*`, `/api/collections/[id]/connections*`); the
pipeline (Slice B) reads associations and writes the ledger, never DDL (ADR
0001). The ingest `config` Zod schema (`associations/schemas.ts`) is the
cross-runtime contract with the pipeline. Golden fixtures for every
cross-runtime config shape live in `tests/contract-fixtures/` and are consumed
by **both** the vitest and pytest suites — **a new or changed cross-runtime
shape ⇒ a new/updated shared fixture** (see that directory's README for the
format and semantics). Group ownership is enforced in-route
(operator+ to mutate; `reference` storage mode is s3-only). UI: the **Data flow**
tab on built-in-catalog collection pages.

**Alerting & notifications (M2-B/M2-C, ADR 0010)**: the pipeline's
`flow_monitor` reconciles `stac_higher.alerts` each minute (raise / re-fire /
auto-resolve, deduped per `(source, kind, connection, association, channel,
collection)` — the collection anchor joined in Phase 7's migration 021);
the app owns the DDL (migrations 014/015) plus the audited `ack`/`resolve`
verbs. Per-group `notification_channels` (`in_app` | `webhook`) are CRUD'd at
`/api/channels*`; in-app delivery is the alerts row + the per-user
`alert_reads` watermark (`/api/alerts/unread`, `/api/alerts/read`). Webhook
dispatch is PIPELINE-side (`pipeline/notify/`) behind the connections egress
policy — never widen the app's `safeFetch` for it — durable via the
`notification_deliveries` ledger (sweep retry → dead-letter → channel-anchored
`webhook_failed` alert; HMAC-signed when the channel config has a `secret`).
The webhook `config` is a cross-runtime contract
(`webhook-channel-config.json` fixture). UI (M2-D): the **`/monitoring`** page
(alert list with ack/resolve, per-association flow telemetry via
`/api/monitoring/flows`, channel management) and the **header alert bell**
(unread firing count; opening /monitoring advances the read watermark).

**Retention & GC (M2-F, ADR 0011)**: `stac_higher.asset_gc` (migration 017)
is the single marked-then-collected queue for every byte-deletion path —
retention expiry, item delete, collection delete, archive. The pipeline's
five-minute `retention_gc` sweep expires items per `collection_settings`
(mark the §5.3 item prefix FIRST, then `pgstac.delete_item`; delete events
never propagate to destinations) and `asset_collect` deletes due prefixes
from the platform bucket after the grace window. The app marks on BFF
item/collection deletes (closes I-51's GC half) and enforces `archived`
(no item writes, no new associations; the sweep expires everything). Since
Phase 7 the dispatcher ALSO marks on claiming a `delete` outbox event —
covering direct-to-proxy deletes — so an item delete can be marked twice
(app BFF + pipeline dispatcher); the open-key unique index makes the dual
`item_delete` markers idempotent by design.
Reference-mode association delete now removes its reference-backed items
(ADR 0009 question settled). Nothing is deleted on an unconfigured platform.

**Table hygiene (M2-G, ADR 0012)**: `item_events` and `audit_log` are
monthly-partitioned (migration 018, attach-don't-copy; `runMigrations()`
reconciles partitions two months ahead). `delivery_log` / `ingest_files` /
`connection_checks` are deliberately NOT partitioned (UNIQUE-key upsert
models) — the pipeline's hourly `history_retention` sweep prunes them
conservatively (checks by age; ledger rows only for soft-deleted
associations or itemless terminal deliveries; stranded running checks on
deleted connections flipped to failed). Audit rows die only by partition
DETACH+DROP.

**Push ingest (Phase 7)**: external clients with a bearer token (client-
credentials JWT accepted on `/api/*` — `docs/auth.md`) push items into
collections flagged `externally_writable`: staged mint (`POST /api/uploads`
without `item` → presigned PUTs into `staging/` + a `stac_higher.
staged_uploads` ledger row, migration 020) → item POST/PUT with
`staging://{upload_id}/{filename}` hrefs via the **brokered BFF route** (the
documented default; direct-to-proxy is supported but discouraged) → the
dispatcher routes staged-item events to the pipeline's **finalize**
(`pipeline/finalize/` — the ADR 0014 producer-parameterized seam: validate →
checksum → move staging→canonical → rewrite hrefs → pypgstac upsert; no
producer branching in the steps) → the client polls
`GET /api/uploads/[uploadId]`. Enforcement floor: the ADR 0015 proxy write
policy (`services/proxy-policy/`, enforced overlay only) gates direct writes
by role + the `externally_writable` set, exempting app writes via
`X-BFF-Auth` (`CATALOG_BFF_SHARED_SECRET`). Rejections land in the ledger
with pinned `PUSH_REJECTION_REASONS` strings (contract fixtures
`staged-asset-href.json` / `push-upload-status.json` / `alert-kinds.json`)
and raise the collection-anchored `push_rejected` alert (migration 021).
Client contract + failure semantics: **`docs/push-ingest.md`**.

**Processes (Phase 9, M5)**: operator-authored transforms. `processes` /
`process_revisions` (immutable snapshots; deploy = insert + repoint) /
`process_sources` (item_event | cron triggers) / `process_outputs` /
`process_runs` / `process_checks` (migrations 022/023). The app owns the DDL
and the CRUD (`/api/processes*`); the PIPELINE triggers, executes and records.
Execution is container-per-run behind the `Executor` seam
(`pipeline/process/`, ADR 0013) — the run's environment holds only its
revision's env, run-scoped STS credentials for `staging/runs/{run_id}/`, and
its code; never the DB URL, master key or platform keys. Outputs publish
through the ADR 0014 `finalize(process_run)` producer hooks
(`finalize/process_run.py`), so delivery composes off the resulting outbox
event for free. A per-process `max_runs_per_hour` ceiling defers-and-coalesces
rather than dropping (§7), and write-time cycle refusal over
collection↔process edges (`lib/graph/edges.ts`, I-64) blocks the loops that
ARE decidable. **Inputs (G-2, ADR 0018)**: before launch the pipeline stages
the triggering items into `staging/runs/{run_id}/inputs/{batch_id}/` with a
`manifest.json` (fixture `process-input-manifest.json`; env
`STAC_HIGHER_INPUT_PREFIX` / `STAC_HIGHER_INPUT_MANIFEST`) — platform-held
assets are read in place via source-collection read grants in the STS session
policy, remote ones are fetched through the owning reference-mode
association's adapter (else an egress-checked public GET); finalize skips
`inputs/`. Every revision carries `runtime.network` capped by
`PROCESS_NETWORK_MAX` (slice 1: `isolated` only). Process-author contract:
**`docs/processes.md`**. Alerting (M5-E): the flow
monitor owns `process_stalled` (per SOURCE, I-63), `process_failed` and
`process_rate_limited` (per process) — all three evaluated as observed
conditions, anchored via migration 024's `alerts.process_id`/`source_id`.
NOTE the four-place lockstep for that identity: the CHECK, the open-dedup
index, both pipeline `ON CONFLICT` targets, AND `sync_alerts`' auto-resolve
comparison.

**Service telemetry (M2-H)**: Prometheus exposition at `GET :8083/metrics`
(`pipeline/metrics.py`): per-job run/duration/outcome (wrapped centrally at
Procrastinate registration), ingest stage counters at the flow-stats choke
points, delivery terminal counters + latency histogram, webhook and alert
counters. No scraper in compose (spec §8). Pipeline log data goes in
`extra={...}` structured fields, never interpolated into the message.

Outbound server fetches go through `safeFetch` (blocks private/loopback targets;
for dev against local pgstac set `SAFE_FETCH_ALLOW_HOSTS=localhost,127.0.0.1` in
`.env.local`; silence logs with `SAFE_FETCH_LOG=0`). `/api/proxy` rejects
`Sec-Fetch-Site: cross-site`; optional `PROXY_AUTH_TOKEN` enforces an
`X-Proxy-Auth` header.

**Extension data model**: rows in `stac_higher.extensions` — `id` (UUID), `name`,
`prefix`, `version`, `description`, `schema` (JSONB), `source` (`local`/`external`),
`source_url`. Attaching an extension adds its schema URL to `stac_extensions`;
`ExtensionFields` renders RJSF fields; on save, properties merge into
`item.properties` (items) or `collection.summaries` (collections).

## Workflow — Worktree Isolation (Mandatory)

AI work lives on `ai/main` and worktree branches off it. **Never commit directly
to `main`** — that branch is human-reviewed integration.

### Solo tasks
1. **Start**: create a worktree off `ai/main` with a descriptive branch name:
   `git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main`
2. **Work**: make changes in the worktree, commit to the worktree branch. Run
   `npm run verify` (after `npm install` in the worktree) before declaring done.
3. **Merge**: when complete and verify passes:
   `git checkout ai/main && git merge ai/<slug> --no-ff`
4. **Cleanup**: `git worktree remove .claude/worktrees/<slug>` and delete the
   branch. Push `ai/main` to origin if running unattended.

### Team tasks
Multi-agent orchestration is harness-specific — see your harness's file (for
Claude Code: `CLAUDE.md` "Team tasks" and `.claude/prompts/ai-loop.md`). The
harness-neutral invariants:
- Each teammate works in its own worktree off `ai/main`.
- Teammates run `npm run verify` **only** — never e2e, never the dev server,
  never Docker (see the contention rule below).
- The lead merges all branches into `ai/main` after teammates finish, then runs
  verify and (if UI flows changed) e2e serially on `ai/main`.

### Promoting `ai/main` → `main`
The AI does not merge into `main`. To promote, open a PR `ai/main → main` for
human review. After anything lands on `main`, sync it back with
`git checkout ai/main && git merge main --no-ff` — the only legitimate path from
`main` into `ai/main`.

### Rules
- Base branch is always `ai/main`. Create worktree branches from it.
- **Singleton resources**: the dev server (:4321), the pgstac backend (:8082),
  and the e2e suite (serial by design — shared DB, `workers: 1`) are shared. Only
  ONE process may run the dev server or e2e at a time. In team work, that is the
  lead, after merging.
- **Merge conflicts**: attempt to resolve — read both sides, understand intent,
  produce a correct merge. STOP and report only if the sides genuinely
  contradict each other.
- **`package-lock.json` conflicts**: `git checkout --theirs package-lock.json &&
  npm install && git add package-lock.json`. Regenerating from the merged
  `package.json` files is the correct fix — never hand-edit the lockfile.
- If verify fails after a merge, fix on `ai/main` and commit the fix there.

## Solo Agent Loop (TODO.md)

When iterating autonomously:
1. Pick the **first unchecked item** (`- [ ]`) in `TODO.md`. No cherry-picking.
2. Read the files the task references before changing anything. Reuse existing
   components (`StacMap`, `FootprintLayer`, `ExtentLayer`, `BboxInput`, …).
3. Implement in a worktree per the workflow above. One task per iteration; keep
   changes minimal and focused.
4. `npm run verify` must pass. Run e2e (`run-e2e` skill) if the task touched
   flows the suite covers.
5. Merge to `ai/main`, mark the task `- [x]` in `TODO.md`, and append any
   discovered follow-ups to the appropriate section.

Additional rules: no new dependencies without clear need; never edit shadcn
primitive files by hand (use `npx shadcn@latest add <component>`; shared package
if the app consumes it from `@stac-higher/shared`); don't break existing pages
when changing shared components.

## Gotchas

- Full-project `npx astro check` from the repo root is meaningless (no
  `src/pages` there; it reports only type-clash noise from
  `app/node_modules/astro` internals — I-8; under Astro 6 it OOM'd outright).
  The app-scoped check (`npm run check` from `app/`) is fast and green —
  `npm run verify` runs it first, matching CI. Never run the check from the
  repo root.
- Astro 7 auto-daemonizes `astro dev` when it detects an AI-agent
  environment (manage with `astro dev stop`/`status`/`logs`). Playwright's
  webServer needs a foreground process — `playwright.config.ts` sets
  `ASTRO_DEV_BACKGROUND` in the webServer env to disable the auto-detection;
  keep it. Set the same env var if you need a foreground dev server yourself.
- The Zod v4 → `zodResolver` type inference mismatch forces an `as any` cast on
  form resolvers — this is a known pattern, not a bug to fix.
- `extensions.spec.ts` and `proxy.spec.ts` (e2e) require the Docker backend on
  :8082. Full e2e preconditions and selector gotchas: `run-e2e` skill.
- Theme is **light** by default (ADR 0017); `Layout.astro` applies the theme
  class pre-hydration in `<head>` to prevent flash. That inline script and the
  `$theme` persistentAtom default in `packages/shared/src/stores/uiStore.ts`
  read the same `stac-theme` key and must stay in lockstep. Toggle via
  `toggleTheme()` from `@stac-higher/shared`.

## Agent Skills

Task playbooks live under `.agents/skills/`, following the
[Agent Skills](https://agentskills.io/) open standard (one folder per skill,
`SKILL.md` with `name` + `description` frontmatter). Codex/OpenCode/Cursor read
`.agents/skills/` natively; Claude Code reads it through the `.claude/skills`
symlink. Read the relevant `SKILL.md` *before* starting a matching task.

- **`project-conventions`** — read first for any non-trivial change: island pattern, three-tier state, import rules, form pattern, shadcn rules.
- **`new-component`** — adding a React component (app vs. shared placement, conventions).
- **`new-page`** — adding an Astro page + React island.
- **`new-test`** — writing unit / component / e2e tests following project patterns.
- **`add-stac-endpoint`** — adding a STAC API client function + TanStack Query hook.
- **`run-e2e`** — running the Playwright suite: preconditions, filters, and its selector/CSRF/IPv6 gotchas.

See `docs/AI-STRATEGY.md` for how this file, the skills, and per-harness shims
fit together.

## Documentation

Project docs live in `docs/` (index: `docs/README.md`), organized into three
tracks — read the relevant one before non-trivial work and update it after:

- **`docs/FEATURES.md`** — catalog of what's built, per phase, with entry points.
- **`docs/decisions/`** — ADRs (index + invariants in `docs/decisions/README.md`).
  Add the next-numbered ADR for any significant, hard-to-reverse choice.
- **`docs/ISSUES.md`** — carried-forward work, known limitations/residual risk,
  and deferrals. Log new gaps here rather than leaving them implicit.
