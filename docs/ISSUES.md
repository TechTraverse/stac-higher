# Outstanding issues

Known gaps, residual risk, and deferrals — tracked honestly so they aren't mistaken for "done." Status: 🔴 open · 🟡 accepted/mitigated · 🟢 resolved · ⚪ deferred-by-design.

Each entry: what it is, why it exists, and where it's tracked. Close an entry by moving it to 🟢 with the resolving commit/PR, or delete it once shipped and documented elsewhere.

---

## Carried-forward work (committed, not yet done)

### I-1 · Per-collection read-visibility at the proxy 🔴
Phase 1 delivered authenticated transactions + audience validation, but **read-visibility filtering** (different groups see different collections) cannot be done with auth-proxy config alone — it needs OPA or a custom filter factory. Deferred out of Phase 1.
- Tracked in: [ADR 0002](decisions/0002-auth-proxy-enforcement.md); Phase 1 note in [`../ROADMAP.md`](../ROADMAP.md).
- Blocks: fully multi-tenant read isolation.

---

## Known limitations / residual risk (accepted, mitigated)

### I-2 · DNS-rebind residual for TLS endpoints 🟡
SFTP, plain FTP, and S3-over-http pin the resolved IP (rebind-proof). **FTPS control channels and S3-https keep the hostname** so TLS cert validation / SNI works, leaving a narrow DNS-rebind window between the egress check and the TLS connect. Mitigated by a fail-closed `resolve_pinned` recheck immediately before connect; the FTP PASV data-channel redirect is fully closed regardless.
- Tracked in: comments in `services/pipeline/.../adapters/{ftps,s3}.py`; found in the Phase 2 adversarial review.

### I-3 · Drain latency is ~1 minute, not ~10s 🟡
ADR 0004 targets a ~10s test-connection turnaround, but Procrastinate's periodic scheduler is **1-minute-granular**. The drain runs every minute and clears the whole pending backlog each tick, so worst-case start latency is ~60s. A true sub-minute drain needs a NOTIFY-woken consumer.
- Tracked in: [ADR 0004](decisions/0004-app-pipeline-bridge.md) "Revisit"; comment in `services/pipeline/.../jobs/drain.py`.

### I-4 · Adapter `list/get` live-verified for S3; SFTP/FTP still mock-only 🟡
The full `StorageAdapter` interface is implemented. `test()` is exercised live via the drain job, and **the S3 adapter's `list`/`get` are now live-verified** by the Phase 4 ingest e2e (2026-07-16): a file dropped in MinIO flowed poll → DISCOVER → GROUP → FETCH into canonical storage, byte-identical. The **SFTP/FTP** `list`/`get` and all adapters' `put`/`delete` remain covered only by unit tests with mocked clients — no live-server integration yet (SFTP/FTP live-exercise is a Slice B5 follow-up; FTPS blocked on arm64, I-6).
- Tracked in: here; `services/pipeline/tests/test_adapters.py`.

### I-5 · Zod v4 ↔ zodResolver `as any` cast 🟡
Form resolvers use an `as any` cast due to a Zod v4 / `@hookform/resolvers` type-inference mismatch. Known pattern, not a bug — don't "fix."
- Tracked in: `AGENTS.md` "Gotchas"; `project-conventions` skill.

---

## Test & infra gaps

### I-6 · FTPS not live-testable on arm64 🟡
The `ftps-test` server (`fauria/vsftpd`) is amd64-only and crashes under Rosetta on Apple Silicon. FTPS shares the `FtpAdapter` code path (only the TLS upgrade differs), which the live FTP test exercises, and is unit-tested — but FTPS-specific live validation needs an amd64 host.
- Tracked in: header comment in `infra/compose.test-servers.yml`; Phase 2 note in [`../ROADMAP.md`](../ROADMAP.md).

### I-7 · Pipeline jobs error noisily before tables exist ⚪
On a fresh DB the drain/health-sweep jobs log `UndefinedTable` each tick until the app's migration middleware creates `stac_higher.connections`/`connection_checks`. Harmless (they recover once tables exist) and correct per ADR 0001 (pipeline never creates tables), but noisy in a pipeline-first startup.
- Tracked in: here. Workaround for local pipeline-only testing: apply migration 004 first.

### I-8 · Full-project `npx astro check` OOMs 🟡
A pre-existing Vite/rolldown plugin type conflict between the repo root and `app/node_modules` OOMs a full-project `astro check`. Rely on `npm run verify` (build + vitest) and the scoped PostToolUse `astro check` hook instead.
- Tracked in: `AGENTS.md` "Gotchas".

---

## Deferred by design (later phases)

### I-9 · KMS credential provider ⚪
Credentials use a local `CREDENTIALS_MASTER_KEY` behind an `EncryptionProvider` seam. A KMS-backed provider arrives in Phase 8. — `app/src/lib/connections/crypto.ts`.

### I-10 · `stac-api` connection protocol ⚪
Reserved in the enum; create/update reject it and the adapter factory raises `NotImplementedError("reserved for a future release")`.

### I-11 · Audit-log partitioning & retention ⚪
`stac_higher.audit_log` is append-only and unpartitioned. Phase 6 adds time-partitioning + a compliance-driven retention job (partition maintenance must drop/re-create the append-only triggers per partition). — migration 003 comment.

### I-12 · `connection_checks` accumulation ⚪
Test-result rows are never pruned; a partial index keeps the drain's pending scan cheap, but the table grows. Retention/GC is Phase 6 hygiene.

---

## Phase 3 — asset service

### I-13 · Asset-read authorization is authentication-only 🟡
`GET /api/assets/...` requires an authenticated identity (unauthenticated → 403) but does **not** yet scope reads to the caller's groups / the collection's visibility — that is the same capability deferred as I-1 (read-visibility). Until it lands, any authenticated user can mint a download URL for any asset. In dev-bypass the static operator satisfies the check, so local flows work.
- Tracked in: [ADR 0005](decisions/0005-asset-service.md); depends on I-1 / [ADR 0002](decisions/0002-auth-proxy-enforcement.md).

### I-14 · Manual uploads go direct-to-canonical; no server-side validation ⚪
Item-form uploads presign straight into canonical storage (trusted RBAC'd writer, ADR 0005) — there is **no finalize step** validating/checksumming the bytes, and no staging quarantine. The untrusted external push path (staging → validate → move to canonical) is Phase 7; `stagingKey` + the TTL sweep already exist as its seam.
- Tracked in: [ADR 0005](decisions/0005-asset-service.md); ROADMAP §6.2.

### I-15 · Presign endpoint must be browser-reachable 🟡
The app signs URLs offline, so `S3_ENDPOINT` must be reachable by the **browser** that uses them. On the host, `http://localhost:9000` works. If the app is ever run **inside compose**, `S3_ENDPOINT` must be set to a browser-reachable host — never `http://minio:9000`, which the browser can't resolve. Defaults assume the host-run dev server.
- Tracked in: header comment in `app/src/lib/storage/config.ts`; `.env.example`.

### I-16 · Endpoint-pinning logic duplicated app-side vs. pipeline ⚪
The egress IP-pinning for a custom http (MinIO) endpoint exists twice: `S3Adapter._pinned_endpoint` (per-connection) and `storage/platform._pinned_endpoint_url` (platform bucket). Parallel, small, and independently tested; a shared helper is a possible future refactor, not a bug.
- Tracked in: here.

---

## Phase 4 — ingest pipeline (Slice A + B)

### I-17 · Association `collection_id` not verified against the built-in catalog 🟡
`POST /api/collections/[id]/connections` stores the `collection_id` from the path as-is; it does **not** yet confirm the collection exists in the built-in catalog (ROADMAP §1 "enforced in the API"). The UI only surfaces the Data-flow tab for the built-in catalog, so this isn't reachable through the client, but the API accepts any string. Hardening = a server-side existence check against the built-in catalog (or a FK once collections are registered in `stac_higher`).
- Tracked in: `app/src/pages/api/collections/[id]/connections/index.ts`.

### I-18 · Associating is gated at operator+, not member ⚪
ROADMAP §7 grants "associate connections ↔ collections" to **member**, but the mutation guard (`matchGatedRoute` → `canMutate`) is binary operator|admin, so association create/edit/delete requires operator+. Reads (list/detail) are open to any authenticated caller who can see the row. A per-route role floor (member for associate, operator for connection CRUD) is the eventual refinement.
- Tracked in: `app/src/lib/authz/permissions.ts`, `app/src/lib/associations/access.ts`.

### I-19 · Adapter `get` fully buffers large assets (streaming deferred) ⚪
The list-metadata half is **done (Slice B1)**: `StorageAdapter.list()` now returns `FileEntry` with size/mtime/etag, which the DISCOVER settled-check needs. The remaining gap: `get() -> bytes` buffers the whole object in memory, and copy-mode FETCH (Slice B2+B3) buffers `get → platform.put_object`, so FETCH of multi-GB assets is unsafe at envelope scale. Fine for local/small assets; true streaming (a streaming read + S3 multipart upload) is deferred and logged here.
- Tracked in: here; `services/pipeline/.../adapters/base.py`, `services/pipeline/.../ingest/fetch.py`.

### I-20 · Ingest discovery is non-recursive (one directory level) ⚪
DISCOVER lists `source_path` once. S3's prefix listing is naturally deep (all keys under the prefix), but SFTP/FTP `list()` returns a single directory level, so nested products under an SFTP/FTP source are not discovered. Adequate for the common flat-drop-directory case; a recursive walk (descend into `is_dir` entries, guarding depth/symlink loops) is the follow-up.
- Tracked in: here; `services/pipeline/.../ingest/discover.py`. Also underpins the `StorageAdapter.list()` path-convention divergence surfaced by DISCOVER (S3 full-key vs SFTP/FTP relative-name), which `relative_source_path`/`source_fetch_path` normalize (I-4).

### I-21 · Reference-mode ingest stalls at `settled` — RESOLVED by Slice C ✅
`storage_mode: reference` associations used to run DISCOVER (files reach `settled`) but stop there — GROUP formed no groups and FETCH skipped the copy, so nothing advanced to `stored`/`itemized`. **Slice C resolves this**: GROUP now forms groups for reference mode identically to copy mode; FETCH's reference branch records a stable, credential-free `source_href` (`S3Adapter.public_object_url`) in `ingest_files.source_href` and advances the ledger `settled` → `stored` without copying bytes; EXTRACT's byte-source seam (`MemberByteSource`/`CanonicalByteSource`/`SourceAdapterByteSource`) reads the source bytes directly for `build_item`; ITEMIZE is unchanged. The asset route resolves reference-mode items via `resolveAssetTarget` → `lookupReferenceHref`, 302-ing straight to `source_href` with no presigning and no decryption. Live SFTP/FTP + a continuous scheduler-driven run (Task 10) is the remaining verification, tracked separately.
- Tracked in: `services/pipeline/.../ingest/group.py`, `services/pipeline/.../ingest/fetch.py`, `services/pipeline/.../ingest/extract.py`; `app/src/lib/storage/resolve.ts`, `app/src/lib/storage/reference.ts`.

---

## Phase 4 — ingest pipeline (Slice B4: EXTRACT + ITEMIZE)

### I-22 · `file_mtime` is a ledger settle-time approximation, not a true source mtime 🟡
`metadata.defaults.datetime: file_mtime` resolves to the `ingest_files` ledger row's `updated_at` (the settle-check timestamp DISCOVER records), not the source file's actual modification time — the ledger has no durable mtime column, and etag-only source protocols (S3) expose no mtime at all to record one from. Adequate for the common case (files settle shortly after they land), but not exact for sources with meaningful clock skew between write and poll. A true source-mtime column would be a future **app-owned** migration (ADR 0001 keeps DDL ownership with the app).
- Tracked in: [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md); `services/pipeline/.../ingest/extract.py` (`resolve_datetime`).

### I-23 · pgstac/pypgstac version lockstep on upgrade 🟡
The pinned `pypgstac[psycopg]` client minor must track the pinned `ghcr.io/stac-utils/pgstac` image minor (both currently `0.9.11`) — pypgstac's upsert path calls pgstac's own SQL functions, and that surface can shift between minor versions. Any future pgstac image bump must bump the `pypgstac` pin in the same change and re-run the upsert path (unit + `test_integration_itemize.py`) before it ships.
- Tracked in: [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md); `services/pipeline/pyproject.toml`, `docker-compose.yml`.

### I-24 · Bundled-GDAL driver subset ⚪
rasterio's `>=1.5,<2` wheels bundle their own GDAL build with a smaller driver set than a full system GDAL install would carry. Sufficient for the ingest media types this platform targets (COG/GeoTIFF and common raster formats); an exotic format outside that subset fails EXTRACT (`ExtractError`) rather than silently degrading. Flag if a source product needs a driver the bundled GDAL omits.
- Tracked in: [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md); `services/pipeline/.../ingest/extract.py`.

### I-25 · Sidecar `generic_xml` parser covers a minimal MVP field set; sidecar file is not a separate asset ⚪
The `sidecar` metadata strategy's `generic_xml` parser looks for a small, namespace-agnostic set of date-ish tags (`datetime`/`acquired`/`date`/`acquisitiondate`/`start_datetime`) and no geometry — richer field mapping is a follow-up, not implemented here. Separately: when a raster and its sidecar share a basename (e.g. `scene.tif` + `scene.xml`), `build_assets`/`build_raster_auto` collapse them to a **single** `data` asset keyed by that stem — the raw sidecar file itself is never exposed as a distinct STAC asset, only the metadata parsed out of it lands in `item.properties`. Flag if a product needs the sidecar file itself downloadable as its own asset.
- Tracked in: [ADR 0006](decisions/0006-ingest-metadata-and-upsert.md); `services/pipeline/.../ingest/extract.py` (`_find_datetime_in_xml`, `build_assets`, `build_raster_auto`).

### I-26 · Memory-buffered raster reads in EXTRACT ⚪
EXTRACT reads a group's primary raster fully into memory (`rasterio.MemoryFile(raster_bytes)`) before handing it to rio-stac — consistent with FETCH's existing buffered `get`/`put_object` (I-19), but compounding the same envelope-scale risk one stage later: a multi-GB scene is fully buffered twice (FETCH, then EXTRACT) before an item exists. True streaming raster reads are deferred alongside I-19's streaming FETCH gap.
- Tracked in: here; I-19 (above); `services/pipeline/.../ingest/extract.py` (`build_item`, `build_raster_auto`).

### I-27 · pgstac requires a non-null geometry — `defaults_only` (and geometry-less `sidecar`) items cannot be catalogued ✅ resolved (Slice B4a)
**Found during the B4 live verification run (2026-07-17).** pgstac's `items` table enforces a **NOT NULL `geometry` column**, so an item without a geometry is rejected on upsert (`NotNullViolation` on `_items_*.geometry`) — even though the STAC spec and `stac-pydantic` both permit `geometry: null`.
- **`raster_auto` was unaffected** — rio-stac always derives a footprint.
- **`defaults_only` — and `sidecar` when no geometry is parsed — produced `geometry: null` items pgstac refused.**

**Resolved by Slice B4a** with a layered, best-effort-first resolution chain in `build_item` (`services/pipeline/.../ingest/extract.py`), applied whenever the chosen strategy leaves the item geometry null: (1) strategy geometry (`raster_auto`/`sidecar`, unchanged); (2) **best-effort GDAL open** of the primary member (`geometry_from_raster`, gated by `is_gdal_candidate` — covers COG/GeoTIFF/netCDF/GRIB/Zarr/etc., not just the `raster_auto` raster set) — recovers a footprint even under `defaults_only`/`sidecar` when the primary file happens to be georeferenced; (3) an **opt-in collection-extent fallback** (`metadata.defaults.geometry: "collection"`, cross-runtime Zod contract in `app/src/lib/associations/schemas.ts`) — `run_itemize` reads the collection's bbox via `PgstacWriter.get_collection_bbox` and passes a `collection_fallback` dict into `build_item`, degrading to a `global_fallback` world polygon when the collection has no real (non-global) extent; (4) **fail-fast** — `ExtractError` when none of the above yields a geometry, so a null-geometry item is never emitted (the group lands `failed`, not stuck at `stored`). Every item that ends with a geometry carries `properties["stac_higher:geometry_source"]` ∈ `raster`/`sidecar`/`collection_extent`/`global_fallback` for provenance.
- Tracked in: `services/pipeline/.../ingest/extract.py` (`GDAL_CANDIDATE_EXTS`, `is_gdal_candidate`, `geometry_from_raster`, `bbox_to_polygon`, `build_item`); `.../ingest/itemize.py` (`_build_collection_fallback`, `run_itemize`); `.../stac/pgstac_writer.py` (`PgstacWriter.get_collection_bbox` + `PgPgstacWriter` impl); `app/src/lib/associations/schemas.ts` (`metadataSchema.defaults.geometry`).

### I-28 · Minor robustness notes from the B4 whole-branch review ⚪
Non-blocking items the final review surfaced; fix opportunistically.
- **`CollectionMissing` is detected by substring** (`"is not present in the database"` in `PgPgstacWriter.upsert_items`). A pgstac/pypgstac wording change on a version bump (see I-23 lockstep) would make a genuine missing-collection error propagate as "transient" and retry forever instead of landing `failed`. Prefer matching on exception type / SQLSTATE when feasible.
- **Non-data stem-collision order differs** between `build_assets` (keeps last on a metadata/metadata stem tie) and `build_raster_auto` (keeps first). Inconsequential today (both are metadata; the data-asset-wins rule IS consistent), but worth unifying.
- **ITEMIZE is gated on `CREDENTIALS_MASTER_KEY`** even when `post_ingest=leave` (the adapter is only needed for delete/move). In practice the key is always present (FETCH required it to reach `stored`), so impact is low; the gate could be relaxed to only require the key when the action actually needs the adapter.
- Tracked in: `services/pipeline/.../stac/pgstac_writer.py`, `.../ingest/extract.py`, `.../jobs/ingest.py`.

### I-29 · Best-effort geometry provenance honesty (no-CRS raster → world bbox tagged as measured) 🟡
`rio_stac.create_stac_item` (used by `build_raster_auto`) falls back to a **world bbox `[-180,-90,180,90]` with a warning** when a raster has no CRS, rather than failing. Our code then tags that item `stac_higher:geometry_source = "raster"` (i.e. *measured*), so a degenerate whole-world footprint is indistinguishable from a real measured one — undermining the provenance honesty the geometry-source property exists for. Consider detecting the no-CRS / world-bbox case and either failing through to the collection/fail-fast layer or tagging it distinctly (e.g. `raster_no_crs`). Found in the B4/B4a `/simplify` efficiency review.
- Tracked in: `services/pipeline/src/pipeline/ingest/extract.py` (`build_raster_auto`).

### I-30 · `_primary()` member selection is raster-only, not GDAL-candidate-aware ⚪
`_primary(members)` picks the first member whose extension is in `RASTER_EXTS` (`.tif/.tiff/.jp2/.png/.jpg/.jpeg`), else `members[0]`. `.nc`/`.grib`/`.zarr` are NOT in `RASTER_EXTS` (they are in the broader `GDAL_CANDIDATE_EXTS`), so a group with a lone `.nc` alongside a `.json` sidecar may select the sidecar (or the `.nc`, depending on discovery order) as primary; the best-effort GDAL geometry step then tests `is_gdal_candidate` against the wrong member and can miss a usable grid, dropping to the collection/fail-fast layer. Fix: make `_primary()` tiered — prefer `is_raster`, then `is_gdal_candidate`, then `members[0]` — so "which member is the main data payload" is decided once. Behavior-affecting (changes asset roles / geometry source for mixed non-raster-extension groups), so it wants a real review, not a silent refactor. Found in the B4/B4a `/simplify` altitude review.
- Tracked in: `services/pipeline/src/pipeline/ingest/extract.py` (`_primary`, `_best_effort_raster_geometry`).

### I-31 · Eager collection-extent DB read on the opted-in geometry path ⚪
When an association opts into `metadata.defaults.geometry: "collection"`, `run_itemize` reads the collection's extent (`PgstacWriter.get_collection_bbox`, a fresh psycopg connection + query) *before* `build_item`, even when the extraction strategy is about to supply a geometry itself (always the case for `raster_auto`, which never returns null geometry). One wasted DB round-trip per item at envelope scale for that config combo. Fix: make the read lazy — fetch the collection bbox only inside the fallback path, when `item["geometry"]` is still null after strategy + best-effort GDAL (thread an async loader into `build_item`). Deferred as invasive (signature change + test churn) for a marginal, unusual-config benefit. Found in the B4/B4a `/simplify` efficiency review.
- Tracked in: `services/pipeline/src/pipeline/ingest/itemize.py` (`run_itemize`, `_build_collection_fallback`).

---

## Phase 4 — ingest pipeline (Slice C: `storage_mode: reference`)

Reference mode ships as **durably-reachable sources only**: the pipeline persists a stable, credential-free source URL (`ingest_files.source_href`) and the app 302s to it with no presigning and no decryption — preserving the `crypto.ts` "app never decrypts" invariant. See I-21 (resolved) for what changed in GROUP/FETCH/EXTRACT/ITEMIZE.

### I-32 · Reference mode has no path for private sources 🟡
Reference mode only works when the source object is reachable **without** credentials at a stable URL (`S3Adapter.public_object_url`). A source that requires credentials to read (a private bucket, SFTP/FTP) has no reference path today — such sources must use `copy` mode instead. The deferred fix is a pipeline resolver endpoint the app calls per-read to mint a fresh presigned URL server-side (the pipeline holds the decrypted connection credentials; the app never would, keeping the decryption boundary intact).
- Tracked in: here; `app/src/lib/storage/reference.ts`; `services/pipeline/.../connections/adapters/s3.py`.

### I-33 · Reference-mode assets have no checksum recorded 🟡
Copy-mode FETCH records a sha256 checksum of the copied bytes; reference-mode FETCH does not — there is nothing to hash without copying, and hashing at the source would defeat the point of not copying. Versioning still works (the DISCOVER fingerprint, not the checksum, drives re-ingest), but reference-mode ledger rows carry no independent integrity signal for the asset the item points at.
- Tracked in: here; `services/pipeline/.../ingest/fetch.py`.

### I-34 · Reference source URL uses the connection's configured endpoint (same class as I-15) 🟡
`S3Adapter.public_object_url` builds the source href from the connection's configured S3 endpoint. If that endpoint isn't reachable from wherever the asset-route redirect is followed (e.g. an internal-only endpoint distinct from a browser-reachable one), the 302 target won't resolve — the same internal-vs-browser-reachable split I-15 already tracks for the platform bucket's presign endpoint, but here for source connections.
- Tracked in: here; I-15; `services/pipeline/.../connections/adapters/s3.py`.

### I-35 · Pipeline image was missing `libexpat1` — in-container `raster_auto` EXTRACT failed ✅ resolved (Slice C live verification)
The runtime stage of `services/pipeline/Dockerfile` installed only `libpq5`. rasterio's bundled-GDAL wheels dynamically link `libexpat` at runtime, so `import rasterio` inside the deployed container raised `ImportError: libexpat.so.1: cannot open shared object file` and `raster_auto` EXTRACT could not run in-container — breaking the Phase 4 done-when (dropped file → catalogued item) for any GeoTIFF-bearing ingest. B4's `raster_auto` verification ran host-side (uv venv), which masked the gap; the first **in-container** scheduler-driven itemize (Slice C live verification) surfaced it. Fix: add `libexpat1` to the runtime apt install (one line). Verified by a fresh image rebuild importing `rasterio`/`rio_stac` cleanly and a full scheduler-driven reference itemize producing a queryable `ST_Polygon` item.
- Tracked in: `services/pipeline/Dockerfile`.

---

## Phase 5 — delivery pipeline (Slice A)

### I-36 · `item_events` / `delivery_log` partitioning deferred to Phase 6 ⚪
`stac_higher.item_events` (migration 007) is a plain, unpartitioned table, as
`delivery_log` will be when Slice B adds it. Both are envelope-scale high-volume
tables; Phase 6 time-partitions them on `occurred_at` and adds partition-drop
retention jobs (mirrors the audit_log / ingest_files deferrals, I-11). Kept plain
so the outbox + dispatcher could land first.
- Tracked in: migration 007 comment; [ADR 0007](decisions/0007-outbox-trigger-ownership.md).

### I-37 · `on_update` must derive redelivery from `delivery_log`, not the outbox `op` ⚪
Live-verified in Slice A: pgstac implements an item update as **delete + insert**,
so an update surfaces as a `delete` then an `insert` outbox row (never `op='update'`
via pgstac's normal paths). Benign for the skeleton (the delete drains, the insert
redelivers), but Slice B's `on_update: redeliver|ignore` logic must decide
first-delivery-vs-redelivery from a prior `delivery_log` row, **never** from the
outbox `op`.
- Tracked in: [ADR 0007](decisions/0007-outbox-trigger-ownership.md) "Update semantics".

### I-38 · Dispatcher item-visibility race is best-effort skip 🟡
`dispatch_once` fetches the item via `pgstac.get_item`; if the outbox row is
claimed before the item is visible (a race under concurrent writes), the event is
logged and drained without dispatching — no retry. Acceptable for the poll-driven
skeleton (a later update event re-drives it); Slice C's `LISTEN`-woken loop should
revisit whether such events need a bounded retry rather than a silent skip.
- Tracked in: `services/pipeline/.../dispatcher/loop.py` (the `item is None` branch).

### I-39 · `dispatch_once` has no per-event error isolation ⚪
The dispatch loop has no `try/except` around a single event's `get_item`/`match_item`;
an exception on one event aborts the batch before `mark_processed`, so the whole
claimed batch fails to drain and re-runs next tick (busy-loop on the offending
item, no backoff). Low risk in Slice A — the matcher is already hardened against
CQL2 filter errors (the one realistic raise) — but Slice B/C should add per-event
isolation (skip + dead-letter the poison event) when the loop starts moving bytes.
There is a concrete API-reachable trigger for this (whole-branch review): the
update route `[assocId].ts` validates PUT `config` with the **ingest-only**
`associationUpdateSchema` (no direction check), so an operator can overwrite a
`direction='deliver'` row with an ingest-shaped config; `match_item` then calls
`parse_delivery_config` **outside** the per-association try/except (`matcher.py`,
also Minor below), which raises `DeliveryConfigError` and — with no per-event
isolation — permanently stalls the outbox. Slice B fix: make the update schema
direction-aware **and** wrap the per-association body (including
`parse_delivery_config`) in the isolation guard, not just the CQL2 eval.
- Tracked in: `services/pipeline/.../dispatcher/loop.py`, `.../delivery/matcher.py`,
  `app/src/lib/associations/schemas.ts` (update schema); found in the Slice A Task 6
  review + whole-branch review.

### I-40 · Dispatcher HA / single-instance assumption ⚪
The poll-driven dispatch (and Slice C's future `LISTEN`-woken loop) assumes a
single pipeline instance. **The current claim/mark split is NOT
concurrency-safe** (whole-branch review): `claim_pending_events` and
`mark_processed` run in **separate** transactions, so the `FOR UPDATE SKIP LOCKED`
lock is released the moment the claim SELECT's transaction ends — processing then
holds no lock and the rows are still `processed_at IS NULL`. Two overlapping
dispatch runs (e.g. a `dispatch_once` outrunning the 60s poll interval) would
re-claim the same rows and, once Slice B moves bytes, double-dispatch. The crash
direction IS safe (a crash between claim and mark leaves rows pending →
redelivered, never lost). Before Slice B moves bytes it must unify
claim→process→mark under one transaction (or add an atomic `claimed_at`/status
claim-marking column); leader election / partitioned ownership is a Phase 8
concern (§10 scheduler-HA). Slice C documents the single-instance assumption where
the `LISTEN` loop lands.
- Tracked in: `services/pipeline/.../dispatcher/repo.py` (split claim/mark),
  ROADMAP §10 (scheduler/monitor HA); found in the Slice A whole-branch review.

### I-41 · `item_filter` is not CQL2-validated on write 🟡
`deliveryConfigSchema` validates `item_filter` only as a non-empty string —
there is no CQL2 grammar check on the app write path (a CQL2 parser exists only
in the Python `cql2` package, not in TS/Zod). A malformed filter is therefore
accepted with a 201, and at dispatch time `_item_filter_passes` catches the
`cql2` exception and returns `False`, so once Slice B moves bytes the association
silently matches nothing — an enabled delivery that never delivers, with only a
pipeline-side warning the operator cannot see. No live impact in Slice A (the
skeleton only logs). Slice B fix: validate the filter on write (a CQL2 parser
app-side, or a pipeline-side validation bounce) and/or surface an
association-`error` state through monitoring (Phase 6) so a bad filter is visible.
- Tracked in: `app/src/lib/associations/schemas.ts` (`item_filter`),
  `services/pipeline/.../delivery/matcher.py` (`_item_filter_passes`); found in the
  Slice A `/code-review`.

### I-42 · Matcher requires ≥1 asset, so metadata-only delivery is skipped ⚪
`match_item` skips an association when the item↔`asset_keys` intersection is empty
(`if not keys: continue`), so an item with zero assets — or an association whose
`asset_keys` don't intersect the item — never matches, even when `payload`
requests metadata-only delivery (`item_json` / `completion_marker`). This matches
the ROADMAP §6.4 "delivery is assets only" headline, but whether a metadata-only
payload should deliver without assets is a real design decision deferred to Slice
B (when payload writing is implemented). Revisit the asset-gate then.
- Tracked in: `services/pipeline/.../delivery/matcher.py`; found in the Slice A
  `/code-review`.

### I-43 · Delivery is at-least-once, not exactly-once — redelivery is idempotent but not deduped 🟡
The dispatcher claims outbox rows (`FOR UPDATE SKIP LOCKED`) in one connection and
`mark_processed`es them in another, so the row lock releases before the delivery
job is enqueued/run (carried from Slice A, see I-40). Now that Slice B-i moves
bytes, a crash between enqueue and drain — or a second dispatcher instance — can
re-dispatch the same `(association, item)`. This is **idempotent-harmless in B-i**:
`delivery_log` `UNIQUE(association_id, item_id)` + `upsert_pending` collapse to one
row, and delivery is overwrite-always (S3 direct atomic PUT / SFTP-FTP
`.part`→rename) of byte-identical canonical data, so a redispatch re-PUTs the same
bytes to the same key and touches the same row — no duplicate object, no partial
file, no divergent state. Worst case is `attempts` over-counting and a brief
status flap under truly concurrent redelivery. Genuine dedup/leader-election is
deferred to Slice C / B-iii; see also I-40.
- Tracked in: `services/pipeline/.../dispatcher/{loop,repo}.py`,
  `.../delivery/repo.py`; found in the Slice B-i whole-branch review.

### I-44 · `delivery_log.attempts` is a lifetime counter, not reset on redelivery ⚪
`upsert_pending`'s `ON CONFLICT DO UPDATE` resets `status='pending'` but leaves
`attempts` untouched, so a legitimately-redelivered item's `attempts` climbs
across independent events (each `mark_delivering` increments). Harmless in B-i
(`attempts` is observability only), but when B-iii adds `max_attempts`
dead-lettering, a frequently-redelivered row could be dead-lettered without a real
retry sequence. The redelivery reset point is exactly `upsert_pending` — reset
`attempts=0` there (or split a per-cycle counter) when B-iii lands.
- Tracked in: `services/pipeline/.../delivery/repo.py` (`upsert_pending`); found in
  the Slice B-i whole-branch review.

### I-45 · Concrete adapter `move()` bodies are inspection-only; SFTP/FTP delivery not live-verified 🟡
Slice B-i added `move()` to every adapter (S3 copy+delete, SFTP `posix_rename`,
FTP `rename`) and unit-tested only the base `put_atomic` (`.part`→move) and the
S3 `put_atomic` override (direct PUT) — the three concrete `move` bodies have no
dedicated unit test, and the B-i live verification exercised the **S3/MinIO**
destination only (S3 uses the direct-PUT override, so its `move` is not even on
the delivery path). SFTP/FTP destinations (which reach `move` via the base
`put_atomic`) are unit-covered by inspection but not run live. B-ii/B-iii should
add a dedicated `move` test (or a live SFTP/FTP destination run) before relying on
the `.part`→rename path in production.
- Tracked in: `services/pipeline/.../connections/adapters/{s3,sftp,ftp}.py`
  (`move`); found in the Slice B-i task/whole-branch reviews.

### I-46 · Outbox op for an item change depends on the write path (pypgstac upsert → `update`, transaction API → delete+insert) ⚪
The B-i live verification found that a changed item written via **pypgstac
`Loader.load_items(Methods.upsert)`** (the ingest ITEMIZE path) fires a single
`update` outbox row, an **identical** re-upsert is a no-op (no event), a new item
is `insert`, and a delete is `delete`. This differs from the ADR 0007 Slice-A
finding that "an update surfaces as delete+insert" — that came from the
**stac-fastapi transaction API** write path (`update_item` = delete+insert). Both
are benign for delivery: `dispatch_once` treats `insert`/`update` identically
(only `delete` is special-cased and never propagates). It matters only for B-ii's
`on_update`, which must key first-delivery-vs-redelivery off `delivery_log`, never
the outbox `op` (already tracked as I-37).
- Tracked in: `app/src/lib/db/migrate.ts` (trigger),
  `services/pipeline/.../dispatcher/loop.py`; found in the Slice B-i live verification.

