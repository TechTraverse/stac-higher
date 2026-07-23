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
| [0008](0008-bff-catalog-writes.md) | Browser catalog writes go through an app BFF route | accepted (implementation pending) | 5 |

## Key invariants these establish

- **0001** — The **app** owns and creates all `stac_higher.*` tables (via migration middleware); the Python pipeline only reads them and UPDATEs a fixed set of columns. It never runs DDL.
- **0002** — The auth-proxy enforces authenticated *transactions* + audience; per-collection **read-visibility** filtering is explicitly out of scope for config-only enforcement (needs OPA / a custom filter factory). Tracked in [`../ISSUES.md`](../ISSUES.md).
- **0003** — A collection with no `collection_settings` row is **unowned + public** by default; no backfill (out-of-band pgstac creation would race).
- **0004** — Test-connection crosses the app↔pipeline boundary via a **request table** (`connection_checks`), not direct queue coupling — backend-agnostic; revisit for Phase 5 backfill/redeliver triggers.
- **0005** — Item asset hrefs point at `/api/assets/{collection}/{item}/{filename}` (last segment is the **filename**, not the STAC asset key) → RBAC → 302 to a presigned URL via `resolveAssetTarget` (the `reference`-mode seam). Manual UI uploads write **direct to canonical** (trusted RBAC'd writer); staging+finalize is the untrusted-push path (Phase 7). Asset-read authz is authentication-only until read-visibility (0002/I-1) lands.
- **0006** — EXTRACT/ITEMIZE metadata libraries are pinned (`rio-stac==0.12.0`, `pystac==1.15.1`, `rasterio>=1.5,<2`, `defusedxml>=0.7.1`, `stac-pydantic==3.6.0`, `pypgstac[psycopg]==0.9.11`); rasterio's bundled-GDAL wheels mean **no system GDAL, no Dockerfile change**. The `ghcr.io/stac-utils/pgstac` image is pinned to **v0.9.11** to stay in lockstep with the pinned `pypgstac` client — re-test the upsert path on any pgstac bump. ITEMIZE validates with the *core* `stac_pydantic.Item` (not the API variant) as an offline, core-structural gate only; `pypgstac`'s `Methods.upsert` writes item data only (no DDL), keeping ADR 0001's invariant intact.
- **0007** — The **app** owns the outbox trigger on `pgstac.items` (extends 0001: a trigger on a pgstac table it does not own is licensed provided it writes only into `stac_higher` and the attachment is `IF EXISTS`-guarded + reconciled on every `runMigrations()`). Row-level trigger form — the only one that catches partition-direct/bulk writes. The outbox `op` must never be used to distinguish first-delivery from redelivery (that's `delivery_log`'s job).
- **0008** — Browser sessions write to the built-in catalog **only through the app BFF route** (server-side session-token injection; token never reaches page JS). External clients keep direct bearer auth at the proxy. Catalog mutations thereby pass the permission guard and land in `audit_log`. Until implemented: I-50.

## Adding an ADR

1. Copy the format of an existing record: a `# ADR NNNN — Title` heading, then **Status**, **Context**, **Decision**, **Consequences** (and **Revisit** if the choice is expected to be reconsidered).
2. Number sequentially (next: `0009`).
3. Add a row to the index above and, if it changes an invariant, note it in "Key invariants."
4. ADRs are immutable once accepted — supersede with a new ADR rather than editing history; mark the old one `superseded by NNNN`.
