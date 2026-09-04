# Architecture Decision Records

One file per significant, hard-to-reverse decision, capturing the context, the choice, and its consequences so future work doesn't relitigate settled ground or violate an invariant unknowingly.

## Index

| ADR | Title | Status | Phase |
|---|---|---|---|
| [0001](0001-migration-ownership.md) | Migration ownership for the shared Postgres | accepted | 0 |
| [0002](0002-auth-proxy-enforcement.md) | stac-auth-proxy enforcement scope | accepted | 1 |
| [0003](0003-preexisting-collections.md) | Default ownership for pre-existing collections | accepted | 1 |
| [0004](0004-app-pipeline-bridge.md) | App→pipeline bridge for test-connection: a request table | accepted | 2 |
| [0005](0005-asset-service.md) | Asset service: filename-keyed redirect route + direct-to-canonical UI uploads | accepted | 3 |
| [0006](0006-ingest-metadata-and-upsert.md) | Ingest metadata extraction + pgstac upsert library choices | accepted | 4 |
| [0007](0007-outbox-trigger-ownership.md) | Event-outbox trigger ownership + mechanism | accepted | 5 |
| [0008](0008-bff-catalog-writes.md) | Browser catalog writes go through an app BFF route | accepted (implemented) | 5 |
| [0009](0009-deletion-semantics.md) | Deletion semantics: soft-delete, retained history, warn-and-proceed | accepted (implemented — soft-delete pre-B-iii, GC half M2-F) | 5 |
| [0010](0010-alerting-notifications.md) | Alerting & notification model | accepted | 6 (M2-B/C) |
| [0011](0011-retention-gc.md) | Retention & GC: one marked-then-collected queue | accepted | 6 (M2-F) |
| [0012](0012-table-hygiene.md) | High-volume table hygiene: partition two, sweep three | accepted | 6 (M2-G) |
| [0013](0013-process-executor-isolation.md) | Process executor isolation | accepted (2026-08-29); cloud-backend recommendation superseded by 0019 | 9 (M5) |
| [0014](0014-process-output-path.md) | Process output path: staging + platform finalize | accepted (2026-08-29) | 9 (M5) |
| [0015](0015-proxy-write-policy.md) | Proxy write policy: custom filter factory enforcing `externally_writable` | accepted (2026-08-30) | 7 |
| [0016](0016-ogc-processes-conformance-posture.md) | OGC API — Processes conformance posture | accepted (2026-08-31) | 9 (post-gate) |
| [0017](0017-product-centric-ui-shell.md) | Product-centric UI shell, NOAA theme, terminology adoption | accepted (2026-08-31) | UI remodel |
| [0018](0018-process-inputs-and-network-profiles.md) | Process inputs (staged manifest + source-collection read grants) and network profiles | accepted (2026-09-01) | GOES loop (G-2) |
| [0019](0019-process-compute-kubernetes-kueue.md) | Process compute: Kubernetes Jobs + Kueue, hardware profiles as the portable vocabulary | accepted (2026-09-04; proposed 2026-09-02; supersedes 0013's cloud-backend half) | K queue / Phase 8 |

Proposed ADRs establish no invariants until accepted (via the Phase 9 design
spec); their draft invariants live inside the documents.

## Key invariants these establish

- **0001** — The **app** owns and creates all `stac_higher.*` tables (via migration middleware); the Python pipeline only reads them and UPDATEs a fixed set of columns. It never runs DDL.
- **0002** — The auth-proxy enforces authenticated *transactions* + audience; per-collection **read-visibility** filtering is explicitly out of scope for config-only enforcement (needs OPA / a custom filter factory). Tracked in [`../ISSUES.md`](../ISSUES.md).
- **0003** — A collection with no `collection_settings` row is **unowned + public** by default; no backfill (out-of-band pgstac creation would race).
- **0004** — Test-connection crosses the app↔pipeline boundary via a **request table** (`connection_checks`), not direct queue coupling — backend-agnostic; revisit for Phase 5 backfill/redeliver triggers.
- **0005** — Item asset hrefs point at `/api/assets/{collection}/{item}/{filename}` (last segment is the **filename**, not the STAC asset key) → RBAC → 302 to a presigned URL via `resolveAssetTarget` (the `reference`-mode seam). Manual UI uploads write **direct to canonical** (trusted RBAC'd writer); staging+finalize is the untrusted-push path (Phase 7). Asset-read authz is authentication-only until read-visibility (0002/I-1) lands.
- **0006** — EXTRACT/ITEMIZE metadata libraries are pinned (`rio-stac==0.12.0`, `pystac==1.15.1`, `rasterio>=1.5,<2`, `defusedxml>=0.7.1`, `stac-pydantic==3.6.0`, `pypgstac[psycopg]==0.9.11`); rasterio's bundled-GDAL wheels mean **no system GDAL, no Dockerfile change**. The `ghcr.io/stac-utils/pgstac` image is pinned to **v0.9.11** to stay in lockstep with the pinned `pypgstac` client — re-test the upsert path on any pgstac bump. ITEMIZE validates with the *core* `stac_pydantic.Item` (not the API variant) as an offline, core-structural gate only; `pypgstac`'s `Methods.upsert` writes item data only (no DDL), keeping ADR 0001's invariant intact.
- **0007** — The **app** owns the outbox trigger on `pgstac.items` (extends 0001: a trigger on a pgstac table it does not own is licensed provided it writes only into `stac_higher` and the attachment is `IF EXISTS`-guarded + reconciled on every `runMigrations()`). Row-level trigger form — the only one that catches partition-direct/bulk writes. The outbox `op` must never be used to distinguish first-delivery from redelivery (that's `delivery_log`'s job).
- **0008** — Browser sessions write to the built-in catalog **only through the app BFF route** (server-side session-token injection; token never reaches page JS). External clients keep direct bearer auth at the proxy. Catalog mutations thereby pass the permission guard and land in `audit_log`.
- **0009** — Nothing cascades into history: connections/associations **soft-delete** (`deleted_at`, credentials scrubbed at delete time); `ingest_files`/`delivery_log`/`connection_checks` rows are permanent passive records. Every destructive action is **warn-and-proceed** with a counted impact preview — no require-cleanup gates. Deleting a connection removes its reference-backed items (never leaves dead links); deleting a collection deletes real data files via the §6.5 GC path (implemented by M2-F, ADR 0011).
- **0010** — Webhook egress lives **pipeline-side** behind `resolve_pinned` (the connections egress policy); the app's `safeFetch` guard is never widened for notifications. Notification durability is ledger-shaped (`notification_deliveries` + sweep + dead-letter → channel-anchored `webhook_failed` alert), not queue-retry-shaped. Only NEW alert rows notify; in-app delivery is the alerts row + the per-user `alert_reads` watermark.
- **0011** — `asset_gc` is the ONLY path by which canonical bytes are deleted, and every mark carries a grace window from `gc_grace_days`. Mark-first-then-delete ordering is invariant (a crash must never orphan bytes). Sweeps touch only collections with declared retention or `archived` — an unconfigured platform deletes nothing. Archive expires all items ("delete the data, keep the record"); reference-mode association delete removes its items (aligned with connection delete). Since W-2 (2026-09-02) retention has TWO rules — an age cap and a count cap (`retention_max_items`, newest N by item datetime) — that UNION into the same `retention` marks on the same queue; the ADR is not amended by that.
- **0012** — `item_events`/`audit_log` are monthly-partitioned (attach-don't-copy; reconcile provisions two months ahead on every runMigrations). `delivery_log`/`ingest_files` are NEVER time-partitioned (their UNIQUE keys are the upsert model) — they age out via the conservative `history_retention` sweep, which only prunes soft-deleted-association rows and itemless terminal deliveries. Audit rows die ONLY by partition drop (DETACH+DROP — the sanctioned escape hatch past the append-only triggers).
- **0018** — A run's read access outside its own prefix is limited to the canonical prefixes of its **source collections** (`assets/{collection}/*`), granted read-only in the STS session policy — never by platform keys; remote inputs are staged INTO the run prefix by the platform (through the owning reference-mode association's adapter, else an egress-checked public GET) before any container exists, and finalize never treats `inputs/` as output. A revision's `network.level` never exceeds `PROCESS_NETWORK_MAX`; the pipeline enforces the cap at launch independently of the app's write gate (slice 1: `isolated` only).
- **0019** — Hardware is expressed ONLY as a named **profile** plus CPU/memory/GPU counts within its bounds; nothing operator-facing names a cloud, instance type or Kubernetes selector. Bounds and profile existence are enforced at the app's write gate AND at launch. A run never holds a pipeline worker slot for its duration (submit-then-reconcile). Kueue nominal quota never exceeds the node pool's provisioning ceiling for the same flavor. Run credentials cover the promised queue wait plus the timeout, else the run requeues without spending an attempt — no credential endpoint is reachable from inside a run.

## Adding an ADR

1. Copy the format of an existing record: a `# ADR NNNN — Title` heading, then **Status**, **Context**, **Decision**, **Consequences** (and **Revisit** if the choice is expected to be reconsidered).
2. Number sequentially (next: `0020`).
3. Add a row to the index above and, if it changes an invariant, note it in "Key invariants."
4. ADRs are immutable once accepted — supersede with a new ADR rather than editing history; mark the old one `superseded by NNNN`.
