# Backend — local stack, app environment, and Astro API routes

The operational reference for the platform behind the STAC client: what
`docker compose up -d` starts, the environment the Astro app reads, and every
Astro server route with its access rule. Per-area design docs are linked from
[`README.md`](README.md); this file is the map, not the narrative.

## Local stack (`docker compose up -d`, repo root)

| Service | Port | Notes |
|---|---|---|
| **pgstac** (PostgreSQL + PostGIS) | :5433 | Also backs the app's `stac_higher.*` schema (`DATABASE_URL` overrides the default connection string; app migrations run on the first API request via middleware). The `pgstac-migrate` one-shot migrates a persisted volume's pgstac schema to the pinned version on every `up` — the pgstac image only installs its schema on fresh volumes (ADR 0001, I-54). |
| **stac-fastapi-pgstac** | :8082 | Transaction extension enabled (full CRUD). |
| **stac-auth-proxy** | :8081 | In front of stac-fastapi. Pass-through by default (`DEFAULT_PUBLIC=true`, no login needed). The client's **built-in catalog** points here (`PUBLIC_BUILTIN_CATALOG_URL`, default `http://localhost:8081`) and is seeded as an undeletable entry on `/catalogs`. |
| **Keycloak** | :8180 | admin/admin, realm `stac-higher` imported from `infra/keycloak/realm-stac-higher.json`. Realm import is **skipped once the realm exists in the persisted volume** — edits to the realm file only take effect after `docker compose down -v`. |
| **MinIO** | :9000 API / :9001 console | minioadmin/minioadmin, bucket `stac-higher`. |
| **pipeline** (`services/pipeline`, Python) | :8083 | Queue worker + scheduler; `/health`, Prometheus `/metrics` (`docs/monitoring.md`). Reference: `services/pipeline/README.md`. |
| **docker-socket-proxy** | internal | The least-privilege seam through which the pipeline launches process-run containers (`CONTAINERS=1 POST=1`, everything else 403 — ADR 0013). The pipeline never holds `/var/run/docker.sock`; `DOCKER_HOST` must be the proxy or the executor refuses to start. Runs attach to the internal `process-runs` network (or `none`, the default) and execute the platform image from `services/process-runtime/` — two images built together by `docker buildx bake -f services/process-runtime/docker-bake.hcl` from the repo root: the base and the `stactools` variant carrying the built-in extractor library (X-2; the bake file also supplies the `fixtures` context through which `tests/contract-fixtures/builtin-extractors.json` reaches the pipeline and runtime images). |
| **titiler-pgstac** | :8084 | OGC API Tiles for rasters, per STAC collection off pgstac. Derived image (`infra/titiler/`) maps platform `/api/assets/...` hrefs to the bucket (G-4). |
| **tipg** | :8085 | OGC API Features/Tiles for vector tables in the shared PostGIS. |

OGC serving is **link-level** exposure only: the per-collection
`serving_enabled` toggle (migration 019) controls whether the collection page
advertises the endpoints; nothing gates the services until I-1. Two UI
surfaces read it — the item page's raster overlay (G-5) and the product page's
**Preview** tab. Design + caveats: [`serving.md`](serving.md).

### Auth-enforced overlay

Opt-in enforcement (authenticated transactions + audience check, reads still
public, plus the ADR 0015 per-collection `externally_writable` write policy
via a derived proxy image):

```sh
export CATALOG_BFF_SHARED_SECRET=$(openssl rand -hex 32)   # or .env; mandatory — the app process needs it too
docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --build --wait
```

See [`decisions/0002-auth-proxy-enforcement.md`](decisions/0002-auth-proxy-enforcement.md)
and [`decisions/0015-proxy-write-policy.md`](decisions/0015-proxy-write-policy.md).

## App environment

| Variable | Purpose |
|---|---|
| `PUBLIC_BUILTIN_CATALOG_URL` | The built-in catalog (default `http://localhost:8081`). |
| `DATABASE_URL` | Overrides the default `stac_higher` connection string (port 5433). |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_FORCE_PATH_STYLE` | Object storage (MinIO defaults work locally). `S3_ENDPOINT` must be **browser-reachable** — `app/src/lib/storage/` presigns offline and never streams bytes (ADR 0005). |
| `CREDENTIALS_MASTER_KEY` | AES-256-GCM key for write-only connection credentials — dev key command in [`connections.md`](connections.md). |
| `CATALOG_BFF_SHARED_SECRET` | Stamps `X-BFF-Auth` on BFF writes so the enforced proxy exempts app writes (ADR 0015). |
| `SAFE_FETCH_ALLOW_HOSTS` | `safeFetch` blocks private/loopback targets; for dev against local pgstac set `SAFE_FETCH_ALLOW_HOSTS=localhost,127.0.0.1` in `.env.local`. `SAFE_FETCH_LOG=0` silences its logs. |
| `PROXY_AUTH_TOKEN` | Optional; makes `/api/proxy` require an `X-Proxy-Auth` header. `/api/proxy` always rejects `Sec-Fetch-Site: cross-site`. |
| Auth (`AUTH_MODE`, OIDC issuer/client, claims mapping) | Full reference: [`auth.md`](auth.md). Dev-bypass is the default in dev — a static operator identity, so unit tests and e2e need no IdP. |
| `STAGING_*` | Pipeline-side TTL sweep of abandoned `staging/` uploads. |
| `PROCESS_HARDWARE_PROFILES_FILE` | Path to the deployment's hardware-profile document (K-1); validated strictly at deploy time and served (minus `backend`) on `GET /api/processes/hardware-profiles`. Unset means the repo checkout's `infra/hardware-profiles/local.json`. |
| `PROCESS_IMAGE_POLICY_FILE` | Path to the deployment's image policy (C-1, container-images spec §7): allowed registries, size cap, block rules, scan window. It is read only when a revision names a user image (`inline_python_on_image` / `container`). A missing or invalid file makes those deploys fail closed (503 `image_policy_unavailable`), and inline deploys never read it. Unset means the repo checkout's `infra/image-policy/default.json`. |

## Astro server routes

Access shorthand: **member+** = any member of the owning group (admin sees
all); **operator+** = `operator`/`admin` role, and the mutation writes an
`audit_log` row (see [`auth.md`](auth.md) "RBAC & audit"). App→pipeline
requests are rows the pipeline drains (ADR 0004).

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
| `/api/monitoring/graph` | GET | The pipeline graph: typed nodes (connection / collection / process) + edges (ingest, deliver, process_source, process_output), member+ scoped. Shares `lib/graph/*` with the M5-D cycle check, so the picture and the write gate cannot disagree — M5-E |
| `/api/monitoring/flows` | GET | Cross-collection association list with `flow_stats` + expectation (member+: own groups; admin: all) — feeds `/monitoring` (M2-D) |
| `/api/processes` | GET, POST | List (member+: own groups; admin: all) / create (operator+, audited) group-owned processes — Phase 9 M5-A |
| `/api/processes/hardware-profiles` | GET | The deployment's hardware profiles minus their backend blocks, plus the executor backend (member+; K-1) |
| `/api/processes/[id]` | GET, PUT, DELETE | Get / update / soft-delete a process. `current_revision` is NOT updatable — only a deploy moves it |
| `/api/processes/[id]/revisions` | GET, POST | List immutable revision snapshots / **deploy** (operator+, audited `deploy`): insert a revision + repoint `current_revision` in one transaction. `runtime.kind` is `inline_python` \| `inline_python_on_image` \| `container`. A kind 2/3 snapshot must name an approved, fresh, digest-equal image the process's group may use: otherwise **422** with `code` `image_not_approved` \| `image_stale` \| `image_group_mismatch` \| `image_digest_mismatch`, or **503** `image_policy_unavailable` when the policy cannot be read (C-1, ADR 0021) |
| `/api/processes/[id]/sources` | GET, POST | List / attach a trigger source (operator+ who can also manage the collection; archived collections refused) |
| `/api/processes/[id]/sources/[sourceId]` | PUT, DELETE | Update trigger/expectation/enabled, or detach. `collection_id` is immutable (unique key + M5-D cycle edge) |
| `/api/processes/[id]/outputs` | GET, POST | List / attach an output collection (operator+, same collection rules) |
| `/api/processes/[id]/outputs/[outputId]` | DELETE | Detach an output; already-published items are untouched |
| `/api/processes/[id]/test` | POST | Request a test run (operator+, audited `test`) — inserts a `process_checks` row the pipeline drains (ADR 0004); 409 when nothing is deployed |
| `/api/processes/[id]/checks/[checkId]` | GET | Poll a test-run request (member+ of the owning group) |
| `/api/processes/[id]/runs` | GET | The run ledger (member+), newest first; `?limit` clamped server-side |
| `/api/processes/[id]/runs/[runId]/rerun` | POST | Dead-run recovery (operator+, audited `rerun`): flip a `dead` row back to `queued` for the pipeline's run tick. Only dead rows; a non-dead run is a 409 naming its status, and an EXTRACTOR run is a 409 outright (its files are retried by the ingest sweep — G-6). The pinned revision is NOT changed — a re-run re-executes what failed — M5-C |
| `/api/processes/[id]/runs/[runId]/log` | GET | Authorize → 302 to a short-lived presigned URL for the run log (member+ of the OWNING GROUP — stricter than the asset route, since a run log is arbitrary operator output). A run with no `log_ref` is a 404 — M5-F |
| `/api/catalog/[...path]` | POST, PUT, PATCH, DELETE | BFF for built-in-catalog writes (ADR 0008): transaction endpoints only; operator+, audited. Session callers get the session access token injected server-side; **bearer callers get their own token forwarded — the Phase 7 §4.3 brokered push path** (precondition set on every bearer write, synchronous staged pre-validation, `prior_item` snapshot on PUT, staged PATCH → 400, bearer collection-create → 403; `X-BFF-Auth` stamped when `CATALOG_BFF_SHARED_SECRET` is set). Reads stay direct. Client docs: [`push-ingest.md`](push-ingest.md) |

## Extension data model

Rows in `stac_higher.extensions` — `id` (UUID), `name`, `prefix`, `version`,
`description`, `schema` (JSONB), `source` (`local`/`external`), `source_url`.
Attaching an extension adds its schema URL to `stac_extensions`;
`ExtensionFields` renders RJSF fields; on save, properties merge into
`item.properties` (items) or `collection.summaries` (collections).

## Where each area is documented

| Area | Doc |
|---|---|
| Auth, claims mapping, dev-bypass, RBAC & audit | [`auth.md`](auth.md) |
| Connections, credential encryption, TOFU host keys, app→pipeline bridge | [`connections.md`](connections.md), ADR 0004 |
| Object storage, asset service, key layout | ADR 0005 |
| Ingest associations, ledger, contract fixtures | `tests/contract-fixtures/README.md`, [`FEATURES.md`](FEATURES.md) Phase 4 |
| Alerts, channels, webhooks, retention & GC, table hygiene, metrics | [`monitoring.md`](monitoring.md), ADRs 0010–0012 |
| Push ingest (staged uploads, brokered BFF, finalize, rejection reasons) | [`push-ingest.md`](push-ingest.md), ADRs 0014/0015 |
| Processes, inputs, network profiles, extractors, process alerts | [`processes.md`](processes.md), ADRs 0013/0018 |
| OGC serving | [`serving.md`](serving.md) |
