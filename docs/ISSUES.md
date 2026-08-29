# Outstanding issues

Known gaps, residual risk, and deferrals — tracked honestly so they aren't mistaken for "done." Status: 🔴 open · 🟡 accepted/mitigated · 🟢 resolved · ⚪ deferred-by-design.

Each entry: what it is, why it exists, and where it's tracked. Close an entry by moving it to 🟢 with the resolving commit/PR; fully-closed entries move to [`ISSUES-ARCHIVE.md`](ISSUES-ARCHIVE.md), leaving a one-line stub in the list at the bottom so inbound references still land. Entries that keep an open or amber half stay here.

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

### I-8 · Full-project `npx astro check` from the repo root is meaningless 🟡
Under Astro 6 a root-level `astro check` OOM'd (Vite/rolldown plugin type
conflict). Under Astro 7 (2026-08-29) it no longer OOMs but is still wrong to
run: it finds no `src/pages` at the root and reports only type-clash noise
from `app/node_modules/astro/components/*` (duplicate astro installs across
node_modules trees). The app-scoped check (`npm run check` from `app/`) is
the real gate — `npm run verify` runs it first (matching CI), and the scoped
PostToolUse `astro check` hook covers edits. Never run the check from the
repo root.
- Tracked in: `AGENTS.md` "Gotchas".

---

## Deferred by design (later phases)

### I-9 · KMS credential provider ⚪
Credentials use a local `CREDENTIALS_MASTER_KEY` behind an `EncryptionProvider` seam. A KMS-backed provider arrives in Phase 8. — `app/src/lib/connections/crypto.ts`.

### I-10 · `stac-api` connection protocol ⚪
Reserved in the enum; create/update reject it and the adapter factory raises `NotImplementedError("reserved for a future release")`.

### I-11 · Audit-log partitioning & retention — partitioning DONE (M2-G), drop-retention open 🟡
Migration 018 time-partitions `audit_log` monthly (attach-don't-copy; append-only row triggers live on the partitioned parent, and retention drops whole partitions via DETACH+DROP — the escape hatch the migration-003 comment demanded, [ADR 0012](decisions/0012-table-hygiene.md)). REMAINING (amber): no automated partition-drop retention job yet — compliance windows are an operator decision; dropping a detached monthly partition is a one-line manual op until a policy exists.

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
- Tracked in: here; `services/pipeline/.../ingest/discover.py`. Also underpins the `StorageAdapter.list()` path-convention divergence surfaced by DISCOVER (S3 full-key vs SFTP/FTP relative-name), which `relative_source_path`/`source_fetch_path` normalize (I-4, archived).

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

Reference mode ships as **durably-reachable sources only**: the pipeline persists a stable, credential-free source URL (`ingest_files.source_href`) and the app 302s to it with no presigning and no decryption — preserving the `crypto.ts` "app never decrypts" invariant. See I-21 (resolved, archived) for what changed in GROUP/FETCH/EXTRACT/ITEMIZE.

### I-32 · Reference mode has no path for private sources 🟡
Reference mode only works when the source object is reachable **without** credentials at a stable URL (`S3Adapter.public_object_url`). A source that requires credentials to read (a private bucket, SFTP/FTP) has no reference path today — such sources must use `copy` mode instead. The deferred fix is a pipeline resolver endpoint the app calls per-read to mint a fresh presigned URL server-side (the pipeline holds the decrypted connection credentials; the app never would, keeping the decryption boundary intact).
- Tracked in: here; `app/src/lib/storage/reference.ts`; `services/pipeline/.../connections/adapters/s3.py`.

### I-33 · Reference-mode assets have no checksum recorded 🟡
Copy-mode FETCH records a sha256 checksum of the copied bytes; reference-mode FETCH does not — there is nothing to hash without copying, and hashing at the source would defeat the point of not copying. Versioning still works (the DISCOVER fingerprint, not the checksum, drives re-ingest), but reference-mode ledger rows carry no independent integrity signal for the asset the item points at.
- Tracked in: here; `services/pipeline/.../ingest/fetch.py`.

### I-34 · Reference source URL uses the connection's configured endpoint (same class as I-15) 🟡
`S3Adapter.public_object_url` builds the source href from the connection's configured S3 endpoint. If that endpoint isn't reachable from wherever the asset-route redirect is followed (e.g. an internal-only endpoint distinct from a browser-reachable one), the 302 target won't resolve — the same internal-vs-browser-reachable split I-15 already tracks for the platform bucket's presign endpoint, but here for source connections.
- Tracked in: here; I-15; `services/pipeline/.../connections/adapters/s3.py`.

---

## Phase 5 — delivery pipeline (Slices A–B)

### I-36 · `item_events` / `delivery_log` partitioning — amended by M2-G 🟢 (as amended)
The blanket partitioning promise was wrong for `delivery_log`: its
`UNIQUE (association_id, item_id)` is the redelivery idempotency key, and a
time-partitioned unique index would have to include the partition key —
breaking the upsert model. As amended ([ADR 0012](decisions/0012-table-hygiene.md)):
`item_events` IS partitioned (migration 018, attach-don't-copy, monthly
partitions reconciled two months ahead on every runMigrations());
`delivery_log` and `ingest_files` get conservative retention SWEEPS instead
(`pipeline.history_retention`: soft-deleted-association rows past
`HISTORY_RETENTION_DAYS`, plus itemless terminal deliveries). Revisit a
current-state/history split at M3 if sweeps prove insufficient under load.
- Tracked in: migration 018; [ADR 0012](decisions/0012-table-hygiene.md).

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
`cql2` exception and returns `False`, so the association silently matches
nothing — an enabled delivery that never delivers, with only a pipeline-side
warning the operator cannot see. **Phase 6 (M2-B) shipped its monitoring
sources (`flow`/`health`/`job_failure`) without the association-`error` /
CQL2-validity source this entry hoped for**, so the direct signal still does
not exist. Partial mitigation as-shipped: an association with a
`deliver_within_seconds` expectation set will eventually raise a
`delivery_slo`/inactivity alert when nothing delivers — indirect, and only
when an expectation is configured. Real fix options unchanged: validate the
filter on write (a CQL2 parser app-side, or a pipeline-side validation
bounce) and/or add a config-validity alert source.
- Tracked in: `app/src/lib/associations/schemas.ts` (`item_filter`),
  `services/pipeline/.../delivery/matcher.py` (`_item_filter_passes`); found in the
  Slice A `/code-review`.

### I-42 · Matcher requires ≥1 asset, so metadata-only delivery is skipped ⚪
`match_item` skips an association when the item↔`asset_keys` intersection is empty
(`if not keys: continue`), so an item with zero assets — or an association whose
`asset_keys` don't intersect the item — never matches, even when `payload`
requests metadata-only delivery (`item_json` / `completion_marker`). This matches
the ROADMAP §6.4 "delivery is assets only" headline. **The revisit this entry
deferred to Slice B (payload writing) did not happen — B-ii shipped payload
writing and kept the asset-gate as-is**, so metadata-only delivery remains
unsupported by (now-implicit) design. If a subscriber ever needs item-JSON
without assets, decide deliberately then: relax the gate for
metadata-only payload configs, or document the gate as intended behavior in
the §5.1 config contract.
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

### I-46 · Outbox op for an item change depends on the write path (pypgstac upsert → `update`, transaction API → delete+insert) ⚪
The B-i live verification found that a changed item written via **pypgstac
`Loader.load_items(Methods.upsert)`** (the ingest ITEMIZE path) fires a single
`update` outbox row, an **identical** re-upsert is a no-op (no event), a new item
is `insert`, and a delete is `delete`. This differs from the ADR 0007 Slice-A
finding that "an update surfaces as delete+insert" — that came from the
**stac-fastapi transaction API** write path (`update_item` = delete+insert). Both
are benign for delivery: `dispatch_once` treats `insert`/`update` identically
(only `delete` is special-cased and never propagates). It matters only for
`on_update`, which keys first-delivery-vs-redelivery off `delivery_log`, never
the outbox `op` (I-37, resolved).
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
at-least-once (I-43) and the redeliver produces byte-identical data. **Phase 6
(M2) shipped without a redundant-transfer signal for this** — delivery
counters (M2-H) count terminal outcomes, not fingerprint-miss redelivers — so
the "surface it to an operator" ask remains open; noise-level so far is nil.
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

---

## CI/CD — GitHub Actions (2026-08-18)

### I-57 · Astro 6.x high-severity advisories fixed only in Astro 7 🟢 (resolved 2026-08-29)
**Resolved by the Astro 6 → 7 upgrade** (astro 7.2.9, @astrojs/node 11.1.4,
@astrojs/react 6.0.4): the 6.x XSS advisories (GHSA-f48w-9m4c-m7f5,
GHSA-7pw4-f3q4-r2p2, GHSA-4g3v-8h47-v7g6), `sharp <0.35.0`, and
`@astrojs/node <=11.0.1` are all cleared — `npm audit --omit dev` now reports
a single low (esbuild dev-server, dev-only class). The `security.yml`
npm-audit gate was tightened from critical to **high** (prod deps).
Migration notes: the codebase needed no source changes (verify + full e2e
green first run); the one behavioral catch was Astro 7 auto-daemonizing
`astro dev` in AI-agent environments, fixed for e2e by `ASTRO_DEV_BACKGROUND`
in the Playwright webServer env. I-8 amended (root check no longer OOMs but
stays app-scoped-only).
- Tracked in: `.github/workflows/security.yml` (npm-deps job).

---

## M2 — operable platform (Phase 6)

### I-58 · Notification semantics: in_app rows are declarative-only; no recovered/resolved events; webhooks are at-least-once 🟡
Three accepted M2-C simplifications (ADR 0010):

- An `in_app` channel row has no runtime behavior — the alerts row plus the
  per-user `alert_reads` watermark serve every member of the group whether or
  not an `in_app` row exists. The row exists so a group's channel list states
  intent (and so email can slot in later); if in-app opt-OUT ever matters, the
  bell/unread queries must start honoring the row.
- Webhooks fire only on `alert.firing` (a NEW alert row). Auto-resolve,
  manual resolve, and recovery send nothing — an operator watching only a
  webhook target never learns the incident ended. The payload's `event` field
  leaves room for `alert.resolved` later.
- Webhook delivery is at-least-once (stall revival / duplicate enqueue can
  POST twice); consumers must dedupe on `alert.id`. The signing secret is
  stored plaintext in `notification_channels.config` (unlike connection
  credentials' AES-GCM envelope) — acceptable for a shared HMAC secret, but
  an envelope upgrade is mechanical if posture changes.
- `PgNotifyRepo` SQL is `# pragma: no cover` per repo convention — unverified
  against real Postgres until the M2-I rehearsal (add a webhook leg to the
  rehearsal script: stop a source, watch the alert fire AND the webhook land,
  kill the worker mid-POST and watch the stall revival).
- Tracked in: `pipeline/notify/*`, `app/src/lib/notifications/*`,
  `docs/decisions/0010-alerting-notifications.md`.

### I-59 · GC residuals: grace-window item-id reuse; datetime-keyed retention; per-run collect counts 🟡
Accepted M2-F simplifications (ADR 0011 "Consequences"):

- **Item-id reuse inside the grace window loses the new bytes**: an open
  `asset_gc` mark is prefix-scoped, so re-creating an item under the same id
  before its old mark is collected means the collector later deletes the NEW
  objects too. Operators should treat an id as unavailable until its mark
  collects (or clear the mark by hand). A future refinement could snapshot the
  key list at mark time instead of the prefix.
- **Retention keys on `pgstac.items.datetime`** (observation time). Items
  without a usable datetime never age out; ingest-time-based retention would
  need a per-item ingest timestamp the catalog does not carry today.
- `asset_collect` logs deleted-object counts but does not persist them per
  mark; if evidence-grade byte accounting is ever needed, add a count column.
- The BFF's archived check and GC marks are best-effort against a reachable
  app DB; in the (unlikely) window where the catalog is up but the app DB is
  down, an item write to an archived collection would pass through and a
  delete would go unmarked — both self-heal (the sweep re-expires; re-delete
  re-marks).
- Tracked in: `pipeline/gc/*`, `app/src/lib/gc/marks.ts`,
  [ADR 0011](decisions/0011-retention-gc.md).

### I-67 · Item edit form crashes on pipeline-ingested items (remote GeoJSON $ref) 🔴
Found by the M2-I rehearsal (2026-08-28): `/collections/[id]/items/[itemId]/edit`
crashes with "Could not find a definition for https://geojson.org/schema/Geometry.json"
for any item carrying the projection extension's `proj:geometry` — RJSF cannot
resolve the remote GeoJSON schema `$ref` (console also shows repeated
`MissingRefError: can't resolve reference __rjsf_rootSchema#/definitions/assetfields`).
Every `raster_auto`-ingested item includes `proj:geometry`, so the edit-form
surface is effectively broken for pipeline-produced items; UI edits fall back
to nothing (the page error-boundaries out). Candidate fixes: pre-resolve/cache
the GeoJSON schema through `/api/extensions/resolve-schema`, strip or inline
remote `$ref`s before handing the schema to RJSF, or register the GeoJSON
definitions statically. Workaround: the audited BFF `PUT /api/catalog/...`
(used by the rehearsal to backdate an item).
- Tracked in: here; found in `TODO.md` "From M2-I".

---

## OGC serving (pre-M5 hardening, 2026-08-29)

### I-68 · Canonical asset hrefs are not resolvable by the tiling service 🟡
titiler-pgstac reads asset hrefs out of item JSON. Platform-ingested items
carry app-relative `/api/assets/...` hrefs (ADR 0005 — bytes are reachable
only through the app), which GDAL/the tiler cannot open, so raster tiling of
canonical platform assets does not work out of the box; reference-mode items
with absolute URLs (and `s3://stac-higher/...` hrefs, via the compose
service's MinIO credentials) tile fine. Resolving it properly means choosing
between absolute asset hrefs at ingest (`ASSET_HREF_BASE` set to an absolute
base — couples items to a deployment hostname), a titiler-side href rewrite,
or serving-path presign integration — a Phase 8 cloud-deployment decision,
not a local one. The Settings-tab serving panel states the limitation.
- Tracked in: `docs/serving.md`; decide in Phase 8.

### I-69 · Serving toggle is advisory until I-1 🟡
`collection_settings.serving_enabled` is LINK-LEVEL only: it controls whether
the collection page advertises the titiler/tipg endpoints, not whether those
services answer for the collection. Real gating needs the per-collection
read-visibility layer (I-1) applied at/in front of the serving services. The
UI copy says so. Revisit when I-1 lands.
- Tracked in: `docs/serving.md`, migration 019; depends on I-1.

---

## Phase 9 — Processes (planning, 2026-08-27)

Open design questions from the Phase 9 planning pass (ROADMAP §9 Phase 9 /
M5; ADRs [0013](decisions/0013-process-executor-isolation.md) and
[0014](decisions/0014-process-output-path.md), both proposed). None block
current work; the open ones must be settled by the Phase 9 design spec
(`TODO.md` P9-F) before any implementation.

### I-60 · Milestone ordering: does Processes (M5) precede or follow M3? 🟢
**Settled 2026-08-27: M5 precedes M3** (local-first steering order, ROADMAP
§9: M2-I → Phase 7 → Phase 9/M5 → M3 → Phase 8/M4 — no cloud environment is
targeted before Phase 8). Rationale: Processes is fully buildable in docker
compose; the load measurement is not, and running it *after* M5 means M3
measures the real workload including process-generated items. The standing
obligation this leaves: M3's ~30 items/s arithmetic MUST include
process-generated volume (ROADMAP §10), and M5 implementation stays within
the singleton pre-M3 architecture (I-40 untouched).
- Tracked in: ROADMAP §9 (steering order, M5 note), §10.

### I-61 · Executor backend: local dev vs. GovCloud 🔴
ADR 0013's recommended container-per-run interface needs concrete backends:
locally, docker-socket availability inside the compose pipeline container
(and the hardening cost of granting it); in cloud, ECS/Fargate task quotas,
launch latency (felt on interactive test runs), and GovCloud service
availability vs. a K8s Job. A backend pair that keeps local dev a single
`docker compose up` is a hard requirement (§1 locked decisions).
- Tracked in: ADR 0013 "Revisit"; investigated by `TODO.md` P9-A.

### I-62 · Run-log storage & retention 🔴
Run logs land in object storage under a `log_ref` (ADR 0013 invariant) —
but under which prefix (a `logs/` sibling of `assets/`/`staging/` in §5.3?),
with what size cap per run, and which sweep ages them out (a
`history_retention` leg keyed to `process_runs` pruning? an `asset_gc`
reason? a plain TTL like staging)? Log bytes from a chatty process are
unbounded without a policy.
- Tracked in: here; decided by P9-F.

### I-63 · `process_stalled` expectation: per-source or per-process? 🔴
The `run_within_seconds` expectation could live on each `process_sources`
row (mirroring per-association expectations — natural for cron triggers with
different cadences) or once per process (simpler, matches how operators
think about "is my process running"). Affects the alert dedup key and the
`/monitoring` flows shape.
- Tracked in: here; decided by P9-F with ADR 0010's dedup model in view.

### I-64 · Cycle-detection scope for the output→source loop hazard 🔴
ADR 0014 refuses associations that close a feedback loop. Direct
source/output edges are cheap to check at association time. But a loop can
also close transitively through delivery→re-ingest edges (process output →
delivery association → external system → ingest association → source
collection) — statically visible only while both ends are our associations,
undecidable once an external system is in the path. Where does the refusal
stop? **Backstop settled 2026-08-27:** regardless of detection scope, a
per-process run-rate ceiling with an alert on breach is a design-spec
requirement — it caps the blast radius of any loop the detector cannot see.
The detection scope itself remains open.
- Tracked in: ADR 0014 "Revisit", ROADMAP §10; scope decided by P9-F.

### I-65 · Inline-editor dependency choice + supply-chain review 🔴
`/processes/[id]` wants a code editor (CodeMirror vs. Monaco) — a
significant new frontend dependency under the no-new-deps-without-need rule
and the platform's compliance posture (supply-chain review before adoption).
A plain textarea may be acceptable for a first slice.
- Tracked in: ROADMAP §8 Phase 9 table; evaluated by `TODO.md` P9-C.

### I-66 · OGC API — Processes conformant facade: worth exposing? 🔴
The Phase 9 capability is the domain of the OGC API — Processes standard
(execute/jobs/results; Part 3 covers workflow chaining). Our internal design
is deliberately richer (event triggers, revisions, group ownership) and must
NOT be contorted to the standard — but a conformant read/execute *facade*
over `/processes` + `process_runs` (the standard's `/processes` and `/jobs`
resources map cleanly onto ours) would be a credible interoperability story
for OGC-conformance-minded deployments (NOAA). Evaluate: conformance classes
worth claiming, auth fit (the standard assumes OIDC-ish bearer auth — fine),
and whether the facade is a Phase 9 slice or a later add-on. Related: the
serving exposure work (titiler-pgstac / tipg, `TODO.md` "Pre-M5 hardening")
covers OGC API Tiles/Features — together these make the platform's OGC
story: Features (STAC API core), Tiles, and potentially Processes.
- Tracked in: ROADMAP §9 Phase 9; decided by P9-F.

---

## Resolved — archived

Fully-closed entries live in [`ISSUES-ARCHIVE.md`](ISSUES-ARCHIVE.md); stubs here keep inbound references landing.

- **I-4** · Adapter `list/get` live coverage — 🟢 resolved (narrowed; FTPS residual tracked as I-6)
- **I-12** · `connection_checks` accumulation — 🟢 resolved (M2-G `history_retention`)
- **I-21** · Reference-mode ingest stalled at `settled` — 🟢 resolved (Slice C)
- **I-27** · pgstac NOT NULL geometry vs `defaults_only`/`sidecar` — 🟢 resolved (Slice B4a geometry chain)
- **I-35** · Pipeline image missing `libexpat1` — 🟢 resolved (Slice C)
- **I-37** · `on_update` keys off `delivery_log`, never the outbox `op` — 🟢 resolved (Slice B-ii, as designed)
- **I-38** · Dispatcher item-visibility race — 🟢 resolved (Slice C cool-off retry)
- **I-39** · `dispatch_once` per-event error isolation — 🟢 resolved (B-iii)
- **I-44** · `delivery_log.attempts` lifetime counter — 🟢 resolved (B-ii reset)
- **I-45** · SFTP/FTP delivery live verification + `move()` bodies — 🟢 resolved (B-iii)
- **I-49** · Reference-mode delivery residuals (B-ii review) — 🟢 resolved (B-iii)
- **I-50** · UI catalog writes under auth enforcement — 🟢 resolved (ADR 0008 BFF)
- **I-51** · ADR 0009 deletion semantics, both halves — 🟢 resolved (pre-B-iii wave + M2-F)
- **I-52** · Ingest crash recovery (`fetching` stall / terminal `failed`) — 🟢 resolved (B-iii sweeps)
- **I-53** · Cross-runtime contract golden fixtures — 🟢 resolved (`tests/contract-fixtures/`)
- **I-54** · pgstac 0.9.10 constraint parser vs fractional seconds — 🟢 resolved (`pgstac-migrate` one-shot, 2026-07-30)
- **I-55** · No queue-level ingest retry / `stored` stall — 🟢 resolved (2026-07-30)
- **I-56** · Delivery pre-record + stall sweep — 🟢 resolved (M2-0)
