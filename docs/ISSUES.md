# Outstanding issues

Known gaps, residual risk, and deferrals — tracked honestly so they aren't mistaken for "done." Status: 🔴 open · 🟡 accepted/mitigated · 🟢 resolved · ⚪ deferred-by-design.

Each entry: what it is, why it exists, and where it's tracked. Close an entry by moving it to 🟢 with the resolving commit/PR; fully-closed entries move to [`ISSUES-ARCHIVE.md`](ISSUES-ARCHIVE.md), leaving a one-line stub in the list at the bottom so inbound references still land. Entries that keep an open or amber half stay here. **This file is not sorted by number** — sections are chronological, not numeric — so a new entry must take the next number above the file's actual MAXIMUM `I-N` (`grep -n '^### I-' docs/ISSUES.md`, sort numerically, take the top), never just the highest number visible near wherever you're inserting.

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
`GET /api/assets/...` requires an authenticated identity (unauthenticated → 403) but does **not** yet scope reads to the caller's groups / the collection's visibility — that is the same capability deferred as I-1 (read-visibility). Until it lands, any authenticated user can mint a download URL for any asset. In dev-bypass the static operator satisfies the check, so local flows work. **Phase 7 amendment (2026-08-30): unchanged by push ingest** — bearer-authenticated push clients are "any authenticated user" too, so they can read any asset. Still I-1's problem.
- Tracked in: [ADR 0005](decisions/0005-asset-service.md); depends on I-1 / [ADR 0002](decisions/0002-auth-proxy-enforcement.md); [`push-ingest.md`](push-ingest.md) "Limits".

### I-14 · Manual uploads go direct-to-canonical; no server-side validation ⚪
Item-form uploads presign straight into canonical storage (trusted RBAC'd writer, ADR 0005) — there is **no finalize step** validating/checksumming the bytes, and no staging quarantine. **Phase 7 amendment (2026-08-30): the untrusted external push path (staging → validate → move to canonical) now exists** ([`push-ingest.md`](push-ingest.md)), but manual UI uploads deliberately stay direct-to-canonical — ADR 0005's revisit stands; Phase 7 does not force the UI through staging.
- Tracked in: [ADR 0005](decisions/0005-asset-service.md); ROADMAP §6.2.

### I-15 · Presign endpoint must be client-reachable (escalated by Phase 7) 🟡
The app signs URLs offline, so `S3_ENDPOINT` must be reachable by whatever **uses** them. Originally that meant the browser; **Phase 7 amendment (2026-08-30): staged-upload presigned PUTs are handed to external push clients, so `S3_ENDPOINT` must now be reachable from push clients' networks — a superset of the browser-reachable requirement.** On the host, `http://localhost:9000` works for host-local clients only. If the app is ever run **inside compose**, `S3_ENDPOINT` must be set to a client-reachable host — never `http://minio:9000`, which no external client can resolve. Defaults assume the host-run dev server and host-local clients.
- Tracked in: header comment in `app/src/lib/storage/config.ts`; `.env.example`; [`push-ingest.md`](push-ingest.md) "Prerequisites".

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

### I-19 · Adapter `get` fully buffers large assets — 🟢 resolved for object stores (M3-C, 2026-09-14)
Copy-mode FETCH now server-side-copies when the platform can read the source
bucket and otherwise streams a bounded multipart upload
(`ingest/transfer.py`, `platform.upload_stream`); `StorageAdapter.open()` is
the streaming seam and `S3Adapter` implements it. SFTP/FTP keep the buffered
`get()` — that residual is I-83.

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

### I-26 · Memory-buffered raster reads in EXTRACT — 🟢 resolved for object stores (M3-C, 2026-09-14)
EXTRACT opens the primary raster in place through a `RasterLocation`
(`/vsis3`, `ingest/raster_io.py`); `MemberByteSource.locate` supplies it and
`build_raster_auto` / `geometry_from_raster` accept it. Buffering remains only
for sources that cannot be located (I-83) and as the best-effort geometry
fallback for HDF-backed files GDAL refuses to open through VSI.

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

### I-67 · Item edit form crashes on pipeline-ingested items (remote GeoJSON $ref) 🟢 (resolved 2026-08-29, P7-X)
**Resolved by a schema-preparation layer in front of RJSF**
(`app/src/lib/extensions/ref-resolve.ts`): extension schemas now have their
STAC-template `definitions.fields` hoisted to the root (killing the
`__rjsf_rootSchema#/definitions/...` MissingRefError class along the way) and
every remote `$ref` inlined before RJSF sees them — bundled GeoJSON schemas
first (`geojson-schemas.ts`, offline, no fetch), anything else pre-resolved
through the `/api/extensions/resolve-schema` seam (which now also serves a
stale cache row when the upstream host is unreachable); a `$ref` that
genuinely can't be resolved (e.g. `proj:projjson`'s PROJJSON document, whose
internal refs can't be rebased) degrades that ONE field to a raw-JSON editor
(`RawJsonField`) instead of taking down the form. The nested-`<form>`
hydration warning was fixed in the same pass — RJSF renders with
`tagName="div"` inside the RHF page form; submit/merge-into-`item.properties`
behavior is unchanged (onChange-only wiring).
- Tracked in: `app/src/lib/extensions/ref-resolve.ts`,
  `app/src/components/extensions/ExtensionFields.tsx`; tests:
  `extension-ref-resolve.test.ts`, `extension-fields.test.tsx`,
  `schema-cache.test.ts`.

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
not a local one.
**2026-09 (G-4):** the local half is closed — the compose tile server is a
derived image that maps canonical hrefs to the platform bucket
(`infra/titiler/`, GOES spec §7.1) — the Settings and Overview serving copy
was corrected to say so when the collection preview landed. Remaining: the
cloud deployment's choice (same mapping vs presign integration) and the fact
that the tiler's bucket credentials see everything (I-1).
- Tracked in: `docs/serving.md`, `infra/titiler/README.md`; cloud half decided in Phase 8.

### I-69 · Serving toggle is advisory until I-1 🟡
`collection_settings.serving_enabled` is LINK-LEVEL only: it controls whether
the collection page advertises the titiler/tipg endpoints, not whether those
services answer for the collection. Real gating needs the per-collection
read-visibility layer (I-1) applied at/in front of the serving services. The
UI copy says so. Revisit when I-1 lands. Both preview surfaces — the G-5 item
overlay and the collection Preview tab — obey the same toggle and are not
gates either: it decides whether the page ASKS the tile server, not whether
the tile server would answer.
- Tracked in: `docs/serving.md`, migration 019; depends on I-1.

### I-112 · Stepping a raster frame series backward showed the wrong frame — 🟢 resolved (V-3, 2026-09-14)

**Was.** `RasterFrameStack` (and the collection Preview tab before it) kept the
previous frame painted beneath the current one and relied on React child order
for draw order. maplibre fixes draw order at `addLayer` time and only
`moveLayer`s when `beforeId` changes, so a frame that was ALREADY mounted
stayed where it was. Walking 1 → 2 → 1 left both frames at full opacity with 2
on top — the viewer saw frame 2 while the slider said 1. Forward playback was
unaffected (the incoming frame is always newly mounted, hence on top), which is
why the live GOES check never saw it.

**Fixed by V-3.** The stack states its draw order instead of inheriting it:
each mounted frame chains `beforeId` to the frame above it, the top frame to
the caller's target, and the always-mounted anchor to the lowest frame — so any
reorder changes a `beforeId` and react-map-gl issues the `moveLayer`. The
frames are rendered top-first because react-map-gl creates layers during
render, in tree order, and maplibre drops a layer whose `beforeId` target does
not exist yet; the anchor is rendered last for the same reason. An empty series
now keeps the anchor mounted, so the `/map` page's chain never points at a
target that comes and goes. Regression tests:
`app/src/__tests__/raster-frame-stack.test.tsx` (the chain after 1 → 2 → 1) and
`app/src/__tests__/collection-preview-tab.test.tsx` (the previous frame's layer
draws beneath the current frame's). Spec:
`docs/superpowers/specs/2026-09-04-map-page-design.md` §11.3.

---

## Phase 7 — push ingest (2026-08-30)

Deferred scope and residual risk from the Phase 7 design spec §13 (plus two
implementation-time additions), logged at the P7-I docs slice. Reference:
[`push-ingest.md`](push-ingest.md).

### I-70 · Direct-path push writes are unaudited 🟡
ROADMAP §5.5 promises every mutation audited, but external writes straight to
the proxy (`:8081`) land no `audit_log` row — only the staged-upload mint and
poll are audited app-side — and finalize's own catalog actions (rejection
delete, snapshot restore) run pipeline-side, where no audit writer exists.
The brokered path (spec §4.3) closes the gap for cooperative clients — its
writes go through the guard like any BFF write — and is the honest
mitigation; `docs/push-ingest.md` documents brokered as the default for
exactly this reason. The deferred remainder: proxy-side audit, or a
pipeline-side audit writer.
- Tracked in: here; [`push-ingest.md`](push-ingest.md); ROADMAP §5.5.

### I-71 · No rate limiting or per-group quota on push ⚪
An external client can fill staging or hammer finalize; the TTL sweep bounds
storage but not churn. The Phase 9 run-rate-ceiling requirement is the
analogous control — push gets one when either demands it.
- Tracked in: here; [`push-ingest.md`](push-ingest.md) "Limits".

### I-72 · No multipart upload — single presigned PUT caps file size ⚪
Staged uploads mint one presigned PUT per file, capping practical asset size.
Large-asset push waits for a real need — same class as ingest streaming
(I-19/I-26).
- Tracked in: here; [`push-ingest.md`](push-ingest.md) "Limits".

### I-73 · Extension-schema validation deferred (both ingest paths) ⚪
Finalize reuses the exact ITEMIZE gate (stac-pydantic core `Item` — offline,
pinned, no egress), so pushed and polled items pass the identical validator.
The cost: no `stac_extensions` schema validation on either path — ROADMAP
§6.2's "+ stac-validator on demand" remains an aspiration (spec §6.2:
stac-validator fetches remote schemas per item, the exact egress hole §5.5
exists to avoid). Anything stricter for push than for polled ingest would be
indefensible; if extension validation lands, it lands for both.
- Tracked in: here; `services/pipeline/src/pipeline/stac/validate.py`.

### I-74 · Asset filename orphans on update pushes ⚪
Replacing an item's assets with *different filenames* leaves the old
canonical files in place until item delete/retention — the ADR 0011 prefix
mark covers them then. Same class as re-ingest replacement; no unbounded
growth, just stale bytes under the item prefix.
- Tracked in: here; [ADR 0011](decisions/0011-retention-gc.md).

### I-75 · Enforced-mode proxy policy reads the app DB 🟡
The ADR 0015 filter factory gives the auth-proxy a read-only Postgres
connection to one table (`collection_settings`, ~15 s TTL cache) — a new
runtime coupling that exists only in the auth-enforced overlay. Acceptable
locally; the cloud deployment must grant the proxy a genuinely read-only
role (Phase 8 IaC note in ADR 0015).
- Tracked in: [ADR 0015](decisions/0015-proxy-write-policy.md);
  `services/proxy-policy/`.

### I-76 · `bulk_items` push unsupported (denied in enforced mode) ⚪
The factory returns constant-false for `bulk_items` requests without the BFF
header — deny, not validate (the factory never parses the bulk body shape),
closing the source-verified member-bulk-insert bypass. The denial is
500-shaped, not 4xx (upstream validate-middleware behavior; the integration
leg accepts any rejection). Supporting bulk push means teaching the factory
and finalize the bulk body shape — deferred until someone needs it.
- Tracked in: [ADR 0015](decisions/0015-proxy-write-policy.md);
  `tests/integration/` bulk-deny leg.

### I-77 · Claims mapping duplicated at the proxy policy ⚪
The factory maps roles from a raw claim path
(`POLICY_ROLES_CLAIM`, default `realm_access.roles`) independently of the
app's claims-mapper config; a deployment with exotic claims must configure
both. Logged; convergence is an OPA-era problem.
- Tracked in: `services/proxy-policy/src/stac_higher_proxy_policy/factory.py`;
  [ADR 0015](decisions/0015-proxy-write-policy.md).

### I-78 · Unclaimed-session push rejections are alert-invisible 🟡
A direct-path push whose staged hrefs name a session that does not exist (or
belongs to another tenant) is rejected by finalize **without** writing a
`rejected` ledger row — the resolver never stamps a foreign or non-existent
session's row, so the `push_rejected` monitor (which observes the ledger)
cannot see the class. Deliberate (P7-H decision: corrupting the ledger or a
new table were worse); the signal is `pipeline_finalize_items_total` +
warning logs only, documented in the monitor docstring. On the brokered path
the same condition 400s synchronously (`unknown_session`), so the blind spot
is direct-path-only. Revisit if Phase 9's staged-item path makes these
rejections ledger-backed.
- Tracked in: `services/pipeline/src/pipeline/flow/monitor.py` (docstring);
  `pipeline/finalize/`.

### I-79 · A delete event draining at the retry cap orphans bytes 🟡
Dispatcher-side GC marking for direct-path deletes (spec §7.3) routes mark
failures through the I-38 defer path, so a transient DB error retries rather
than draining unmarked. But the bounded-attempts cap still drains the event
with only a loud log — a delete whose mark never commits within the budget
knowingly orphans the item's bytes. An alert kind for this class (alongside
the I-78 question) is the candidate fix if it ever fires in practice.
- Tracked in: `services/pipeline/src/pipeline/dispatcher/loop.py`; found in
  the P7-F review.

### I-80 · I-46 delete+insert pairs: residuals of the P7-Z gate fix 🟡
The P7-Z rehearsal caught the transaction-API write path (BFF/proxy PUT)
splitting a client update into **delete+insert** outbox events (I-46), which
(a) made the dispatcher's §7.3 delete-marking GC-mark a LIVE item's canonical
prefix on every update push, and (b) made finalize's §6.3 op-discriminated
rejection tiers treat brokered updates as inserts — deleting the item instead
of restoring the snapshot (observed live: a rejected update deleted the item
and its rejection reason was the spurious mark's `gc_pending`). Fixed on
`ai/main` (2026-08-30): the dispatcher skips the mark when the item still
exists at claim time, and finalize discriminates by snapshot-presence first,
deleting only a provable create (op=insert AND no `item_events` history
predating the session; an unknown session — no mint anchor — now always
leaves the document). Residual risk, accepted:
- A true delete followed by an independent recreate before the dispatcher's
  claim skips the mark — the OLD version's bytes orphan (the I-74 class;
  logged).
- The provable-create check reads `item_events`, whose partitions die by
  operator-manual DETACH+DROP (I-11): a long-dormant pre-existing item whose
  history was dropped could misclassify as a create and be deleted on a
  rejected direct-path staged PUT. Requires manual partition drops plus a
  staged PUT to a dormant item via the discouraged path.
- Fixed in: `pipeline/dispatcher/loop.py`, `pipeline/finalize/push.py`,
  `finalize/repo.py` (`item_predates`); tests in `test_dispatch_loop.py` /
  `test_finalize_push.py`.

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

### I-61 · Executor backend: local dev vs. GovCloud 🟢 (settled 2026-08-29)
ADR 0013's recommended container-per-run interface needs concrete backends:
locally, docker-socket availability inside the compose pipeline container
(and the hardening cost of granting it); in cloud, ECS/Fargate task quotas,
launch latency (felt on interactive test runs), and GovCloud service
availability vs. a K8s Job. A backend pair that keeps local dev a single
`docker compose up` is a hard requirement (§1 locked decisions).
**Settled by P9-A + the approved Phase 9 spec**: local `DockerExecutor`
(Engine API via a least-privilege socket proxy — sibling launch 0.125s warm,
limits first-class), cloud ECS/Fargate RunTask in Phase 8 (available in
GovCloud, ~30–45s cold start, vCPU quotas), K8s-Job-on-EKS fallback
(EKS-on-Fargate absent in GovCloud). Details: ADR 0013 "Investigation".
**Cloud half RE-OPENED and re-decided 2026-09-02 (ADR 0019, proposed):**
GPU/CUDA processes are a requirement and **Fargate has no GPU support**, so
the ECS/Fargate recommendation is superseded — the cloud backend is
Kubernetes Jobs + Kueue on EKS (Auto Mode, available in GovCloud), and the
executor seam becomes submit-then-reconcile. The local `DockerExecutor`
half stands. Closes again at the K-9 gate with measured queue-wait and
cold-start numbers.
- Tracked in: ADR 0013 (accepted; cloud half superseded), ADR 0019, the K
  queue in `TODO.md`.

### I-62 · Run-log storage & retention 🟢 (settled 2026-08-29)
**Settled by the approved Phase 9 spec (§9)**: `logs/runs/{process_id}/
{run_id}.log` (a `logs/` sibling in the §5.3 layout), capture-time size cap
(`PROCESS_LOG_MAX_BYTES`, default 10MB, truncation marker), aged out by the
`history_retention` leg in lockstep with terminal `process_runs` rows — log
object deleted before the row; no `asset_gc` involvement (platform bytes,
not catalog assets).
- Tracked in: the Phase 9 design spec §9.

### I-63 · `process_stalled` expectation: per-source or per-process? 🟢 (settled 2026-08-29)
The `run_within_seconds` expectation could live on each `process_sources`
row (mirroring per-association expectations — natural for cron triggers with
different cadences) or once per process (simpler, matches how operators
think about "is my process running"). Affects the alert dedup key and the
`/monitoring` flows shape.
**Settled by the approved Phase 9 spec (§8): per `process_sources` row** —
mirrors the per-association expectation model (cadences differ per source),
reuses the M2-A editable-expectation UI shape, and slots the source id into
the ADR 0010 dedup key's association position; the `/processes` dashboard
aggregates per-source states into the process-level view.
- Tracked in: the Phase 9 design spec §8.

### I-64 · Cycle-detection scope for the output→source loop hazard 🟢 (settled 2026-08-29)
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
**Scope settled by the approved Phase 9 spec (§8)**: write-time DFS refusal
over OUR edges only ({ingest, deliver, process_source, process_output},
shared with the `/api/monitoring/graph` edge model; 409 with the path).
Paths through connections/external systems stay undecidable and out of
scope — the per-process run-rate ceiling + `process_rate_limited` alert
(spec §7) is the blast-radius backstop.
- Tracked in: ADR 0014 (accepted), the Phase 9 design spec §7–8.

### I-65 · Inline-editor dependency choice + supply-chain review ✅ (closed 2026-09-01)
`/processes/[id]` wanted a code editor (CodeMirror vs. Monaco) — a
significant new frontend dependency under the no-new-deps-without-need rule
and the platform's compliance posture (supply-chain review before adoption).
**Settled by P9-C + the approved Phase 9 spec**: slice 1 shipped a plain
textarea (dependency-free first accreditation surface); when editor UX was
justified, CodeMirror 6 — never Monaco (~98MB unpacked, worker architecture).
**Adopted in UI-5 (ADR 0017 §6).** Supply-chain review recorded here, since
this repo merges to `ai/main` rather than through a PR:
- **Packages (7, all pinned exactly via `--save-exact`):**
  `@codemirror/state` 6.7.2, `@codemirror/view` 6.43.10,
  `@codemirror/commands` 6.11.0, `@codemirror/language` 6.12.4,
  `@codemirror/lang-python` 6.2.1, `@codemirror/lang-json` 6.0.2,
  `@lezer/highlight` 1.2.3.
- **Why these and not `codemirror`:** the meta-package pulls autocomplete,
  search and lint whether or not they are used. Assembling from the
  individual modules keeps the dependency surface to what the editor
  actually needs.
- **Provenance:** all from the `codemirror` / `lezer` orgs (Marijn
  Haverbeke), the reference implementations for their ecosystem.
- **Install-time risk:** no postinstall scripts in any of the seven.
- **No theme package:** the editor theme is written against the app's own CSS
  variables, so the palette follows `global.css` in both modes and one more
  dependency is avoided.
- **Scope:** the process code editor ONLY. Every other textarea in the app
  stays a textarea (ADR 0017 §6 is explicit about this).
- Tracked in: the P9 scoping notes; the Phase 9 design spec §10; ADR 0017.

### I-66 · OGC API — Processes conformant facade: worth exposing? 🟢 (settled 2026-08-29)
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
**Settled by the approved Phase 9 spec (§11): worth claiming, as a
post-gate stretch slice.** Read-only + async-execute mapping over
`/processes` + `process_runs` (Part 1 async core; Core/JSON/Process
Description/Job list conformance candidates); the internal model stays
canonical and richer. Ships only after the native surface is rehearsed.
**2026-08-31 addendum:** the standard's Parts 2–5 (all unreleased drafts)
were surveyed and the conformance posture recorded in
[ADR 0016](decisions/0016-ogc-processes-conformance-posture.md): Part 1
v1.0 stays the claim target, the v2.0 draft's `collection-output` is the
planned representation of output collections (better than contorting
`output_items` into v1.0 `/results`), Part 5 provenance is a cheap future
add-on off the run ledger, and two constraints bind now (container-runtime
contract must not foreclose OGC Application Packages; run↔item provenance
linkage stays queryable). The facade remains a post-M3 stretch.
- Tracked in: the Phase 9 design spec §11; ADR 0016.

### I-81 · No cancel-run verb (runaway runs; OGC `dismiss` gap) ⚪
A `queued`/`running` process run cannot be cancelled: the only recovery
verbs are the retry budget (automatic) and Re-run (dead rows). An operator
watching a mis-deployed revision burn its timeout budget can disable the
process (stops FUTURE runs) but cannot stop the one in flight — the
executor's timeout is the only backstop. Cancellation needs executor
cooperation (stop the container, flip the row terminal, keep the crash-safe
ordering) plus an audited verb + UI affordance. Also the reason the future
OGC facade cannot claim the `dismiss` conformance class (ADR 0016 §3) —
the facade never fakes a verb the platform does not have.
- Tracked in: here; [ADR 0016](decisions/0016-ogc-processes-conformance-posture.md);
  `services/pipeline/src/pipeline/process/docker_executor.py` (timeout is
  the existing kill path a cancel would reuse).
- **Planned (2026-09-02):** lands with **K-4** (ADR 0019 spec §5.3) — the
  submit-then-reconcile executor makes cancel a `cancel_requested_at` flag
  the run watcher acts on; `cancelled` becomes a terminal status and the
  audited verb + UI button ship with it. Status moves to 🟢 when K-4 merges.

### I-82 · The M3 byte-volume model is declared, not measured 🟡
M3-S-B redid ROADMAP §2's arithmetic at 2.6M items/day against a **stated
model** of NOAA-class product sizes (2 MB / 25 MB / 250 MB at 60/30/10% of
items, mean 33.7 MB), chosen to span the three shapes that behave differently
— not against a census of the actual feeds. Every byte conclusion inherits
that: 88 TB/day ingest-origin, the 59%-of-storage case for referencing the
large tier, the FTP/SFTP honest-limits arithmetic, and the concurrency sizing
that follows from it. The *shapes* are robust (a heavy tail is the defining
property, and 10% of items carrying 74% of the bytes is what every
recommendation turns on); the absolute numbers are not. A real size census
belongs before any contractual scale or storage-cost commitment.
- Tracked in: here; `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md`
  (M3-S-B "Stated assumptions"); the M3 design spec §6 "What M3 explicitly
  does NOT do".

### I-83 · Streaming ingest reaches object stores only ⚪
The M3 streaming decision (M3-S-E) fixes whole-object buffering for **s3**
sources and canonical storage via GDAL `/vsis3` and streamed multipart
transfers. SFTP/FTP sources keep the buffered `adapter.get() -> bytes` path,
because there is no VSI handler that authenticates through our adapter. This
is consistent with ROADMAP §2's honest-limits posture — those protocols carry
NRT-subset volumes — and M3-S-B now attaches a number to it (~8 saturated
10 GbE streams to feed one such destination at envelope volume). It becomes a
real gap only if a deployment tries to run a high-volume SFTP source, which
the posture says it should not. M3-C landed both halves for s3 (2026-09-14);
this entry is now the only buffered path in the ingest byte path itself — the
best-effort geometry fallback, a `CanonicalByteSource` built without platform
access, and the public-URL stage (I-72) still buffer.
- Tracked in: here; I-19; `services/pipeline/.../connections/adapters/base.py`.

---

## UI remodel (ADR 0017, 2026-09-01)

Carried out of the remodel. Full per-slice follow-up list: `app/UI-TODO.md`.

### I-84 · `/api/alerts` omits `process_id` / `source_id` — 🟢 resolved (A-1, 2026-09-09)
`ApiAlert` gained `process_id` (the EFFECTIVE process — the alert's own, else
its source's parent, the same read-time COALESCE `collection_id` uses) and
`source_id` (raw). `buildProductRows` claims process alerts through the
product's wired processes and colours process lineage nodes from them;
`processVerdict` takes the open alert list and lets an open alert outrank the
run ledger; the pipeline graph indicts `proc:<id>` nodes and no longer paints
deployed processes `unknown`. The product Overview's "may relate to this
product" caveat is gone; the home page keeps its residual "not shown against
a product" line, which now covers only channel-anchored alerts and processes
wired to no product. No migration (024 already stored both columns); no
fixture (the row shape is app-only). Commit: see `git log --grep I-84`.

### I-85 · Storybook missed the fonts (and, it turned out, not the scan) — 🟢 resolved (UI-12)
The FONT half was real: only the app imported `@fontsource`, so every story
rendered in the system fallback stack — Storybook showed a typography the
product does not use. Fixed by `packages/shared/.storybook/preview.css` plus
the four font imports in `preview.tsx`, with the packages declared in
`packages/shared` devDependencies. Verified in a built Storybook:
`document.fonts.check` passes for both faces.

The SCAN half was a false alarm. Shared-only utility classes were never
missing: verified by building with and without an `@source`, with a marker
class placed in a shared component — generated either way, because Storybook's
Vite root IS `packages/shared`, so Tailwind's auto-detection already covers
`src/`. The app needs its explicit `@source` for the opposite reason (its Vite
root is `app/`). The directive is kept in `preview.css` as belt-and-braces and
labelled as such.


### I-86 · No true item count per product 🟠
The home product list shows ingest-derived `flow_stats.items` (labelled
"ingested"), not an item count: a real count needs `numberMatched` per
collection — one request each — or a rollup no endpoint exposes. The same
constraint caps the product Overview tab. Revisit if operators ask for it;
the honest label is the mitigation.

### I-87 · `flow_stats_daily` counter semantics are not pinned 🟠
`runs` / `failed` / `dead` are deltas of a live `flow_stats` jsonb, and
nothing states whether `runs` includes failures. UI-5 therefore derives
process success from the RUN LEDGER and labels it "last N runs" rather than
"30d". Pinning the semantics (a contract fixture would be the right place)
would let the UI report a real 30-day rate. Until then, do not relabel the
current number.

### I-88 · `overview.ts` derivations are untested — 🟢 resolved (UI-11)
Covered by `app/src/__tests__/overview.test.ts` (27 cases over health ranking,
the three alert-attribution paths, unattributed alerts, lineage roll-up,
ingest-only counts and `successRate`). Residual noted in
`app/UI-TODO.md`: `buildProductRows`' `lateFlow` branch is unreachable, because
`isLate`'s match set is already claimed by the firing/acknowledged branches.
The verdict is identical either way, so it is dead, not wrong.

### I-89 · External-catalog CRUD was removed with UI-10 🟡
Before UI-10, selecting an external catalog made `/collections*` write to it —
directly or through `/api/proxy`, never through the ADR 0008 BFF, so those
writes were unaudited and un-RBAC'd. UI-10 pinned the product pages to the
built-in catalog and gave external catalogs a **read-only** browser at
`/catalogs/[catalogId]/collections*` (lead decision, 2026-09-01). This is the
remodel's one deliberate capability removal. If generic STAC-client CRUD is
wanted back, it needs its own design: which identity signs the write, what gets
audited, and whether `/api/proxy` should carry mutations at all — not a revived
dual-mode page.

**Enforced in the client since UI-14**, not just conventionally: `stacFetch`
refuses a write whose catalog is not the built-in one, so a future caller that
passes an explicit `endpointUrl` cannot reinstate the old behaviour by
accident. `POST /search` is exempt — STAC's item search is a read that speaks
POST, which is also why `/api/proxy` cannot simply be narrowed to GET/HEAD.

**Still open (deliberately):** `/api/proxy` is `export const ALL` — it will
forward any method to the declared endpoint, ungated and unaudited. Nothing in
the app sends a write through it any more, but the capability is there for
anything that can reach the route same-origin (`Sec-Fetch-Site: cross-site` is
rejected and `PROXY_AUTH_TOKEN` can lock it down; `safeFetch` blocks
private/loopback targets). Narrowing it is an `/api/*` change and needs the
`/search` carve-out above.

---

## GOES GeoColor loop (G queue, 2026-09-01)

### I-90 · STS inline-policy size caps a run's source collections at 8 🟡
A run's session policy (ADR 0018) grants read on each SOURCE collection's
canonical prefix. Real STS caps an inline session policy at 2048 characters
(MinIO is laxer), so `mint_run_credentials` refuses more than
`MAX_READ_PREFIXES = 8` source collections with a clear error rather than
minting a policy STS would reject. Typical processes have 1–3 sources. The
cloud backend (Phase 8) must re-measure against real STS with long collection
ids and either raise the bound, move to a managed policy per process, or grant
by tag.

### I-91 · Remote inputs without a matching association use an unauthenticated public GET 🟡
When a triggering item's asset href is absolute and no **enabled**
reference-mode ingest association's connection claims it
(`adapter.public_object_url("")` prefix match), the pipeline stages it with
`connections/http_fetch.fetch_public_url` — HTTPS only, `resolve_pinned`
first, no redirects, size-capped, but **no credentials**. A private
reference source therefore needs its association enabled (so its adapter is
used) or, once slice 2 lands, the `inputs` network level. Also: the public
fetch connects by hostname after validation (the same TLS-endpoint rebind
residual as I-2), and it buffers the object in memory like the adapters
(I-19). Both are inherited, not new.

### I-103 · The live GOES e2e depends on NOAA availability 🟡
`app/e2e/goes-loop.spec.ts` (G-7) needs the internet and the real
`noaa-goes19` bucket to be reachable and current; it is gated behind
`E2E_LIVE_NODD=1` and out of CI's default run for exactly that reason (spec
§14). If a NOAA outage or a quiet hour makes it flaky in practice, the fix is
a seeded offline variant driven by `pipeline.demo goes-seed --include`
against a granule copied once into MinIO, rather than loosening the gate.

### I-104 · The pipeline graph lists soft-deleted processes' collections as "Not wired" ghosts ✅ (closed 2026-09-04)
`loadGraph` (`app/src/lib/graph/storage.ts`) minted collection nodes from
the union of `collection_connections`, `process_sources` and
`process_outputs` collection ids, but only the first branch excluded
soft-deleted rows; `loadGraphEdges` excludes deleted processes on every
branch. A soft-deleted process therefore contributed collection NODES with
no EDGES — degree-0 orphans that the `/graph` page showed under "Not wired".
Seen 2026-09-04 after four live e2e runs: eight `e2e-goes-{src,out}-*`
collections that no longer exist in pgstac.
**Fixed by P-1**: both process branches of the union now join `processes`
and require `deleted_at IS NULL`, matching `loadGraphEdges` exactly, so the
node set and the edge set can no longer disagree about what a live process
is. Regression test: `app/src/__tests__/graph-storage.test.ts` (SQL shape
plus a fake-database run asserting no surviving node has degree 0). Spec:
`docs/superpowers/specs/2026-09-04-pipeline-graph-views-design.md` §1, §10.

### I-105 · Six curated stactools packages ship without a live gate 🟡
Opened by the X-queue spec (§3.5, §10): `viirs`, `modis` (Earthdata
login), `landsat`, `sentinel1`, `sentinel2`, `naip` (requester-pays) have
no anonymous public source, so X-5 can only
import-smoke and unit-test their adapters against the packages' own
fixture files. Their live gate is owed the first time a credentialed
connection to one of those archives exists. Until then a registry entry
marked `access: credentialed` is a promise the platform has not verified
end to end. X-2 narrowed it: `modis`, `landsat`, `naip` and `sentinel2` ARE
tested against their packages' own fixture files (metadata-only where the
rasters are not vendored); `viirs` (no fixture in its repo) and `sentinel1`
(3 MB of SAFE annotation) are tested with the package's `create_item`
replaced by a double, so for those two only the platform's half — file
picking and the SAFE rebuild — is proved.

### I-106 · A process cannot take a static reference asset as an input 🟡
ADR 0018 stages the TRIGGERING items into a run; there is no way to hand a
process a fixed reference raster (a nighttime-lights layer, a DEM, a land
mask) on every run. The lead chose GeoColor's night side as tint-only
(G-8) for exactly this reason: NOAA's city-lights layer needs a static
Black Marble raster the run cannot fetch (network `isolated`) and cannot
be given. Shape when wanted: a catalogued reference collection granted
read-only in the run's STS policy and named in the revision (`runtime.
reference_inputs`), staged under `inputs/reference/` — an ADR-sized
addition, not a slice of G-8.

### I-108 · Collection preview playback is paced by the tile server 🟡
The Preview tab's first pass through a 50-frame series runs at roughly 1.5
frames a second, not the nominal rate: every frame is a fresh titiler render
off the object store, and playback deliberately WAITS for tiles
(`map.areTilesLoaded()`, 10 s cap) rather than advancing onto blank frames.
Replays run at the full rate off the browser cache, so the limitation is the
first pass — the one a demo watches. The fix is server-side warming (a mosaic
tile cache, or pre-rendering a series' tiles), NOT a deeper client lookahead:
every mounted maplibre source competes for the same handful of connections to
the tiler, and widening the window measurably starves the frame on screen —
that is what the shipped implementation had to back out of. Related: the tab
has no e2e coverage, since the suite does not assume a tile server; gate any
future spec the way `goes-loop.spec.ts` gates on `E2E_LIVE_NODD`.
- Tracked in: `docs/serving.md` "Collection preview", `CollectionPreviewTab`.

### I-107 · Three curated stactools packages have no release to pin 🟡
The lead's curated set (X-queue spec §2) named fourteen packages; three of
them — `noaa-nwm`, `noaa-sst` and `hls` — have **never been published to
PyPI**. They exist only as repos under `stactools-packages` with no tags or
releases at all (last pushed 2023-10, 2021-09 and 2022-08 respectively), so
there is no `package==version` to pin and nothing for the image's build-time
import smoke test to prove. X-1 therefore ships **eleven** registry entries,
not fourteen (lead's call, 2026-09-04), and X-5's live gate B covers the four
remaining anonymous packages (`goes-glm`, `noaa-hrrr`, `noaa-mrms-qpe`,
`noaa-cdr`) beside gate A's `goes`. Shape if wanted later: a `source`
discriminator on the registry entry (`pypi` with a version, or `git` with an
immutable commit SHA) so the three can be installed from their repos — a
supply-chain decision, not a registry-schema one, which is why it was not
taken unilaterally. Cheaper trigger: one of the three cutting a release.
- Tracked in: X-queue spec §2/§3.5; `tests/contract-fixtures/README.md`.

### I-109 · Three stactools packages cap pystac below the platform pin — overridden, not dropped 🟡
Found by X-2's first step (the resolver, in one second): `stactools-goes-glm
0.2.4` and `stactools-noaa-hrrr 1.0.1` declare `pystac<1.12`,
`stactools-sentinel1 0.8.1` `pystac~=1.9.0`, against the platform's
`pystac==1.15.1`. Spec §11's fallback was to drop such entries, but that
would have removed two of gate B's four anonymous packages — and all eleven
import, build items and pass their adapter tests on 1.15.1 (the runtime
image's smoke and the pipeline suite's adapter tests re-prove it on every
build and CI run). So `Dockerfile.stactools` installs with a uv
`--override pystac==1.15.1` and the pipeline's `[tool.uv]
override-dependencies` carries the same line. The cost: an upstream release
that REALLY needs an old pystac would fail at the smoke or the adapter test
rather than at resolution — which is the earliest visible point anyway. Also
pinned beside it: `setuptools<81`, because eight of the eleven still import
`pkg_resources` (removed in setuptools 81). Revisit when the three lift
their caps; the pin check does not see the override, only the eleven.
- Tracked in: `services/process-runtime/Dockerfile.stactools`,
  `services/pipeline/pyproject.toml`, `test_stactools_runtime.py`.

### I-110 · SAFE products and NAIP need the SOURCE path, which only reference-mode hrefs carry 🟡
The ingest stages a group's files flat, under their basenames
(`staging/runs/{run}/inputs/{batch}/{item}/{filename}`); the relative path
inside a product survives only in the draft's asset hrefs, and only when the
association is reference-mode (a canonical-mode ingest's hrefs are
`/api/assets/{collection}/{item}/{filename}`). The `sentinel1` and
`sentinel2` adapters rebuild the `.SAFE` tree from those hrefs (symlinks in a
scratch dir) and the `naip` adapter reads the state from the archive's
`/{state}/{year}/` segment; with canonical hrefs they fail the item with a
reason naming reference mode. Also a limit on the platform side, not the
adapters: a SAFE's files are keyed by stem, so two files with the same
basename in different subdirectories (rare in SAFE, not impossible) would
collide when staged. Shape if it bites: stage grouped files under their
source-relative path (an ADR 0018 manifest addition, `relative_path` per
asset) so every adapter — and any hand-written extractor — sees the tree.
- Tracked in: `stac_higher_stactools/adapters/_files.py` (`rebuild_tree`),
  `test_stactools_adapters.py`.

### I-111 · A built-in process is named from its registry label, so a hand-written process with that name blocks the pick 🟡
`POST /api/processes/builtin` creates the group's process with the registry
entry's `label` as its name (spec §7), and migration 022's live-name index is
per `(group_id, name)`. A group that already has a hand-written process
called "GOES-R ABI (L1b / L2)" gets a 409 from the pick, naming the
collision; renaming that process clears it. Not resolved automatically
because a suffix ("… (2)") would make the built-in one harder to find than
the collision is to fix. Two smaller X-4 limits recorded with it: the
picker's `supports` filter is the spec's rule taken literally (`single_file`
⇔ grouping `none`, `grouped` ⇔ `shared_basename`), so a single-file package
cannot be picked for a grouped association even where its adapter would
cope; and a built-in process whose registry id has since left the registry
keeps running its deployed revision but cannot "Update to current" (409
naming the drift) — the operator recreates it from whatever replaced the
entry.
- Tracked in: `app/src/pages/api/processes/builtin.ts`,
  `IngestFormDialog.tsx` (`builtinSupportsGrouping`), `api-processes-builtin.test.ts`.

## Process compute — Kubernetes + Kueue (K queue, 2026-09-02)

Opened by `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md`
§15 and ADR 0019 (proposed). All five are design-time findings from
primary-source research on 2026-09-02; none is measured yet.

### I-92 · Kueue quota and Karpenter capacity are not linked 🟡
Kueue's autoscaler admission check drives the ProvisioningRequest API,
which cluster-autoscaler and GKE implement — **Karpenter does not** (its
CapacityBuffer API is the intended replacement; kueue#9662 tracks the
migration with no target release). On EKS, Kueue admits against its
*nominal* quota, the pod goes Pending, and Karpenter reacts. So the
ClusterQueue quota per flavor must be mirrored by hand to the NodePool
`limits` (ADR 0019 invariant), and `waitForPodsReady` must evict an admitted
job whose node never arrives back to the queue. A quota above the pool's
ceiling admits jobs that never schedule.
- Tracked in: ADR 0019; spec §7.3; K-6 (`waitForPodsReady`), K-8 (limits =
  quota). Revisit when Kueue supports CapacityBuffer.

### I-93 · Run credentials vs. queue wait 🟡
Run-scoped STS credentials are minted before submit (they are in the Job's
environment) and today last `max(900, timeout + grace)`. A run that waits
two hours for a GPU would start with expired credentials. K-4 mints for
`max_queue_wait + timeout + grace` and requeues (no attempt spent) any run
still pending with less than `timeout + grace` left — losing its queue
position. In EKS the pipeline's own credentials come from Pod Identity /
IRSA, and `AssumeRole` from role credentials is **role chaining, capped at
one hour**, which bounds cloud profiles' `max_queue_wait_seconds` unless the
run role is assumed with web identity directly. Measure at K-9.
- Tracked in: spec §5.4; K-4; K-9.

### I-94 · Run logs are captured at exit only ⚪
Both backends read the run's combined output once, at exit, capped at
`process_log_max_bytes`. A Kubernetes pod lost to node failure loses its
log entirely (`error = "log unavailable"`), and an hour-long GPU run has no
live tail for the operator watching it. A rolling capture (periodic
`pods/log` with `sinceTime`, appended to the `log_ref` object) is the
follow-on; not in the K queue.
- Tracked in: spec §6.1; here.

### I-95 · GovCloud GPU families lag commercial regions 🟡
Verified 2026-09-02 against the EC2 instance-type region table: neither
GovCloud region has g5 (A10G), g6e (L40S) or g7/g7e (Blackwell RTX PRO);
p4d/p5 are us-gov-west-1 only; p6-b300 is us-gov-east-1 only. Present in
us-east-1 and both GovCloud regions: g4dn (T4), g6/gr6 (L4), p6-b200
(B200). Profile files are therefore **per deployment/region** — the reason
profiles are documents, not code. Also recorded here: PyTorch is not in the
slice-1 CUDA image (cupy + numba only); demand for a `process-runtime-torch`
variant is the signal to build one.
- Tracked in: spec §10, §11.1; K-7, K-8.

### I-96 · Per-group Kueue quotas deferred ⚪
Slice 1 runs one ClusterQueue and one LocalQueue for every group; one
group's burst can starve another's runs (priority classes separate test
runs from triggered runs, not groups). Kueue's mapping is one ClusterQueue
per group in a Cohort with fair-sharing weights (GA since v0.17); the
profile's `queue` field and a per-run group label are in place so the split
is a manifest + profile-file change.
- Tracked in: spec §7.4; here.

## Ingest window + retention cap (W queue, 2026-09-02)

Opened by `docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md`
§10 when W-1 landed. Two of that section's five risks are deliberate and
documented rather than open: prefix expansion assumes a UTC, zero-padded,
date-partitioned key layout (a bucket keyed otherwise cannot use the template
and filters after a full listing instead), and `INGEST_MAX_WINDOW_PREFIXES`
is a blunt refusal, not a degrade-to-daily (silently changing the granularity
would change which files are found).

### I-97 · The ingest window gates on upload time, not observation time 🟡
`window` matches `FileEntry.mtime` — when the producer uploaded the object.
For GOES that trails the scan time by minutes; a producer that backfills old
data under a new timestamp could be admitted by a recent window even though
its content is old. Gating on observation time requires reading the file,
which is the download the window exists to avoid.
- Tracked in: spec §3.1, §10. Revisit if a source's upload order stops
  tracking its observation order.

### I-98 · A source with no modified times ingests nothing once a window is set 🟡
S3 always reports an mtime; an FTP server without MLSD may not. With a window
configured such entries are skipped and counted (`undateable`) plus a warning
log line, but the Data flow tab shows nothing — an operator who sets a window
on an MLSD-less FTP source sees silence.
- Fix candidate: surface `undateable` (and `deferred_by_cap`) on the Data flow
  tab from the tick counters.

### I-101 · `raster_auto` on GOES `ABI-L2-MCMIPC` netCDF yields no footprint (silent collection-extent fallback) 🟡
The same live gate: with `metadata.strategy: raster_auto` every MCMIPC item
carried `stac_higher:geometry_source: collection_extent` and no warning was
logged, after downloading ~39 MB per file. The GOES spec §9 assumed "the
built-in netCDF path already derives it"; for the geostationary CONUS
product it does not (the subdataset-aware best-effort read finds no usable
georeferencing, or none that reprojects). The fix belongs in G-6/G-7 —
either the extractor derives the footprint from the netCDF's `goes_imager_projection`
attributes, or `geometry_from_raster` learns the geostationary case — and
the plan for those slices should start by reproducing this on one file.
Until then a GOES association should use `defaults_only` with
`geometry: "collection"` so it does not pay for a download it cannot use.

**Cause (2026-09-02, G-6 planning):** the netCDF container dataset has no
geotransform — only its subdatasets (`NETCDF:"file":CMI_C02`) are
georeferenced, and `geometry_from_raster` opens the container. The GOES
extractor (G-7) derives the footprint from the C02 subdataset; the built-in
path is unchanged.

### I-102 · The run planner issues one ledger query per canonical-href input item, and extract finalize's config parse runs inside the batch 🟡
Two G-6 findings, both bounded rather than fixed:

- `ProcessRepo.reference_source_hrefs` (Task 9b) is called once per input item
  that carries a canonical href, to find out whether it is reference-mode —
  an N+1 on the run-launch path. Batchable into one query per launch when it
  matters.
- In the extract finalize branch, `parse_ingest_config` and `build_adapter`
  run once before the per-item loop, so a malformed association config or
  unreadable credentials raises the whole batch out of the job rather than
  failing item-by-item. Bounded: `TRIGGER_RETRY` retries the run, and the
  `extracting` stall sweep fails ledger rows whose run never completes.

### I-99 · A small `max_files_per_poll` against a wide window leaves a long-lived `settled` backlog 🟡
Files admitted but not yet fetched sit `settled` in the ledger; the cap paces
admission, so a very small cap against a very large window means a backlog
that drains over many polls. The existing stall sweeps cover correctness; the
backlog's *visibility* (how far behind the window the association is) has no
surface.
- Fix candidate: a "behind by N files / oldest admitted at T" read-out on the
  association card, computed from the ledger.

## NOAA-scale readiness (M3 queue, 2026-09-01)

Opened by `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` as M3-A
(pgstac write path) landed.

### I-113 · Per-upsert `PgstacDB` leaks an `atexit` handler and re-checks pgstac's version 🟡
Every `_upsert_sync` in the pgstac writer (`stac/pgstac_writer.py`) builds a
fresh `PgstacDB`, and pypgstac's `PgstacDB.connect()` registers an `atexit`
disconnect hook on every call — a long-lived worker retains one `atexit`
entry per upsert, growing without bound (significant at M3's ~30 items/s
target). `Loader.load_items` also re-runs `check_version()` per upsert, an
extra round-trip. Neither is new to M3-A — the pre-existing
`PgstacDB(dsn=...)` per call had both — and hoisting the `PgstacDB` out of
the per-call path would wrongly pin one pooled connection for the writer's
lifetime, so the fix belongs to a later M3 slice.
- Tracked in: `services/pipeline/src/pipeline/stac/pgstac_writer.py`
  (`_open_pgstac`); found in the M3-A docs review (Task 8).

### I-114 · A newly created partition is invisible to datetime-ordered search until the queue drains 🟡
Under `use_queue` (M3-A), a brand-new partition — a new collection, or a new
month on a `partition_trunc` collection — has no row in `pgstac.partition_steps`
(a materialized view refreshed only inside `update_partition_stats`, pinned
`pgstac.0.9.11.sql` ~L2621) until the queue drains it. `chunker` joins the
search planner's chosen relation names against `partition_steps`, so a
partition absent from that matview contributes no chunk range and is silently
dropped from datetime-ordered STAC search — not a planning slowdown, an
absence. Only NEW partitions are affected: a running deployment's existing
data already has its range in the matview. The window is bounded by one drain
tick (the job runs every minute, `pipeline.pgstac_queue_drain`) but is
UNBOUNDED if the drainer stops, which is why the stale-queue WARNING
(`pipeline_pgstac_query_queue_oldest_seconds`) matters beyond a performance
signal. A fresh `docker compose down -v` → `demo seed` → look-at-the-UI path
now has a roughly one-minute blind window on newly seeded data that did not
exist before this branch.
- Tracked in: `services/pipeline/src/pipeline/metrics.py` (queue-depth gauge
  comment), `services/pipeline/README.md` (`PGSTAC_QUEUE_DRAINER`); found in
  the M3-A final whole-branch review.

## Map page (V queue, 2026-09-07)

### I-115 · Layer opacity slider has no accessible name 🟡
`LayerRow` sets `aria-label="Layer opacity"` on the shared `Slider`, but that
prop lands on Radix's `SliderPrimitive.Root` — the element that actually
carries `role="slider"` is the Thumb, which takes its own label from a
`getLabel(index, count)` fallback that returns `undefined` for a single-thumb
slider. So `/map`'s opacity control ships with no accessible name. The real
fix is in `packages/shared/src/components/ui/slider.tsx` (thread a label
prop down to the Thumb), which is a hook-blocked shadcn primitive and needs
its own decision rather than a drive-by patch.
- Found in: V-2 whole-branch review.

### I-116 · `bboxToLngLatBounds` mis-fits a 3D bbox 🟡
`packages/shared/src/lib/map/bbox.ts` assumes a 4-element `[minx,miny,maxx,maxy]`
bbox. A STAC collection with a 6-element `[minx,miny,minz,maxx,maxy,maxz]`
extent fits to `[[minx,miny],[minz,maxx]]` — no error, just a camera that
lands somewhere wrong and a user who has to pan away. Pre-existing helper
limitation, but `/map`'s first-add camera fit (V-2, spec §4.6) is the first
caller that feeds it arbitrary user-chosen collection extents, making it
reachable.
- Found in: V-2 whole-branch review.

### I-117 · Pipeline graph health is state-blind: an acknowledged alert paints its node `error` 🟠
`unhealthyNodeIds` (`app/src/lib/monitoring/graph-decorate.ts`) returns a
`Set<string>` of anchors and ignores `alert.state` entirely, so a merely
`acknowledged` alert indicts its node exactly like a `firing` one. Pre-existing
for connection/collection anchors; A-1 (I-84) extends the same anchor set to
process nodes, so it now also colours process nodes wrong. Net effect:
`/monitoring` and `/graph` show a node red where `/processes` and the product
Overview (which both read `state` via `processVerdict` / `buildProductRows`)
show it amber. Fix: change `unhealthyNodeIds` to return a
`Map<nodeId, LineageHealth>` (firing → `error`, acknowledged → `warn`) and
thread it through `makeDecorator` instead of a bare `Set`, touching both
`PipelineGraph.tsx` and `LineagePanel.tsx`. Note for that follow-up: A-1's
`alertsAreComplete` completeness gate (fix-round-1) applies to PROCESS nodes
only — connection/collection nodes still report `ok` on an incomplete alert
list — the same fix should unify that gate across all three anchor kinds.
- Tracked in: `app/src/lib/monitoring/graph-decorate.ts`.
- Found in: A-1 Task 3 review, fix round 1.

### I-118 · Extractor processes: alerts never attribute to a product, and `processVerdict` reads "No trigger" over a firing alert 🟠
Seen on the A-1 live check (2026-09-14, standing GOES demo): the
`goes-abi-metadata` extractor had a firing `process_failed` alert. Two gaps,
both by construction rather than by bug: (1) `buildProductRows` wires
processes through `process_source` / `process_output` edges only, and an
extractor reaches its collection through the `extractor` edge kind, so the
alert lands in the home page's residual "not shown against a product" line
instead of on `goes-abi-mcmipc`. (2) `processVerdict` puts deployment state
first and an extractor has no `process_sources` by design, so `sourceCount
=== 0` yields "No trigger · no source attached" and the alert block below it
is never reached — the card shows a grey verdict while the graph paints the
node red. Fix shape: attribute through `extractor` edges in
`buildProductRows`, and let `ProcessesPage` pass `undefined` (not `0`) as
`sourceCount` for `kind === "extractor"` (it already special-cases the
trigger summary text) so the alert branch runs.
- Tracked in: `app/src/components/layout/overview.ts`,
  `app/src/components/processes/health.ts`, `ProcessesPage.tsx`.
- Found in: A-1 lead live check.

### I-119 · `/map`'s parked tick is positional, so a reshaping axis moves it 🟡
`MapPage` resolves the current tick as `axisIndex ?? last` into an axis that
is the union of every time-aware layer's frames (spec §4.4, V-3). Adding a
layer whose frames are older prepends ticks, so a user parked at index 12 is
suddenly looking at an older instant; removing the layer that contributed
the oldest ticks shifts it the other way. The reducer clamps a negative
index and the page clamps to the axis length, so nothing throws — the
position just drifts. Fix: hold the parked INSTANT in state and re-derive
the index whenever `axis` changes (`lib/map/state.ts` + `MapPage.tsx`).
- Tracked in: `app/src/components/map/MapPage.tsx`, `app/src/lib/map/state.ts`.
- Found in: V-3 Task 6 review (plan-mandated form).

### I-120 · The Add-layer popover fires two queries per listed product on every open 🟡
`AddLayerCollectionRow` (V-3) calls `useCollectionSettings` and a five-item
`useItems` probe unconditionally for each collection the popover lists, so a
catalog of N products costs 2N requests on the first open (cached after).
The probe cannot be skipped when serving is off because `useItems` exposes
no `enabled` flag. Cheap fix: give `useItems` an `enabled` option and gate
the probe on `settings?.servingEnabled === true`. The probe's `limit: 5`
also keys a separate cache entry from the layer's own `limit: frameSpan`
query, so adding the layer issues a fresh items request.
- Tracked in: `app/src/components/map/AddLayerCollectionRow.tsx`,
  `app/src/lib/query/items.ts`.
- Found in: V-3 Task 4 review.

### I-121 · The basemap lags the theme toggle by ~8–10 s on map pages 🟡
`StacMap` swaps `mapStyle` between the light and dark Carto styles on
`$theme`, which makes maplibre fetch the other style from
`basemaps.cartocdn.com` and rebuild the whole style; the app chrome switches
instantly, the map stays on the old basemap until the fetch completes
(measured ~8–10 s on the V-3 live check; every overlay layer survives the
rebuild). Not a V-3 regression — V-2 recorded it. Fix candidates: prefetch
both style JSONs at map mount so the swap is a local `setStyle`, or apply
the theme through `setStyle(..., { diff: true })` on a cached document.
- Tracked in: `packages/shared/src/components/map/StacMap.tsx`.
- Found in: V-2/V-3 lead live checks.

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
- **I-100** · `file_mtime` was the ledger settle time, not the object's modified time — 🟢 resolved (G-6: DISCOVER persists `source_mtime`; `file_mtime` prefers it)
