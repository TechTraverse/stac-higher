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

### I-38 · Dispatcher item-visibility race is best-effort skip 🟢
**Resolved in Slice C.** An event whose item is not yet visible is no longer
silently drained: `dispatch_once` releases the claim with a cool-off
(`item_events.dispatch_attempts` + `next_dispatch_at`, migration 012) and a
later wake (NOTIFY or the poll fallback) retries it, up to
`MAX_VISIBILITY_ATTEMPTS`; only then does it drain, with a loud log. The
cool-off keeps deferred events out of the drain-until-empty loop so one wake
cannot burn the retry budget.
- Resolved by: `ai/slice-c` (`dispatcher/loop.py`, `dispatcher/repo.py`,
  migration `012_dispatch_retry_and_backfills`).

### I-39 · `dispatch_once` has no per-event error isolation 🟢
**Resolved across the pre-B-iii wave + Slice B-iii.** The API-reachable
trigger is closed (`parseAssociationUpdate` validates PUT `config` against the
existing row's direction; `match_item` wraps the whole per-association body in
its isolation guard), and `dispatch_once` now wraps each event's
`get_item`/`match_item` in a per-event guard: a poison event is logged loudly
and drained with the batch (dead-lettered into the logs) instead of
busy-looping the claim.
- Resolved by: `ai/i39-pair` + Slice B-iii (`dispatcher/loop.py`).

### I-40 · Dispatcher HA / single-instance assumption 🟡
**Claim half resolved (Slice B-iii):** `claim_pending_events` now stamps
`item_events.claimed_at` (migration 011) in the same `FOR UPDATE SKIP LOCKED`
statement that selects the batch, so overlapping dispatch runs cannot
double-claim; a crash leaves claimed-but-unprocessed rows that are reclaimed
after a 10-minute stale window (`STALE_CLAIM_SECONDS`) — the crash direction
stays redeliver-never-lose. Accepted trade-off: an enqueue failure now
redrives after the stale window rather than the next tick (rare — queue
down). Remaining: leader election / partitioned ownership for genuine
multi-instance operation is a Phase 8 / M3 concern (§10 scheduler-HA); the
single-instance assumption is documented where the `LISTEN` loop landed
(`dispatcher/listener.py` module docstring) — overlapping wakes are safe
(atomic claim), so multi-instance is a throughput topic, not correctness.
- Tracked in: `services/pipeline/.../dispatcher/repo.py`, ROADMAP §10;
  found in the Slice A whole-branch review.

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
deferred to Slice C / B-iii; see also I-40. `delivery_log.delivered_assets`
fingerprints make redundant redelivery cheap but do not dedup concurrent
dispatch.
- Tracked in: `services/pipeline/.../dispatcher/{loop,repo}.py`,
  `.../delivery/repo.py`; found in the Slice B-i whole-branch review.

### I-44 · `delivery_log.attempts` is a lifetime counter, not reset on redelivery ✅ resolved (Slice B-ii)
`upsert_pending`'s `ON CONFLICT DO UPDATE` used to reset `status='pending'` but
leave `attempts` untouched, so a legitimately-redelivered item's `attempts`
climbed across independent events (each `mark_delivering` increments).
Harmless in B-i (`attempts` was observability only), but B-iii's planned
`max_attempts` dead-lettering would have dead-lettered a frequently-redelivered
row without a real retry sequence. **Resolved by Slice B-ii**: `upsert_pending`
now resets `attempts = 0` on the redelivery conflict branch, so `attempts`
counts a single delivery cycle, not the item's lifetime.
- Tracked in: `services/pipeline/.../delivery/repo.py` (`upsert_pending`); found in
  the Slice B-i whole-branch review, resolved in Slice B-ii.

### I-45 · Concrete adapter `move()` bodies are inspection-only; SFTP/FTP delivery not live-verified 🟢
**Resolved (Slice B-iii, 2026-07-25):** live SFTP and FTP destination
deliveries ran against the `compose.test-servers.yml` servers through the
production `put_atomic` → `move` path (`.part` → `posix_rename`/`rename`),
byte-identical payloads verified on both servers; dedicated unit tests for the
concrete `move` bodies and the put paths now exist
(`tests/test_adapter_put_dirs.py`). The live run surfaced (and B-iii fixed)
that neither adapter created missing parent directories — delivery path
templates are directory-shaped, so the first delivery into a fresh destination
failed without it. Second finding: the delfer FTP test server does not chroot;
FTP connections against it need `root_path=/ftp/demo` (compose comment
corrected).
- Resolved by: Slice B-iii live verification + `ai/b-iii-dirs`.

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

### I-47 · Copy-path etag fingerprints are endpoint-generation-specific ⚪
`delivery_log.delivered_assets` (migration 009) stores an `etag:<etag>/<size>`
fingerprint for server-side-copied assets and a `sha256:<hex>` fingerprint for
streamed ones — the two kinds intentionally compare unequal. Switching an
association between streamed (sha256 checksums) and copy (no checksums, or
md5) transfer, or a destination-bucket re-upload that changes the object's
etag generation (e.g. a copy that changes storage class or a bucket
migration), makes the recorded fingerprint compare unequal to the next
delivery's, costing one redundant redeliver. Benign by design — delivery is
at-least-once (I-43) and the redeliver produces byte-identical data — but
worth surfacing to an operator rather than silently re-transferring. Noted for
Phase 6 observability.
- Tracked in: `services/pipeline/.../delivery/transfer.py` (`can_server_side_copy`,
  `etag_fingerprint`, `sha256_fingerprint`); found in the Slice B-ii review.

### I-48 · md5 checksum sidecars ride the canonical ETag, which is NOT the content MD5 under SSE-KMS/SSE-C 🟡
The B-ii server-side-copy path writes the `.md5` sidecar from a single-part
canonical ETag (`worker.py`, `"-" in etag` is the only guard). On an
SSE-KMS/SSE-C-encrypted canonical bucket, single-part ETags are not the MD5, so
the sidecar would fail a consumer's `md5sum -c`. Spec-level assumption (the
B-ii design doc licenses it); safe on local MinIO and SSE-S3. **B-iii chose to
document the constraint** (here and at the copy gate in `worker.py`): md5
checksum sidecars require a canonical bucket whose single-part ETags are
content MD5s (unencrypted or SSE-S3). Deployments using SSE-KMS/SSE-C must use
`sha256` checksums (which force streaming) — a force-streaming flag is
deferred until such a deployment exists.
- Tracked in: `services/pipeline/src/pipeline/delivery/worker.py`; found in the
  Slice B-ii whole-branch review.

### I-49 · Reference-mode delivery residuals from the B-ii whole-branch review 🟢
**Resolved (Slice B-iii):** (1) a reference basename collision now logs a
warning and deterministically keeps the first source; (2) `mark_failed`
persists the partial `delivered_assets` map, so a retry skips assets the
failed cycle already wrote (unit-proven: only the missing asset re-transfers);
(3) the completion manifest is pruned to the item's current assets before
writing; (4) the missing tests exist (`test_delivery_retry.py`: source-read
failure → failed row, mixed reference+canonical item, md5 + copy-failure
fallback combo).
- Resolved by: Slice B-iii (`delivery/{worker,repo}.py`,
  `tests/test_delivery_retry.py`).

---

## Cross-phase — found by the 2026-07-22 architecture review

### I-50 · UI catalog writes have no token path under auth enforcement 🟢
**Resolved — the ADR 0008 BFF is implemented.** Built-in-catalog browser
writes now route through `/api/catalog/[...path]` (transaction endpoints
only, writes only): the route injects the caller's session access token
server-side (the token never reaches page JavaScript; the proxy stays the
enforcement point), `stacFetch` routes built-in-catalog mutations there
unconditionally (dev pass-through included, so the seam can't silently
regress), and the guard gates the paths (operator+) with one `audit_log` row
per mutation — the catalog plane is audited. The enforcement suite gained the
UI-path leg (`tests/integration/bff-catalog-writes.test.mjs`): a real
authorization-code session login → BFF write with only the httpOnly cookie →
201 through the enforced proxy → audit row, replacing the password-grant
client for the browser case.
- Resolved by: `ai/i50-bff` ([ADR 0008](decisions/0008-bff-catalog-writes.md);
  `app/src/pages/api/catalog/[...path].ts`, `app/src/lib/stac-api/client.ts`,
  `app/src/lib/authz/permissions.ts`).
### I-51 · ADR 0009 deletion semantics — soft-delete half DONE, GC half Phase 6 🟡
**Soft-delete half implemented (pre-B-iii hardening wave).** Migration 010:
`deleted_at` on connections/associations, history FKs CASCADE → RESTRICT
(`connection_checks`, `collection_connections`, `ingest_files`,
`delivery_log`), the association uniqueness now a partial index on live rows,
and `ingest_files.reference_removed_at`. Connection DELETE soft-deletes +
scrubs credentials/host-key, soft-deletes its associations, and **removes its
reference-backed items from pgstac** (ledger rows stamped
`reference_removed_at` so the asset route stops resolving them — the rows
survive as provenance). Association DELETE soft-deletes; history retained.
Both DELETE routes return the counted impact; pre-flight
`GET .../impact` endpoints feed the warn-and-proceed dialogs
(`app/src/lib/connections/deletion.ts`, `associations/storage.ts`). Pipeline
scheduler, dispatcher matcher, delivery reference-source loader, health sweep,
and check-drain queries all filter not-deleted.
**Remaining (Phase 6, GC-dependent):** collection-delete data impact via the
§6.5 marked-then-collected GC path (until then collection deletion still
orphans canonical bytes) and the `archived` collection state.
- Tracked in: [ADR 0009](decisions/0009-deletion-semantics.md);
  `app/src/lib/db/migrate.ts` (migration 010).
- Blocks (remaining half): honest collection-delete warnings.

### I-52 · Ingest has no crash recovery: stuck-`fetching` rows are unrecoverable, `failed` is terminal 🟢
If the pipeline dies mid-FETCH (most plausibly an OOM from the buffered
multi-GB `get`, I-19/I-26), the ledger row is stranded at `fetching` forever:
DISCOVER explicitly skips `fetching` rows even on fingerprint change, GROUP
only forms groups from `settled` rows, and nothing sweeps stalled Procrastinate
`doing` jobs — the file silently never becomes an item, with no alarm. A
`failed` row (transient network error) was likewise terminal.
**Resolved (Slice B-iii):** the periodic `pipeline.ingest_recovery_sweep`
(1) resets `fetching` rows stalled past `INGEST_FETCH_STALL_SECONDS`
(default 30 min) back to `settled` — safe, FETCH is idempotent against
canonical storage; and (2) re-settles `failed` rows after an
`INGEST_FAILED_RETRY_SECONDS` cool-off (default 5 min), bounded by
`ingest_files.retries < INGEST_MAX_RETRIES` (default 3, migration 011) — rows
at the cap stay `failed` (terminal until the ROADMAP §8 operator backfill).
Both transitions re-enter at `settled`, so the normal GROUP → FETCH chain
re-drives them on the next poll tick. Hard job crashes need no separate
Procrastinate sweep: the ledger's idempotent stages plus these sweeps re-drive
the work regardless of the stranded queue row.
- Resolved by: Slice B-iii (`ingest/repo.py` sweeps, `jobs/ingest.py`,
  migration 011).

### I-53 · Cross-runtime config contracts have no drift test (golden fixtures missing) 🟢
**Resolved (pre-B-iii hardening wave).** Golden JSON fixtures for both §5.1
config directions live in `tests/contract-fixtures/` (valid + invalid
documents with per-side accept/reject expectations — the Zod write gate is
strict, the Python readers are lenient by design; the README there documents
the semantics). Both suites consume them:
`app/src/__tests__/contract-fixtures.test.ts` asserts Zod's accept/reject per
case and that the minimal document parses to the golden defaults document;
`services/pipeline/tests/test_contract_fixtures.py` runs the same cases
through `parse_ingest_config`/`parse_delivery_config` and asserts every
re-applied default against the same golden values. The standing rule ("new
cross-runtime shape ⇒ new shared fixture") is in AGENTS.md. Building the
fixtures surfaced one real drift, fixed with them: whitespace-only
`source_path`/`path_template` passed Zod's `min(1)` but the Python parsers
`.strip()`-reject it — the Zod schemas now reject non-blank-violating values
too. The direction-aware update schema (the app half of I-39) landed in the
prior iteration.
- Resolved by: the pre-B-iii hardening wave (`ai/i53-fixtures`), 2026-07-25.
