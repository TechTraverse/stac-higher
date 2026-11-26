# Outstanding issues

Known gaps, residual risk, and deferrals — tracked honestly so they aren't mistaken for "done." Status: 🔴 open · 🟡 accepted/mitigated · 🟢 resolved · ⚪ deferred-by-design.

Each entry: what it is, why it exists, and where it's tracked. **This file is the record of accepted limitations, deferrals and residual risk — not a backlog.** An entry gets a GitHub issue only when it becomes work someone should do; then its "Tracked in" line names the issue (`GitHub #N`) and the issue title carries the `(I-N)` code. Everything else stays here, ADR-consequences style. Close an entry by moving it to 🟢 with the resolving commit/PR; fully-closed entries move to [`ISSUES-ARCHIVE.md`](ISSUES-ARCHIVE.md), leaving a one-line stub in the list at the bottom so inbound references still land. Entries that keep an open or amber half stay here. **This file is not sorted by number** — sections are chronological, not numeric — so a new entry must take the next number above the file's actual MAXIMUM `I-N` (`grep -n '^### I-' docs/ISSUES.md`, sort numerically, take the top), never just the highest number visible near wherever you're inserting.

---

## Carried-forward work (committed, not yet done)

### I-1 · Per-collection read-visibility at the proxy 🔴
Phase 1 delivered authenticated transactions + audience validation, but **read-visibility filtering** (different groups see different collections) cannot be done with auth-proxy config alone — it needs OPA or a custom filter factory. Deferred out of Phase 1.
- Tracked in: [ADR 0002](decisions/0002-auth-proxy-enforcement.md); GitHub #34 (blocked on a lead design decision); Phase 1 note in [`../ROADMAP.md`](../ROADMAP.md).
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

**M3-D (2026-09-21, PR #22):** in-process concurrency is 12 across two queues (`default`
8 / `bytes` 4); the one non-atomic ledger leg S-C found (`ingest_files`
settled → fetching) is a compare-and-set; ITEMIZE's `flow_stats` bump is
batched. Multi-instance stays a deployment option, not a slice: the periodic
deferrer dedupes across processes and every claim leg is atomic (S-C's audit +
M3-D's shared-state table in the pipeline README "Concurrency"). One pipeline
process holds up to 40 Postgres connections, so a second replica needs
`max_connections` raised (README "Connections"). Leader election remains
Phase 8.

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
- Tracked in: ADR 0013 (accepted; cloud half superseded), ADR 0019, GitHub #17 (K-9 closes the cloud half), the K
  queue (GitHub epic #2, `queue: k8s compute (K)`).

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
this repo merged to a local `ai/main` rather than through a PR at the time:
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
serving exposure work (titiler-pgstac / tipg, FEATURES §Cross-phase — Pre-M5 hardening, ✅)
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

### I-81 · No cancel-run verb (runaway runs; OGC `dismiss` gap) 🟢
A `queued`/`running` process run could not be cancelled: the only recovery
verbs were the retry budget and Re-run (dead rows), so a mis-deployed
revision burned its timeout budget with only the executor's timeout as a
backstop, and the future OGC facade could not claim `dismiss` (ADR 0016 §3).
- **Resolved (K-4, migration 031):** `POST /api/processes/[id]/runs/[runId]/cancel`
  (operator+, audited `cancel`) flips a `queued` or retry-pending `failed`
  run to the new terminal `cancelled` status at once, and flags a `running`
  run (`cancel_requested_at` + `pg_notify('process_run_cancel')`); the
  pipeline stops it (10 s SIGTERM grace), keeps its log and lands it
  `cancelled`. Cancel wins even over a run that exits in the meantime (plan
  R3). A cancelled run can be re-run. Run rows show Cancel, *Cancelling…*
  and a `cancelled` badge. The facade can claim `dismiss` when it ships.
- Tracked in: `app/src/pages/api/processes/[id]/runs/[runId]/cancel.ts`,
  `services/pipeline/src/pipeline/process/reconcile.py`; GitHub #12.

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
access, and the public-URL stage (I-91) still buffer.
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
- Tracked in: GitHub #9 (X-5 live gates).

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
- Tracked in: GitHub #19 (the owed night-frame look; city lights stay out of scope).

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
- Tracked in: X-queue spec §2/§3.5; `tests/contract-fixtures/README.md`. GitHub #9 (X-5 gate B records the missing two).

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
K-6 configures that safety net (`infra/kueue/config/production.yaml`): an
admitted run whose pod is not ready in 10 minutes is evicted and requeued;
after three requeues Kueue deactivates it and the run dies (K5-R8). The kind
leg proves the path with a 30 s timeout.
- Tracked in: ADR 0019; spec §7.3; K-8 (limits = quota). Revisit when Kueue
  supports CapacityBuffer.

### I-93 · Run credentials vs. queue wait 🟡
Run-scoped STS credentials are minted before submit (they are in the run's
environment) and last `max(900, max_queue_wait + timeout + grace)`; a run
still pending with less than `timeout + grace` left is cancelled and
resubmitted with fresh ones, without spending an attempt and losing its
queue position (K-4; dormant on Docker, which never holds a submitted run).
Residual: in EKS the pipeline's own credentials come from Pod Identity /
IRSA, and `AssumeRole` from role credentials is **role chaining, capped at
one hour**. That bounds cloud profiles' `max_queue_wait_seconds +
timeout_seconds + grace` to 3600 s (`PROCESS_MAX_CREDENTIAL_SECONDS`)
unless the run role is assumed with web identity directly. Measure at K-9.
- Tracked in: spec §5.4; K-9. GitHub #17.

### I-94 · Run logs are captured at exit only ⚪
Both backends read the run's combined output once, at exit, capped at
`process_log_max_bytes`. A Kubernetes pod lost to node failure loses its
log entirely (`error = "log unavailable"`), and an hour-long GPU run has no
live tail for the operator watching it. A rolling capture (periodic
`pods/log` with `sinceTime`, appended to the `log_ref` object) is the
follow-on; not in the K queue.
- K-5: `pods/log` serves only the container's CURRENT log file, and the
  kubelet rotates at 10 MiB by default, so a run that prints more than that
  loses the head of its log on Kubernetes (Docker keeps it). A run Kueue
  preempts restarts in a fresh pod; the preempted execution's partial log is
  gone with its pod. A disrupted pod (node loss, eviction) is usually gone
  before the reconciler reads it: the requeued attempt records no log.
- Tracked in: spec §6.1; `process/kubernetes_executor.py` (`logs`); here.

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

### I-134 · Delivery reads each canonical object whole — the `bytes` queue bounds job count, not bytes 🟡
`delivery/worker.py` `_stream_canonical` reads the canonical object into
memory (`platform.get_object` → `Body.read()`), hashes it, then writes it
through the adapter; server-side copy avoids that only when the destination
shares the platform endpoint and the checksum algorithm is not sha256. M3-D
puts `pipeline.deliver` on the 4-slot `bytes` queue, which bounds *how many*
such jobs run, but the per-slot memory is one whole asset — so the M3-C
envelope's "independent of asset size" holds for FETCH and the located
EXTRACT read only (~1 GB above baseline at the 250 MB tier with 4 slots,
against ~480 MB by the formula). Found in the M3-D whole-branch review; M3-C
had logged the buffered residuals as SFTP/FTP-only (I-83) and process staging
(I-91). Streaming delivery through `StorageAdapter.open()` is M3-K (#38).
- Tracked in: #38; `services/pipeline/README.md` "Concurrency (M3-D)".

### I-135 · `flow_stats` rollups took `FOR UPDATE` on the association row that the ledger inserts already share-lock — 🟢 resolved (M3-L, #41)
`delivery_log.association_id` and `ingest_files.association_id` reference
`collection_connections(id)`, so an INSERT into either takes a `FOR KEY
SHARE` lock on the association row for the rest of its transaction. The
in-transaction `flow_stats` rollups then asked for `FOR UPDATE` on that row,
which conflicts with the *other* transaction's share lock: two concurrent
deliveries for one association deadlocked (`DeadlockDetected` after
`deadlock_timeout`, 1 s). Invisible at concurrency 1; on M3-D's first load run
at 4 concurrent deliveries 48 `deliver` jobs failed permanently and 190 more
succeeded only on retry. S-C's audit had marked the rollup "`SELECT … FOR
UPDATE` — safe under concurrency": safe from races, not from the lock upgrade.
Both sites now take `FOR NO KEY UPDATE`, which does not conflict with `FOR KEY
SHARE` (`flow_stats` is not a key column). The 140 deliveries those failed
jobs carried were lost — that gap is #42.
- Tracked in: GitHub #41 (fix), #42 (the loss).

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

### I-126 · `/map` deferred scope (map-page spec §8) ⚪
Logged when V-4 closed the V queue; none is a defect.
- Shareable URL state (layers, span, tick, camera).
- The STAC `renders` extension (band / rescale / colormap) instead of the tiler's defaults; a legend.
- A basemap picker.
- Whole-span footprints toggle; click-through on imagery to the item at that tick.
- A seeded demo vector table (e.g. GOES hotspot points) — today's tipg lists only PostGIS function collections, so the Vector tiles section has nothing real to show.
- Function collections that need parameters cannot draw: the V-4 live check added `public.st_hexagongrid` — TileJSON 200, every tile 422 `Missing Required parameters … size`, maplibre marks the source errored and stops requesting (nothing drawn, no error UI). Of tipg's six functions only `public.postgis_srs_all` serves a 200 tile (5 MB, no geometry) and `public.st_subdivide` 500s. A parameter UI (spec §4.5 lists none) or a real table is needed before the section shows anything.
- I-122–I-125 are reserved by the C-queue spec branch `docs/c-images-spec` (draft PR open, not yet on `main`) — do not reuse them.
- A pre-warmed mosaic cache for smooth first-pass playback (shared with I-108).
- Vector layers are not hoverable and contribute no ticks (spec §4.4/§4.6 as written).
- Tracked in: `app/src/components/map/`, `docs/superpowers/specs/2026-09-04-map-page-design.md` §8.

### I-127 · tipg's `Cache-Control: max-age=3600` keeps a stale vector-tile listing for an hour 🟡
tipg answers `/collections` (and the TileJSONs) with `Cache-Control: public,
max-age=3600` and no ETag. `fetchTipgCollections` uses the browser's default
cache mode, so a browser that listed the collections within the hour serves
the list from its HTTP cache when tipg is unreachable — the V-4 live check
saw the six-entry list with tipg stopped (0 bytes transferred), and the
spec's "no vector tiles published" line only with a cold cache (a different
top-level site partition). Adding a layer then fails quietly at tile time
(errored source, nothing drawn). Fix candidate: `cache: "no-store"` (or
`"no-cache"`) on the quiet tipg fetches in `app/src/lib/serving/queries.ts`
— one request per picker open, which `enabled: open` + `staleTime` already
bound. Same shape applies to `fetchItemTileJson`.
- Tracked in: `app/src/lib/serving/queries.ts`, `app/src/components/map/AddLayerPopover.tsx`.
- Found in: V-4 lead live check (2026-09-14).

### I-128 · `S3Adapter.copy_source` offers a server-side copy for anonymous connections too 🟡
`copy_source` returns `(bucket, key)` unconditionally, including for an
anonymous (public-bucket, e.g. NODD) connection whose bucket the platform's
own keys usually cannot read. M3-C's FETCH then pays one failed `CopyObject`
and a WARNING (`copy_fallback`) per member before streaming — correct, but
noisy at NOAA ingest rates. Fix candidate: return `None` when the adapter is
anonymous (the platform keys cannot sign for a foreign public bucket), or
gate on the connection's credentials in `transfer_policy`.
- Tracked in: `services/pipeline/src/pipeline/connections/adapters/s3.py`, `services/pipeline/src/pipeline/ingest/transfer.py`.
- Found in: M3-C Task 5 review (2026-09-14).

### I-129 · Copy-mode `raster_auto` EXTRACT through `/vsis3` is ~4.5× slower than the buffered read it replaced 🟢 resolved (narrowed)
The M3-C measurement (60 × 64 MB GeoTIFFs on local MinIO, `with_raster`
statistics) put `ingest_itemize` at 0.283 s mean against the 0.063 s baseline
while fetch went 0.359 → 0.067 s (server-side copy) and the items stayed
byte-identical. The likeliest cause: the pipeline sets none of the GDAL
vsicurl tuning titiler's compose block does (`GDAL_DISABLE_READDIR_ON_OPEN=
EMPTY_DIR`, `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES`, `VSI_CACHE=TRUE`,
`CPL_VSIL_CURL_CHUNK_SIZE`), so a statistics pass issues many small range
requests. Fix candidate: set those in the pipeline's compose environment (or
in `open_raster`'s `rasterio.Env`) and re-measure; the window also saw peak
RSS 386 MiB against a 206 MiB baseline, confounded by the re-seeded GOES
demo's catch-up in the same process — re-measure in a quiet window too.
- Tracked in: `services/pipeline/src/pipeline/ingest/raster_io.py`, `docker-compose.yml` (pipeline env).
- Found in: M3-C Task 7 measurement (2026-09-14).
- Status 2026-09-15: vsicurl tuning applied in `raster_io.py` (`VSICURL_TUNING`); re-measurement by the lead pending.
- Resolved 2026-09-15 (ea5e0b2 + the `m3c-vsi` measurement): itemize mean 0.283 → **0.094 s** (baseline 0.063 — the residual 1.5× is the range-read latency of a statistics pass over `/vsis3` against an in-memory read, accepted); RSS flat at ~212–245 MiB across 60 × 64 MB items (peak 270 from a 90 MiB start). Not tuned further: `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` also hides a pre-existing `.aux.xml` beside an object, so precomputed statistics no longer short-circuit the pass — the pipeline's sidecar strategy reads sidecars through the adapter, not GDAL's probe, so nothing depends on it.
### I-130 · Stored revisions with `memory_mb` above their profile's ceiling die at launch until redeployed 🟡
Neither schema ever had a `memory_mb` maximum; `standard.memory_mb.max` is now
16384 (`infra/hardware-profiles/local.json`), so a revision deployed before
K-1 with more memory becomes a `dead` run naming the bound
(`check_hardware_bounds`) on its next run; a deployment shipping a tighter
`standard` (or shrinking any profile later) widens the exposure.
Since K-2 the deploy form surfaces it: the form starts from the current
revision's hardware and memory, runs the write gate's own bounds check
(`hardwareFormIssue` in `lib/processes/hardware-public.ts`, the same code the
route calls), shows the bound inline on the offending input and keeps
**Deploy** disabled with that reason until the value fits. Residual, accepted:
nothing changes a STORED revision, so its runs keep dying at launch until
someone opens the process and redeploys, and nothing lists the over-bound
current revisions across processes (a one-off audit query is the fix
candidate if a deployment ever shrinks its profiles).
- Tracked in: `services/pipeline/src/pipeline/process/runner.py`, `app/src/lib/processes/hardware-public.ts`.
- Found in: K-1 final review (2026-09-15).

### I-132 · A permanently unreadable hardware-profile file requeues every run forever with a WARNING only 🟡
By ruling, an unreadable `PROCESS_HARDWARE_PROFILES_FILE` is an
infrastructure fault (requeue, no attempt spent) rather than a dead run;
there is no alert and no ceiling, so a mis-deployed file shows up as queued
runs that never start plus a log line. Fix candidate: an `asset`-style
monitor condition on repeated infrastructure requeues, or a startup
assert-and-warn on the file (the M3-A `use_queue` pattern).
- Tracked in: `services/pipeline/src/pipeline/process/runner.py`, `services/pipeline/src/pipeline/process/hardware.py`.
- Found in: K-1 Task 4/5 reviews (2026-09-15).

### I-170 · Docker capacity is counted across every worker, waiters have no strict order, and default-profile extractor runs have their own pool 🟡
K-3 admits a process or test run in the claim itself (per-profile advisory
lock; `executor_backend` is the admitted marker). A run whose profile is full
is not claimed: it stays `queued` as `pending_capacity` with
`next_attempt_at = now + 15 s` (spec §8). Three limits, accepted:
- **Latency.** Since K-4 a run leaving `running` (a verdict, a cancel, a
  requeue) wakes the oldest run waiting in the same admission pool at once
  (`wake_capacity_waiters` + `process_run_now`), so the wait after a slot
  frees is seconds. A run whose slot frees any other way (a pre-submit
  verdict, the stall sweep) is still picked up by the minute tick, and a
  newly triggered run's immediate claim can still take a freed slot before
  an older waiting run: there is no strict queue order among waiters.
- **Scope.** The count is the whole database's admitted runs on the profile,
  not one Docker host's: `capacity` means "per host" only while one host
  runs process jobs (the local and single-host shape). A host id on the
  claim would be needed for multi-host Docker. The cluster leg uses Kueue
  quotas instead (K-5/K-6).
- **Ruling: extractor runs on the default profile are exempt.** Spec §8 does
  not say how extractor runs (G-6, association-triggered) relate to
  capacity. The rule: an extractor run whose revision is on the default
  profile (`standard`; a revision without a `hardware` block, which is every
  built-in extractor, is `standard`) is not admitted against capacity, does
  not count toward it, and launches without `NanoCpus`. An extractor whose
  revision names ANY other profile is treated like a process run: it counts
  against that profile's capacity, waits when it is full, and gets
  `NanoCpus` (and `DeviceRequests` where the profile has them), so an
  operator cannot use an extractor to bypass a large or GPU profile's
  limits. The reason: before K-4 a pipeline process executed one process
  run at a time, and capping default-profile extractors at `standard`'s 1
  CPU, or counting them against its capacity of 2
  (`infra/hardware-profiles/local.json`), would slow each extraction or
  hold back tick-claimed batches. **K-4 replaced the worker bound** (runs
  are now submitted and reconciled, so the worker no longer serialises
  them) with a deployment-wide pool: exempt extractor runs are admitted in
  the claim under their own advisory lock, at most
  `PROCESS_EXTRACT_MAX_RUNNING` (default 4, Helm
  `processRuns.extractMaxRunning`) at once; past it they wait as
  `pending_capacity` like a run on a full profile (K-4 plan addendum A3).
  **Residual:** up to that many default-profile extractor containers run
  with no CPU cap at the same time, so a slow or hostile hand-written
  extractor on `standard` can take that many host CPUs' worth of contention
  until it times out; each is still memory-capped. Lower the setting on a
  small host.
- A run already `running` when migration 029 lands has no admitted marker
  and does not count, so a deploy can briefly admit past `capacity` until
  those runs finish.
- Tracked in: `services/pipeline/src/pipeline/process/repo.py` (`_admit_and_claim`, `EXTRACT_CAPACITY_LOCK`), `services/pipeline/src/pipeline/jobs/process.py`, `services/pipeline/src/pipeline/config.py` (`PROCESS_EXTRACT_MAX_RUNNING`).
- Found in: K-3 and its review (2026-11-25).

### I-171 · The socket proxy's `INFO=1` discloses daemon metadata to every service on the default network 🟡
K-3 enables the proxy's read-only `INFO` section so the executor can ask
`GET /info` whether the daemon has an NVIDIA runtime before it sends GPU
`DeviceRequests`, and how many CPUs the daemon has (`NCPU`). The same answer
carries the daemon's version, kernel, OS, CPU and memory totals, container
counts and registry configuration. **Who can reach it:** the proxy is not
published to the host, but it is on the default compose network, so every
service there can call `tcp://docker-socket-proxy:2375`, not only the
pipeline: database, api, auth-proxy, keycloak, minio, titiler, tipg,
cube-server and the one-shot init jobs. The larger pre-existing exposure is
that those same services can already list, create, start and remove
containers and pull images through the proxy (`CONTAINERS`, `IMAGES`,
`POST`). `INFO` adds read-only metadata on top of that. A compose test pins
the proxy's whole KEY=VALUE map. Fix candidate (lead, needs Docker to
verify): put the proxy on its own internal network shared only with the
pipeline. Alternative to `INFO`: drop the probe and rely on the daemon's
"could not select device driver" and "Range of CPUs" refusals, which the
executor maps to `HardwareUnavailable` anyway.
- K-4 (#202, first half): the image's default-on `EVENTS`, `PING` and
  `VERSION` are now written explicitly — `EVENTS=1` for the run watcher's
  read-only `GET /events`, `PING=0`, `VERSION=0` — and the compose test pins
  them. The dedicated network stays open in #202.
- Tracked in: `docker-compose.yml` (`docker-socket-proxy`), `services/pipeline/tests/test_compose_scanner.py`.
- Found in: K-3 (2026-11-25).

### I-172 · A crash between a run's verdict and its publish loses the publish 🟡
The Reconciler writes a run's verdict, its log reference and its source's
`flow_stats` in one transaction (exactly once), but enqueues
`process_finalize` (a `succeeded` run) and calls the extractor `on_dead`
hook (a `dead` extractor run) after that transaction commits. A worker
killed in that window leaves a `succeeded` row nobody publishes, or a dead
extractor's ingest rows `extracting` until the ingest stall sweep fails
them. Pre-existing (the old blocking path had the same window), now stated.
Fix candidates: a `finalize_enqueued_at` column the run tick sweeps, or an
outbox row written in the verdict transaction.
- Tracked in: `services/pipeline/src/pipeline/process/reconcile.py` (`_after_commit`).
- Found in: K-4 (2026-11-25).

### I-173 · A Docker run can overrun its timeout by up to a minute 🟡
Docker has no server-side deadline, so `DockerExecutor.status()` enforces
the run's timeout on read (the `stac-higher.timeout-seconds` label against
`StartedAt`) and kills a run found past it (K-4 plan R10). Reads happen on
every container event and on every run tick, so a run that emits no event
is killed at the first tick after its deadline: up to 60 s late. Run
credentials carry 300 s of grace, so the overrun cannot outlive them.
- Tracked in: `services/pipeline/src/pipeline/process/docker_executor.py` (`status`).
- Found in: K-4 (2026-11-25).

### I-174 · `process_runs_terminal_age_idx` does not cover `cancelled` 🟡
The 024 partial index is `WHERE status IN ('succeeded','dead')`. Nothing
reads it yet; migration 031 left it alone. The run-history retention leg
that will use it must include `cancelled` (a new index, or a predicate the
planner can match).
- Tracked in: `app/src/lib/db/migrate.ts` (024, 031).
- Found in: K-4 (2026-11-25).

### I-175 · A run whose container keeps disappearing requeues forever 🟡
A submitted run the backend reports `lost` (Docker: the container is gone)
goes back to `queued` without spending an attempt (K-4 plan R15, like
K-5's evicted pod), every time. Something that keeps deleting run
containers therefore loops the run at 60 s spacing with a WARNING each time
(`process run lost by the executor`) instead of exhausting its budget.
On Kubernetes the same holds for a node that keeps refusing the run's pod
(a pod `Failed` with a `status.reason` and no terminated container).
- Tracked in: `services/pipeline/src/pipeline/process/reconcile.py` (`_requeue_lost`).
- Found in: K-4 (2026-11-25).

### I-176 · The reaper's age ceiling counts the longest promised queue wait 🟡
The M3-W-1 reaper removes any run container or Job older than its age
ceiling, even one whose row is `running`. Since K-5 the ceiling is
`MAX_TIMEOUT_SECONDS + 600 (the Job's deadline slack) +
max(max_queue_wait_seconds) + 3600` over the deployment's profiles
(`reap_max_age_seconds`), on both backends, and a `running` row's launched
run under a handle other than the recorded one is reaped as a superseded
submission (a failed or crashed submit; K-5 review); an unreadable profile file falls
back to `MAX_TIMEOUT_SECONDS + 3600` and logs a WARNING. Residual: a Job
that waits in Kueue longer than every profile promises (a quota drought) can
be reaped while its row is still `running`; the reconciler then reads the
Job as gone and requeues the run without spending an attempt.
- Tracked in: `services/pipeline/src/pipeline/process/reaper.py`, `jobs/process.py` (`process_reap`).
- Found in: K-4 (2026-11-25).

### I-177 · The 031 status CHECK is not validated 🟡
Migration 031 re-adds `process_runs_status_check` `NOT VALID` to avoid a
table scan under the ACCESS EXCLUSIVE lock all pending migrations share
(029's precedent for the phase CHECK). Every write since is checked, and the
new list is a strict superset of the old, so no existing row can violate
it; but `pg_constraint.convalidated` is false. A later migration (or an
operator, off-peak) can run `ALTER TABLE stac_higher.process_runs VALIDATE
CONSTRAINT process_runs_status_check` (SHARE UPDATE EXCLUSIVE, no write
block) — the same applies to `process_runs_phase_check`.
- Tracked in: `app/src/lib/db/migrate.ts` (029, 031).
- Found in: K-4 (2026-11-25).

### I-178 · Nothing in the Kubernetes executor has met a real API server 🟡
K-5 is proven against a fake API server (`tests/_fake_kube.py`) that
enforces the pipeline's Role, speaks plain HTTP and emulates Kueue's
webhook. Unverified until K-6's kind leg (#14): TLS to the API server with
the mounted CA and a projected token; the Role under the real authorizer;
Kueue's webhook suspending our Jobs and the Workload's conditions and
labels; and two kubelet behaviours the executor relies on — that a pod
killed by `activeDeadlineSeconds` (the run's timeout, and `cancel()`, which
lowers it to 1 s) stays readable through `pods/log` until `reap`, and that
the kill follows the 10 s grace. If the log is lost, the fallback is to read
logs BEFORE the stop (K-4's cancel row) and replace
`KubernetesExecutor._stop_keeping_the_log` with a foreground Job delete.
- Tracked in: `services/pipeline/src/pipeline/process/kubernetes_executor.py`; GitHub #14.
- Found in: K-5 (2026-11-26).

### I-179 · On Kubernetes a run's duration includes its queue wait 🟡
K-4's `started_at` means "claimed", and on Kubernetes a run is claimed (and
`running`, phase *Waiting for capacity*) while Kueue holds it, so the UI's
run duration and `pipeline_process_run_seconds` include the queue wait and
node provisioning. Splitting them needs a `running_at` column (a migration);
not built.
- Tracked in: `services/pipeline/src/pipeline/process/reconcile.py` (`PROCESS_RUN_SECONDS`).
- Found in: K-5 (2026-11-26).

### I-180 · Run pods on Kubernetes: what differs from Docker 🟡
- **Read-only root.** A user image that writes into its own `WORKDIR` works
  on Docker (writable root) and fails on Kubernetes; only `/tmp` (and the
  platform images' working directory) is writable, memory-backed and counted
  against `memory_mb`. `HOME` is `/tmp` on a user image.
- **The API address is visible.** The kubelet injects
  `KUBERNETES_SERVICE_HOST/PORT` into every container (service links are off,
  so nothing else). The run can see the address; under the namespace's
  default-deny egress it cannot reach it unless a policy allows it, and it has
  no token to authenticate with.
- **No per-run PID limit** on either backend. A fork bomb is bounded only by
  the node's kubelet `podPidsLimit` (Docker: the daemon's defaults); R-8's
  NodeClass sets `podPidsLimit` (4096 by default); a cluster-owned NodeClass
  must set its own.
- **The timeout counts the image pull.** A pod's `activeDeadlineSeconds`
  runs from the pod's `startTime`, which precedes the pull, so a large image
  eats into the run's timeout; Docker counts from container start.
- **An orphan-deleted Job leaves its Secret.** A Job deleted with
  `propagationPolicy=Orphan` (by hand) keeps its env Secret, holding live
  run credentials until they expire; the executor always deletes in the
  foreground and deletes the Secrets explicitly on reap.
- **The scanner's Grype DB update cannot write** (`/opt/grype-db` is on the
  read-only root) and falls back to the baked database; #68 owns the
  scanner pod's update path.
- **CPU is always capped.** Requests equal limits, so default-profile
  extractor runs, uncapped on Docker (I-170), get their profile's CPU.
- Tracked in: `services/pipeline/src/pipeline/process/kubernetes_manifests.py`; #68.
- Found in: K-5 (2026-11-26).

### I-181 · Kubernetes start failures and waits cost time and attempts 🟡
- **An unpullable image** (`ErrImagePull`, `ImagePullBackOff`) or a pod
  whose config cannot be created is failed 5 minutes after the pod started
  (found by the run tick, so up to a minute later), spending an attempt per
  try; Docker fails it at submit. `InvalidImageName` fails at once.
- **A credential requeue goes to the back of the queue.** Each one cancels
  the pending Job and submits a new one, which Kueue queues behind every
  Workload created since (BestEffortFIFO). Under a deep backlog a run can
  requeue once per promised wait, each time an STS mint plus a Job create and
  delete, losing its position each time. The requeue fires only after the
  run has waited the queue wait its credentials were minted for, and at
  least 60 s, so a profile promising no wait cannot loop.
- **The credential requeue sees only waiting for capacity.** A pod that is
  scheduled but waits for its image is `starting`, so K-4's row 3 does not
  requeue it as its credentials run short; it spends the credential grace
  instead.
- **An inadmissible Workload** (a profile naming a LocalQueue that does not
  exist, a misconfigured flavor) pends with Kueue's message as the row's
  detail until K-4's credential requeue fires, then repeats — visible only
  in that detail.
- **No queue position.** Kueue's visibility API (`positionInLocalQueue`) is
  not read; the Role does not grant it.
- Tracked in: `services/pipeline/src/pipeline/process/kubernetes_executor.py` (`_job_state`).
- Found in: K-5 (2026-11-26).

### I-182 · Switching `PROCESS_EXECUTOR` strands runs in flight 🟡
A Reconciler only reconciles runs its own backend submitted
(`executor_backend`), so runs submitted on Docker are never collected by a
pipeline restarted with `PROCESS_EXECUTOR=kubernetes`, and vice versa. Drain
first: stop triggers and wait until no run is `running`. Not built. Related:
when the selected executor cannot be built (no API URL), the run tick returns
before its sweep, so already-submitted runs freeze where they are; the tick
logs one WARNING a minute and `/health` reports `executor.reachable: false`.
Two pipelines with different `PROCESS_EXECUTOR` values on one database are
not supported either: each reaper removes the other backend's runs, whose
recorded handle never matches what it launched.
- Tracked in: `services/pipeline/src/pipeline/process/reconcile.py`, `docs/processes.md`, the chart README.
- Found in: K-5 (2026-11-26).

### I-183 · On Kubernetes runs are `isolated` only, and user images skip the mirror 🟡
- **Network.** The executor refuses to start any run unless the runs
  namespace has the NetworkPolicy `stac-higher-runs-default-deny` (deny all
  ingress and egress to every run pod); R-8 renders it and the allow
  policies, selected by `stac-higher.run-kind` and
  `stac-higher.network-level`. The check proves the policy OBJECT exists
  with `podSelector: {}`, both policy types and no rules; it cannot prove
  the CNI enforces it (on EKS the VPC CNI / Auto Mode network policy
  enforcement is off unless enabled), and it cannot see a second, additive
  policy that allows more. Deleting or widening the policies opens egress
  for pods already running, whatever the executor's 60 s cache holds. R-8's
  drift test and the live checks (K-6's kind leg, R-11) carry those. It
  enforces only the `isolated` level: a run
  whose revision asks for `inputs`, `open` or a `hosts` list is refused
  (`dead`, naming the reason) rather than run with wider or narrower access,
  because NetworkPolicy has no FQDN egress (spec §6.2). Today
  `PROCESS_NETWORK_MAX` refuses those levels anyway; raising it on a cluster
  makes such runs die instead of running.
- **Images.** The registry mirror map rewrites only platform images; a
  user's own image is pulled from its own registry (a pull-through cache
  holds the deployment's upstream credentials). On a cluster whose nodes
  can reach only the mirror, user images cannot be pulled. This narrows the
  deployment spec §5's "the executor rewrites … every `container_images`
  row".
- **Start window.** Even with the policy in place, a new run pod may have
  open egress until the CNI enforces policy for it (I-188, GitHub #207).
- **R-8** renders the default-deny and the two allow policies, drift-tested
  against `infra/kubernetes/` and pinned by
  `tests/contract-fixtures/process-k8s-cluster.json`. The residuals above
  stand: CNI enforcement, and additive policies created outside the chart.
- Tracked in: `services/pipeline/src/pipeline/process/kubernetes_manifests.py`; GitHub #144.
- Found in: K-5 review (2026-11-26).

### I-184 · What a compromised pipeline identity can do in the runs namespace 🟡
The pipeline's Role has no direct read of a Secret, but `jobs create`,
`pods patch` and `secrets create` in the runs namespace are enough, for an
attacker holding the pipeline's token, to: run a Job under another
ServiceAccount in the namespace, mount or `envFrom` another run's Secret,
create ServiceAccount-token Secrets, relabel a pod into the `scanner`
egress policy, change a running pod's image (`pods patch`), or create a
Job without the Kueue queue label (bypassing quota). Bounds: the runs
namespace holds no Secrets but runs' own and no ServiceAccount with RBAC
(`infra/kubernetes/README.md`), and K-6's ValidatingAdmissionPolicy (#14)
admits on Job CREATE only `serviceAccountName: stac-higher-run`,
`automountServiceAccountToken: false`, `emptyDir` volumes, `envFrom` naming
only the Job's own Secret, image pull Secrets named `{job}-pull`, the queue
label and a known priority class; and on Pod UPDATE by the pipeline's
ServiceAccount only a lowered `spec.activeDeadlineSeconds`. Every Job the
executor builds is checked against those rules in
`tests/test_kubernetes_rbac.py`.

K-6 ships those rules as `infra/kueue/common/admission-policy.yaml` (two
ValidatingAdmissionPolicies on the runs namespace) and adds two on Job
CREATE: the pod template's `stac-higher.run-kind` / `stac-higher.network-level`
pair must be `(process, isolated)`, or `(image_scan, scanner)` for a
`stac-scan-` Job; and the template must repeat the Job's run-kind,
network-level and run-id labels. A pod's labels cannot be changed by the
pipeline afterwards (the pod-update rule). After review it also refuses, on
Job CREATE, any placement or host setting the executor never makes (a pinned
`nodeName`, a pod priority class, affinity, spread constraints, scheduling
gates, host namespaces, host aliases, DNS settings, …), containers whose
requests differ from their limits (Kueue counts requests), a Job or pod
without `activeDeadlineSeconds` or beyond the platform's maximum timeout (pod
86 400 s, Job 87 000 s), a termination grace above 30 s, a volume that is not
a memory-backed, sized `emptyDir` (node disk), probes and lifecycle hooks (a
kubelet request from the node, outside NetworkPolicy), and any Job with an
owner reference or that is not suspended on arrival (a Job controlled by a
Kueue-managed Job is not suspended by Kueue and runs outside the quota), and
any label or annotation in Kueue's domains (`kueue.x-k8s.io/`, its
subdomains) beyond the Job's queue-name and priority-class labels, on the Job
or its pod template (Kueue reads such keys to bind a Job to a prebuilt
Workload, change its admission or its accounting); and a
third policy admits only `Opaque` and `kubernetes.io/dockerconfigjson`
Secrets (no ServiceAccount-token Secret).
**Placement (R-8):** `nodeSelector`, `tolerations` and `runtimeClassName`,
which the executor copies from the hardware profile and Kueue mutates at
admission, are bound by the chart's fourth policy,
`stac-higher-runs-placement`: on Job CREATE the pod template carries exactly
one `processRuns` family's three fields, or none (an image scan). The chart
renders K-6's three policies from byte-identical copies and this one from
`processRuns.families` (a CEL literal, not a param resource: R-8 plan
addendum A-R3). Its CEL has run under cel-go only, not on an API server
(I-189). **Residual:** the policy narrows the shape, it does not
authenticate intent. A compromised pipeline identity
can still create a scan-shaped Job (`stac-scan-…`, `image_scan`, `scanner`)
running any image under the run ServiceAccount and get the scanner policy's
egress (DNS, the object store, 443). Closing that needs the scan's image
and arguments pinned at admission (the scanner image's digest) or scans in
a namespace of their own with their own identity; not built.
- Tracked in: `infra/kubernetes/pipeline-role.yaml`, `infra/kueue/common/admission-policy.yaml`; GitHub #14.
- Found in: K-5 review (2026-11-26).

### I-185 · What the kind leg cannot show 🟡
The kind CI leg (`.github/workflows/kind.yml`, K-6) runs the Kubernetes
executor against a real API server, kubelet, Kueue and CNI, but not:
- **GPUs**: flavors with GPU node labels, extended resources, the device
  plugin (no GPU on the runners; K-5's mapping unit tests and K-9's cluster
  gate cover them).
- **Karpenter**: NodePools and the quota-equals-limits rule (I-92; K-8/R-11).
- **The registry mirror** rewrite and the pull-through cache (R-8 #144).
- **The allow policies** for `isolated` runs and scans (R-8 renders them):
  kind proves the default-deny is enforced by kindnet, not that EKS's VPC CNI
  enforces policy, nor what an allow policy opens.
- **The in-cluster pipeline pod**: the tests run on the runner with a token
  file and the CA, not the projected mount and the `KUBERNETES_SERVICE_HOST`
  URL (K6-R4; R-11).
- **Image-scan Jobs** end to end (the scanner image is not built; its Job
  shape is admitted by a server dry run).
- **A real `410 Gone`** on a watch (the fake owns it; K6-R13).
- **Production `waitForPodsReady` timing** (kind uses 30 s; K-9 measures).
- **Kubernetes newer than 1.34** (one node image; a matrix is a one-line
  addition).
- Tracked in: `.github/workflows/kind.yml`; R-8 (#144), R-11, K-8 (#16), K-9.
- Found in: K-6 (2026-11-26).

### I-186 · Kueue's webhooks couple workloads in every namespace to Kueue 🟡
Kueue v0.20.8's release manifests register 36 of their 43 admission
webhooks with `failurePolicy: Fail` on every namespace except `kube-system`
and `kueue-system`, including for integrations the configuration disables
(they answer no-op): `mpod`/`vpod` (pods, CREATE and UPDATE: the executor's
cancel patch included), `mdeployment`/`vdeployment` and
`mstatefulset`/`vstatefulset` (CREATE and UPDATE), `mjob`/`vjob`, LeaderWorkerSet,
JobSet, Ray, Kubeflow, AppWrapper, Spark and TrainJob. As released, while
Kueue's webhook is down no pod, Deployment or StatefulSet anywhere outside
those two namespaces can be created, and no Deployment or StatefulSet rolled.
**Mitigation (K-6):** `scripts/kueue-narrow-webhooks.py` rewrites every
webhook that has a namespaceSelector to match only the runs namespace before
the manifests are applied; the seven without one (ClusterQueue,
ResourceFlavor, Cohort, Workload) match only Kueue's own API. Kueue does not
undo it: its only writer to webhook configurations, the cert-controller
rotator, sets `clientConfig.caBundle` alone. `scripts/check-kueue.sh` fails
when a Kueue release's webhooks differ from `infra/kueue/webhooks.txt`. The
kind leg installs Kueue this way, proves it still admits, preempts and
validates, and that with Kueue stopped a Deployment in `default` still
creates while a run Job is refused. **Residual:** the cluster installer (K-8)
must apply the same transform; a plain `kubectl apply` of Kueue's manifests
restores the coupling.
- Tracked in: `scripts/kueue-narrow-webhooks.py`, `infra/kueue/README.md`; GitHub #16 (K-8).
- Found in: K-6 (2026-11-26); widened in its review.

### I-187 · The kind workflow is path-filtered and not a required check 🟢
`.github/workflows/kind.yml` runs on changes to its paths and weekly
(Mondays). A skipped path-filtered workflow reports nothing, so it cannot be
a required status check; a change outside its paths that breaks the kind
leg is caught by the weekly run, not the PR (K6-R14).
- Tracked in: `.github/workflows/kind.yml`.
- Found in: K-6 (2026-11-26).

### I-188 · A new run pod has open egress for a moment at start 🟡
On a CNI whose NetworkPolicy enforcement is eventually consistent, a pod
starts with no policy applied and gets its rules only once the CNI's agent
has learned the pod's IP. A run that connects at once gets out despite the
runs namespace's default-deny. Measured on kind v0.33.0 (kindnetd with
kube-network-policies, which sends only known, policy-selected pod IPs to its
queue): IMDS, the API service and an internet address were all reachable at
t=0; the run's egress was last reachable at 0.26 s and first blocked at
0.51 s; everything was blocked by 15 s. A connection opened in the window is
not re-checked (kube-network-policies checks new connections only, unless its
strict mode is on). The AWS VPC CNI documents the same behaviour for its
`standard` enforcing mode ("containers in the new pod will start with a
default allow policy") and a default deny from the first packet in `strict`
mode. K-5's fail-closed check (I-183) proves the policy object exists, not
that a pod is covered from its first packet. Until #207 lands (a start gate
in the run pod, or strict enforcement as a deployment prerequisite), a
deployment must run its CNI in a strict enforcing mode
(`infra/kubernetes/README.md`). The kind leg records the window on every
run and fails if it exceeds 10 s.
- Tracked in: GitHub #207.
- Found in: K-6 kind leg (2026-11-26).

### I-133 · CodeQL and dependency review are skipped, not passing 🟡
Both need GitHub Advanced Security on a private repo, which this repo does not
have (`gh api repos/TechTraverse/stac-higher` → `security_and_analysis: null`),
so every CodeQL analyze job failed with "Advanced Security must be enabled for
this repository to use code scanning" and dependency review with "not supported
on this repository" — on every push since the workflows were added. They are now
gated behind a preflight job that asks the API, so they report **skipped**
rather than red. The practical gap: **no static analysis and no PR dependency
review actually runs.** `npm audit`, `pip-audit`, gitleaks and trivy do, and
they are the current coverage. Closes itself if the repo goes public (code
scanning is free there) or GHAS is enabled — the preflight flips with no edit.
The check queries the API rather than `github.event.repository.visibility`,
which is not populated on `schedule` runs.
- Tracked in: `.github/workflows/codeql.yml`, `.github/workflows/security.yml`.
- Found in: CI repair (2026-09-15).

## Container images + scanning (C queue, epic #56)

### I-122 · UI test runs are inserted and never drained 🔴
`POST /api/processes/[id]/test` INSERTs a `process_checks` row (the ADR
0004 bridge, M5-A) and the UI polls it, but no registered pipeline job has
ever called `claim_process_checks` / `attach_check_run` / `finish_check`
(`git log -S claim_process_checks -- services/pipeline/src/pipeline/jobs`
is empty; the only caller is the test fake). A test run from the UI stays
`pending` forever. Found while designing `image_scans` on the same
pattern: copy the drain half deliberately (spec §8.1). Fix is a `process_
check_drain` leg on the run tick that claims the row, calls
`process_trigger(is_test=True, revision_id=…)`, attaches the run and
finishes the check when the run finalizes — independent of the C queue
and small enough for any loop to pick up.
- Tracked in: GitHub #55.
- Found in: the 2026-09-13 planning session.

### I-123 · The scanner's egress network cannot filter by registry host 🟡
`scanner-egress` (spec §11) is a plain bridge network: Docker cannot
restrict it to `allowed_registries`. The list is enforced in software
twice (app on add, pipeline before launch) and the scanner is the only
container on the network, but a compromised scanner could reach any host.
Being non-internal and a plain bridge also means it is not egress-isolated
from the host: on a dev host the scanner can reach every port this compose
file publishes — Postgres 5433, the auth proxy 8081, the API 8082, the
pipeline 8083, titiler 8084, tipg 8085, Keycloak 8180, and MinIO 9000/9001.
The scanner's own registry client (`services/image-scanner/.../registry.py`)
follows a registry's `WWW-Authenticate: Bearer realm=...` challenge and a
blob redirect to any https host (stripping `Authorization` on a host
change) — that client has no allow-list of its own, so the network is the
only boundary. On Kubernetes the equivalent is a NetworkPolicy with FQDN
egress, which is CNI-dependent (K8s spec §12).

**Decision (2026-09-29, #61): accepted for the compose/dev stack.** A dev
stack runs on one developer's machine with throwaway data, and binding the
published ports to `127.0.0.1` is not a reliable fix on Docker Desktop
(containers reach the host through `host.docker.internal`; untested). Treat a dev
stack's Postgres credentials as disposable: never reuse them anywhere real,
and do not scan images you would not run on that machine.

**Production posture:** no deployment beyond a developer's machine may run
the scanner on this network. The fix is an egress proxy that allows only
the policy's registries (and their token and blob hosts) as the scanner's
only route out, plus default-deny NetworkPolicies, the cloud metadata
endpoint (`169.254.169.254`) blocked at pod and node level, and enforced
policy. It is built with the Kubernetes work.
- Tracked in: GitHub #68

### I-124 · A scan holds a worker slot while it runs 🟡
The drain launches the scanner through the executor and blocks its thread
until the scan exits, up to the policy timeout (900 s, spec §8.1). Since K-4
the scan no longer calls `wait`: it `submit`s and polls `status()` through
`run_to_exit` (K-4 plan R11), but it still occupies one worker slot for the
scan's duration. M3-D's concurrency (12) makes that one occupied slot, and
C-2 caps scans at `IMAGE_SCAN_CONCURRENCY` (default 1) deployment-wide,
each in a thread, so the event loop is never blocked. A large image's
first pull adds to the same held slot — `_ensure_image` blocks for however
long the daemon takes to pull it, and that time is not counted against the
scan's own timeout. Measured (C-5): admission scans took 82-109 s for
images up to ~1 GB, so the held slot is minutes, not the 900 s bound. Not a
reason to build a second executor shape (ADR 0021 option A). Fix: move the
scan verdict (`images/drain.py`) onto the Reconciler's submit-then-reconcile
path (the watcher already ignores `run-kind=image_scan` events).
- K-5: on Kubernetes a scan is a Job at Kueue priority `triggered` in the
  default LocalQueue, so an admission scan can wait behind a queue of
  triggered runs, and `run_to_exit`'s clock counts that wait and the pod's
  scheduling against the scan's policy timeout: a long queue can time a scan
  out before it runs. The same move to the Reconciler fixes both.
- Tracked in: `jobs/image_scans.py`, `images/scan_launch.py`; GitHub #203.

### I-125 · Anonymous Docker Hub pulls share a 100-per-6-hour budget per IP 🟡
Scans (the scanner's own registry reads, Syft's layer pulls included) and
launch pulls (the daemon's) of `docker.io` references without a group
credential pull anonymously unless `REGISTRY_DOCKERHUB_USER/_TOKEN` is set
(spec §5). Docker's enforced limit is 100 pulls / 6 h per IPv4 (200 with a
Personal account, unlimited for paid orgs); a NAT'd cluster shares one
budget. HEAD requests (tag→digest, drift) are free. Deployment checklist
item, not a code change; in GovCloud the ECR pull-through cache (spec
§12) removes it.
C-5 pulled 3 docker.io images anonymously (scanner registry reads) without
hitting the limit; unchanged.
- Tracked in: `services/pipeline/README.md` env table (C-2).

### I-136 · The registry check's Bearer token realm is untrusted 🟡
`check_registry` (`pipeline/connections/registry.py`) reads the token
endpoint from the registry's own `WWW-Authenticate: Bearer realm=...`
challenge; a hostile registry can name any host on `EGRESS_ALLOW_HOSTS`
(compose-internal services, say) instead of its own host, and the probe
sends the connection's own Basic credentials there. `resolve_pinned` still
enforces the allow-list and HTTPS, so the registry cannot reach an arbitrary
host, only one already on the allow-list, and only the response status code
comes back to the caller — no body, no header is relayed. Same posture as
every other adapter in this codebase: the egress allow-list is the trust
boundary, not the far end's identity. Possible hardening: accept a realm
host on the allow-list only when it equals the registry's own host.

### I-137 · Pulled user images are never removed from the Docker daemon 🟡
C-2 pulls a user image by digest the first time a run needs it
(`DockerExecutor._ensure_image`) and nothing removes it afterwards: a
revoked or superseded image's layers stay on the host until an operator
prunes them. The socket proxy's `IMAGES=1` would allow a delete, but deleting
from the daemon needs a reference count across in-flight runs and every
revision still pinning the digest, which is C-4's retention territory (spec
§8.3) and K-4's reconcile loop in cloud (the kubelet's own image GC applies
on Kubernetes). Deployment-checklist item for compose hosts: `docker image
prune` on a schedule, never while a run is starting. C-4 considered and deferred it: a
safe daemon delete needs a reference count across in-flight launches and
every revision still pinning the digest, and a delete racing a launch's
`_ensure_image` would fail that run. K-4's reconcile loop (and the kubelet's
own image GC on Kubernetes) is the right home.
- Found in: the C-2 plan (2026-09-27).

### I-138 · The launch-time image check has two TOCTOU gaps 🟡
`resolve_run_image` (§8.4) re-checks the `container_images` row at launch,
but the check and the pull/create are not atomic. (a) `_ensure_image`
inspects the daemon by digest first and skips the registry entirely when
that digest is already present (§8.4's designed fast path) — a credential
revoked after the first pull does not stop a second run from launching a
cached digest, because the registry is never contacted again. (b) even on a
cold pull, a revoke landing between `resolve_run_image`'s DB read and the
executor's `POST /containers/create` still launches: nothing re-reads the
row between them. Both are ordinary TOCTOU gaps between a read and a later
effect, not a missing check — the deploy-time write gate and the launch
re-check are deliberately two separate points in time (§3, "the pipeline
re-checks at launch"). Accept for C-2; a tighter bound needs either a
launch-time row lock or a short-TTL re-check immediately before create.
(c) A registry credential's connection is re-groupable (`group_id` is
editable): moving it to another group does not stop it serving pulls for
already-deployed runs of the OLD group, because `resolve_registry_auth`
resolves the credential by `registry_connection_id` alone and never
re-checks its group at launch. This is the same precedent the deploy-time
`secret_ref` gate already sets (`findUnresolvableSecretRef` checks the
group at deploy; the launch path does not re-check it), not a new gap.
- Found in: the C-2 plan (2026-09-27).

### I-139 · A launch pulls the multi-arch index digest, not the platform manifest that was scanned 🟡
`container_images.digest` is the manifest **or index** digest the tag
resolved to at add time (§4.1); the scanner always scans one platform's
manifest (`platform_digest`, from the policy's `platform`, default
`linux/amd64`) and that is what the verdict covers. The launch path pulls
`{reference}@{digest}` (§8.4) — the index digest for a multi-arch image —
so a non-`amd64` daemon (an arm64 dev machine, say) resolves and runs
whichever platform manifest the index and the local daemon agree on, which
was never scanned or evaluated. Compose dev hosts on Apple Silicon are the
practical case today; production stays on the policy's declared platform.
- Found in: the C-2 plan (2026-09-27).

### I-140 · The rescan diff compares summaries, not full findings 🟡
`image_scans.result.diff` (C-4, spec §8.2) is computed over the two stored
summaries (`top`, the ≤ 25 findings chosen policy-first, plus the complete
`kev` list). A finding that drops below the top-25 cut reads as `resolved`,
and one that rises into it as `new`, although the scanner saw it both
times. `counts_delta` comes from the complete counts and is exact. The
alert's message quotes `new`/`new_kev` counts, so on a very noisy image it
can overstate churn; the verdict (which drives `flagged`) is unaffected.
Exact diffs need both full Grype JSON files (large, untrusted,
object-stored).
- Found in: the C-4 plan (2026-09-27).

### I-141 · A flagged image that gets worse does not re-notify 🟡
`process_image_flagged` is a state-observed condition (C-4): it fires once
when the image goes flagged (or revoked, or stale) and stays open. A later
rescan that adds CRITICALs or a KEV to an image that is already flagged
updates the open alert's message (`last_seen` bump), but ADR 0010 notifies
only on a new alert row, so webhooks hear nothing. The dashboard's scan
history shows each rescan's diff. A resolve-and-re-raise on a worsening
diff would re-notify, at the cost of alert churn.
- Found in: the C-4 plan (2026-09-27).

### I-142 · `http-cache-semantics` cross-user disclosure advisory has no fix 🟢 (resolved 2026-10-07)
[GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp)
(high, published 2026-09-18) covers every `http-cache-semantics` release
through 4.2.0, the latest; no patched version exists. It arrives only through
`astro` (7.3.5 still depends on `^4.2.0`), whose one use is the build-time
cache for remote images (`astro/dist/assets/build/remote.js`). The app uses
no `astro:assets` remote images and runs no shared HTTP cache, so the
max-stale cross-user disclosure has no path here. The npm-audit gate accepts
the GHSA in `.npm-audit-allow.json` (`scripts/npm-audit-gate.mjs`). When a
fixed release lands, the gate warns that the entry is stale. Remove it then.
- Found in: the Security workflow going red on main (2026-10-03).
- Resolved 2026-10-07: `http-cache-semantics` 4.3.0 shipped the fix; the lockfile
  takes it and the `.npm-audit-allow.json` entry is gone.

## Virtual cube (Z queue, epic #83)

### I-143 · A cube repository's Icechunk bookkeeping grows without bound 🟡
Every commit to a rolling Icechunk cube leaves two objects that expiry plus garbage collection never remove:
- a **transaction log** (`transactions/`, about 17 KB once the window trims, because each `shift_array` records every moved chunk reference);
- a **backup copy of the `repo` object** (`overwritten/`, written before each update).

Snapshots, manifests and inline chunks stay flat. Measured in the Z-1 soak (288 commits, 72-step window, hourly expiry + GC): after every GC, `transaction_logs_deleted: 0` and the `overwritten/` count rose by one per commit. The repo grew from 0.25 MB to 6.3 MB over 20 h.

The backup copies are by design (the Icechunk spec keeps them for recovery and as the operations-log chain), and their size tracks the `repo` object's. Setting `num_updates_per_repo_info_file = 100` (default 1,000) keeps that object near 10 KB instead of ~75 KB. With it, the projected growth is about **8 MB per day per cube** (~3 GB a year at a 5-minute cadence): ~5 MB of transaction logs and ~3 MB of backups. That is small next to copying the data (~1.1 GB a day for CMIPC C13).

Whether Icechunk *intends* transaction logs to outlive expired snapshots is unknown. The spec says they are not needed to read data and mentions `pruned_ancestor_tx_logs` on expiry, but 2.2.2 exposes no cleanup. Accepted for v1 (decided 2026-10-04): the sink sets the history limit to 100, and `cube_maintain` records per-kind object counts and bytes and warns above a threshold.

Not done, deliberately:
- **No upstream question or issue yet.** Ask Icechunk first, before building anything; the answer decides between waiting for an upstream cleanup and a platform-side fix.
- **No deleting of Icechunk internals ourselves.** The object layout is format-private.
- **No periodic rebuild** of the repository from its current window. That is the fallback if upstream says "working as intended": it bounds everything using only the public API, at the cost of re-parsing the window's headers and swapping the repo under the cube server.

Revisit when any of these happens:
- a cube's repository passes ~1 GB;
- the platform runs more than a handful of cubes, or cubes on a faster cadence;
- an Icechunk release mentions transaction-log or `overwritten/` cleanup.
- Tracked in: [Z-1 results §4](research/2026-10-03-virtual-cube-spike.md#4-soak); [ADR 0022](decisions/0022-virtual-cube-sink.md) (the GC exception it scopes).

### I-144 · A double run during a cube's first commit can mislabel ledger rows 🟡

While a sink has no recorded snapshot, its repository is provisional (Z-4): a
job that finds unrecorded data resets `main` to the root snapshot before
writing. The job reads the sink again just before that write, so it never
resets a snapshot another run has already recorded. A second `cube_append`
still overlaps the first when a stalled-job requeue starts it while the first
is committing (a job blocking its event loop > 300 s, #90). If the second run
re-reads the sink after the first committed but before it recorded, it resets
the first run's snapshot and writes its own:
- **Usually only labels are wrong.** Both runs claimed the same oldest pending
  rows, so the second run rewrites every step of the first. The cube's data and
  `cube_sinks.last_snapshot_id` end correct; the first run's rows are finished
  `appended` with a snapshot id the reset orphaned.
- **One narrower window loses data.** If older rows were inserted between the
  two claims (late-arriving items), they can push some of the first run's rows
  out of the second run's 50. Those rows are finished `appended` by the first
  run, but the second run's reset dropped their steps, so the cube does not
  hold them. Nothing re-appends them. The window needs a stalled-job requeue,
  during the sink's very first commit, with a full batch and an older item
  landing between the two claims.

Related:
- After the first commit, `record_commit` is unconditional. In a double run,
  the slower recorder can move `cube_sinks.last_snapshot_id` back to an older
  snapshot until the next commit records the tip.
- Rows appended and trimmed in the same commit read `appended` with a
  snapshot that no longer holds them. After crash recovery, such rows read
  `late`.
- A sink deleted while its repository is provisional (no recorded snapshot)
  leaves the repository in storage until the cube collection is deleted
  (`asset_gc`).
- The app's `first_append_pending` refusal (PUT/PATCH `/api/collections/[id]/cube-sink`,
  #168) is check-then-write: a pending row the dispatcher inserts between the
  check and the guarded write lets one app write through, which bumps
  `updated_at` and costs a redo of the first batch (no data loss). Accepted by
  the lead on PR #173.

Accepted for v1; revisit if the ledger's `snapshot_id` ever drives a reader, or
if a requeued `cube_append` is ever seen during a sink's first commit.

### I-145 · A NODD outage longer than a row's retry budget leaves a gap in the cube 🟡

`cube_append` does not fail a row when a source read fails in transit (NODD
5xx or throttling, timeouts, refused connections, DNS:
`cubes/resolve.py::is_transport_error`; the lead's decision on PR #101). The
first such error stops the batch: no further row is resolved or read, and
reads already in flight (at most 4) finish. The job then ends before writing
anything, sets the sink's `last_error` and returns without raising, so
Procrastinate does not retry it (each queue retry would be another take and
another attempt within 90 s). The 5-minute `cube_kick` re-enqueues the sink
while rows are pending, which paces the retries. Only the oldest row that
failed in transit keeps its attempt; every other row is given back. The next
take works that row alone, so a file NODD never serves cannot be retried
forever: it ends `failed: crash_loop` after 6 reads. An outage that clears
costs one isolated run for that row, then the batch appends as usual, with no
gap.

The cost: an outage longer than the oldest row's budget fails rows
`crash_loop` **one at a time, oldest first**. Derived from the schedule (one
take per kick; the 7th take fails the row unread), the budget is about
**25–30 minutes per row**:
- **Reads that fail fast** (obstore gives up on a 503 after ~5 s of its own
  retries, ~4 s on a refused connection, measured locally): one run takes
  seconds.
- **Reads that time out**: obstore retries one read for up to 3 minutes by
  default, so a run takes about 3 minutes (the first wave of 4 reads times out
  together, and nothing more is started). That still fits between kicks, so
  the budget is the same, but each such run holds the sink's lock and a worker
  for those 3 minutes.
- After a `crash_loop`, the job re-enqueues itself and the next oldest row
  starts its own budget at once.
- New items for the source collection wake the sink between kicks, and each
  wake-up is another take, so a busy collection spends a budget faster.

The most visible cost is a stall, not the gap: while a row that keeps failing
in transit is the oldest pending row, isolation works it alone and newer steps
wait, so the cube stops advancing for that row's budget. Several bad keys
stall it in sequence.

A deterministic `GenericError` takes the same path: obstore reports, for
example, a 400 or a 301 from a misconfigured endpoint as a `GenericError`,
indistinguishable here from a 5xx. Its rows end `crash_loop` one at a time,
and the real error appears only in `cube_sinks.last_error`; the ledger says
`crash_loop`.

A gap is a missing time step only. The `t` axis holds only the times that were
appended, so the other steps, tiles and EDR series read normally, and the gap
rolls off with the window. Nothing re-appends a `crash_loop` row.

A per-sink choice (`on_source_error: retry | skip`) is not offered: it would be
a change to the cross-runtime sink config (`cube-sink-config` fixture, Zod and
Python). Accepted for v1; revisit if gaps after NODD incidents matter to users,
or if the cube needs a backfill path.

### I-146 · A maintenance run that kills its worker stalls the sink's appends 🟡

`pipeline.cube_maintain_sink` holds the sink's `lock` (`cube:{id}`), the same
lock as `cube_append` (ADR 0023). Unlike the append, it has no crash-loop guard
of its own: the append's guard is its ledger (`cube_appends.attempts`), and
maintenance writes no ledger row. If a run deterministically kills its worker
(for example, GC running out of memory on a very large repository):
- the dead job keeps the lock until its heartbeat is `STALLED_WORKER_SECONDS`
  (300 s) old;
- the next 5-minute `cube_kick` requeues it (`retry_stalled`), and a requeued
  job keeps its place ahead of the waiting append;
- after `MAX_STALLED_ATTEMPTS` (3) requeues, it is closed `failed`, and the
  appends run;
- the next hour's tick starts a fresh job, and the cycle repeats.

The cost: the sink's appends wait up to about 20–40 minutes each hour, and then
catch up. No step is lost (the rows stay `pending`), and nothing is deleted
twice. The sink's `last_maintained_at` stops advancing, and `last_maintenance`
keeps the last run that finished, so a stuck sink shows up as stale
maintenance, not as a failure.

Accepted for v1: the 1 GiB `repo_size` attention fires long before GC needs
that much memory on a 100-snapshot repository. Revisit if a sink ever
crash-loops: either let `retry_stalled` close a stalled maintenance job
`failed` instead of requeueing it (it has no retry anyway; the next hour
retries), or record a started marker in `last_maintenance` and skip GC after
repeated unfinished runs.

---

### I-147 · A source file that kills its worker is isolated only after every batch ahead of it has died once 🟡

Crash recovery (#127) isolates one row per crash: after a worker dies
mid-batch, the next `cube_append` works the oldest row alone and forgives the
rest, so the batch returns to normal after one isolated take instead of one
job per row (which cost a 50-row backlog about five minutes of serial jobs).
The price is paid by the rare row that deterministically kills its worker (a
header that segfaults the parser, say): sitting `k` places behind the oldest
pending row, it dies with its batch `k` times, each death followed by the
5–10 minute lock hold until the next `cube_kick` requeues the job, before it
is the oldest row and crash-loops alone (`failed: crash_loop` after
`MAX_ROW_ATTEMPTS`). An exception, as opposed to a worker death, never pays
this: it gives the attempts back (I-145 governs source errors). A row older
than the crashed batch arriving after the crash costs one extra death before
the isolation triggers.

Accepted for v1: a worker-killing file has not been seen on CMIPC, and the
common case (a restart or OOM during a batch) is now cheap. Revisit if Z-9's
kill-the-worker check or a live sink ever shows a poison row: a marker column
set on the take, or a bisecting isolation, would pinpoint it in `log k`
deaths.

### I-162 · A full store outage on `edr/position` is a 200 with every step null 🟡

`position` turns every `icechunk.StorageError` into a null step. A missing
source file and an unreachable store raise the same error, so when every step
of a series fails the response is a 200 whose values are all null and whose
`x-cube-missing-steps` equals the step count. A client that does not read the
header cannot tell an outage from a series of deleted files. Returning a 5xx
when all steps fail would be wrong for a series whose every source is really
gone, and telling the two apart needs a probe of the store (a design change to
spec §8.4).

Accepted for v1: the header carries the signal and the cube-server logs each
failed step. Revisit with a store probe in the fallback path if a client
mistakes an outage for empty data.

### I-163 · A tile whose source file is gone is a 500 🟡

xpublish-tiles loads the chunks inside an `asyncio.TaskGroup`, so a missing
virtual-chunk source (`icechunk.StorageError`) surfaces as an
`ExceptionGroup` that its own handler turns into a generic
`500 {"detail":"Internal server error."}` before the app sees it. The
response is `no-store`, so it is retried, but a client cannot tell a trimmed
or deleted source from a server fault. A 404/410 would need either a handler
that unwraps the group in xpublish-tiles' route (a patch of the dependency) or
a pre-check of the step's source from the tile path, which is a design change.

Accepted for v1: the pipeline trims a source only with its step, and the
server reloads within one poll.

## Deployment artifacts (R queue, epic #136)

### I-148 · The proxy's client_id audience mode rides on upstream internals 🟡

Upstream stac-auth-proxy (through v1.4.0) matches the `aud` claim only. To
support `AUTH_BEARER_AUDIENCE_CLAIM=client_id` (Cognito access tokens), the
derived image swaps `EnforceAuthMiddleware` in `app.user_middleware`. The swap
is guarded by a startup `RuntimeError` when upstream no longer registers
exactly one such entry, and by the lockstep pin test
(`test_dev_pin_matches_the_dockerfile_base`). It goes away if upstream gains a
claim setting, which is worth proposing upstream. Tokens carrying `aud` are
refused in `client_id` mode.

Tracked in: `services/proxy-policy/src/stac_higher_proxy_policy/app.py`; ADR
0015 bump procedure.

### I-149 · Credential budget is checked at deploy only 🟡

A revision deployed before `PROCESS_MAX_CREDENTIAL_SECONDS` was set, or
before its hardware profile's `max_queue_wait_seconds` grew, is not
re-checked. The pipeline's mint refusal (R-2) is the backstop and fails the
run naming the cap. Since K-4 the mint counts the same sum as the deploy
gate (`max_queue_wait + timeout + grace`), so the two agree on every case of
`process-credential-budget.json`; the remaining gap is only a cap or profile
change after deploy, which the mint refuses (never clamps, K-4 plan R7).

### I-150 · Generated platform secrets sit in Terraform state 🟡

The `secrets` module's `random_*` values are stored in state in plaintext.
Write-only arguments (`secret_string_wo`) would avoid that but need
OpenTofu/Terraform >= 1.11, above the modules' 1.10 floor.

Mitigation: the deployment's state backend must be encrypted and
access-controlled. Revisit when the floor moves.

### I-151 · Run credentials carry no KMS permission 🟡

`session_policy()` lists S3 actions only. On the SSE-KMS bucket, a run's PUT
and GET then depend on the key policy's `Principal "*"` + `kms:ViaService`
statement applying to the session principal past the session policy. AWS
documents that only for policies naming the session ARN. R-11 settles it with
one PUT under run credentials. If it is denied, `session_policy()` gains
`kms:GenerateDataKey`/`kms:Decrypt` on the platform key (with #158). The run
role already grants both.

Tracked in: GitHub #158 (the session-policy change) and GitHub #147 (R-11's first-deploy rehearsal settles it).

### I-152 · The pipeline's STS client reuses the S3 endpoint 🟡

`build_sts_client` passes `STAGING_S3_ENDPOINT` as the STS endpoint, so on AWS
that variable must stay blank (botocore's defaults). Set to the regional S3
URL, every run fails `RunCredentialsError`. The chart (R-6) leaves it unset on
AWS. A separate STS endpoint setting is the fix if a deployment ever needs one.

### I-153 · Cognito residuals 🟡

- Cognito's discovery document has no `end_session_endpoint`, so
  `/api/auth/logout` ends only the app session.
- `cognito:groups` feeds both `groups` and `roles`, so role groups
  (`stac-operators`) also appear as data groups.
- Access tokens carry no `email`, so identities show `username`. In an
  email-as-username pool Cognito's `username` claim is the sub UUID, so bearer
  identities display a UUID.

### I-154 · The demo-destination key is a long-lived IAM user key in state 🟡

Accepted for the test environment (spec §7.1, lead decision 2026-11-02). It is
the user-credential path by design, not the platform's own access. Destroy the
module with the environment.

### I-155 · The Pod Identity trust is not scoped to the cluster 🟡

The roles trust `pods.eks.<dns_suffix>` without `aws:SourceArn`. Any
association in the account may bind them, and creating one needs
`iam:PassRole`. Adding the cluster-ARN condition is a hardening step to verify
at R-11.

Tracked in: GitHub #147.

### I-156 · Hook-scoped role Secrets are recreated each hook phase; the master's is removed on success 🟡

The `<fullname>-hook-db-master` ExternalSecret carries `hook-succeeded`
(Argo: `HookSucceeded`), `refreshInterval: "0"` and `creationPolicy: Owner`, so
the RDS master Secret is deleted once the hook phase succeeds. On Helm that
happens only after every hook in the phase, so db-bootstrap (weight -20) still
reads it. On Argo CD the `HookSucceeded` timing for a non-Job hook is
unverified until a live check; if Argo deleted it before db-bootstrap, the
bootstrap would sit in `CreateContainerConfigError` until
`activeDeadlineSeconds` expires, rather than fail at once (and would not leak).

The five role hook ExternalSecrets keep `before-hook-creation` only. With
`creationPolicy: Owner`, each upgrade or sync deletes the ExternalSecret and
Kubernetes GC deletes its Secret, so they are recreated on every hook phase
and do not persist. Helm does not wait for ExternalSecret readiness, so there
is a short window, and a stale-password risk during rotation. R-6's runtime
pods reference only runtime Secrets (`<fullname>-db-owner`, `-platform`),
never hook copies; R-7 must use runtime copies or an explicit allowlist.

The role passwords reach Postgres only as client-side SCRAM-SHA-256 verifiers
(`files/db/run_sql.py`), never as plaintext, but a verifier can still be
logged under `log_statement=all`.

Follow-up idea: the bootstrap reads the master from Secrets Manager with its
own workload identity, so no master copy enters the cluster.

### I-157 · The role chain is proven on a superuser Postgres only 🟡

The pytest bootstrap runs as a superuser. Three things are first exercised on
RDS (master is not a superuser): pypgstac migrating as the non-superuser owner,
the owner granting the `pgstac_*` roles it created (PG16 `CREATEROLE` rules),
and the search_path. The owner's `CREATEROLE` is mandated by spec §7.2
(pypgstac creates the `pgstac_*` roles); on PG16+ it is bounded to roles the
owner created or administers. The bootstrap refuses a role name equal to
`current_user` or shared by two keys.

Tracked in: GitHub #147.

### I-158 · `/api/internal` is blocked at the Ingress by rule order 🟡

The block depends on the controller evaluating `/api/internal` before `/`
(listed first; longest-prefix on nginx). The `Prefix` match is path-literal:
encoded or case variants are not normalised by every controller (the ALB is
case-sensitive), so the block is defence in depth. The route itself requires
`x-internal-token` (`app/src/pages/api/internal/`) and fails closed without
`INTERNAL_API_TOKEN`.

### I-159 · `PROXY_AUTH_TOKEN` is generated but not wired 🟡

Terraform generates it and the chart accepts `secrets.arns.proxy_auth_token`,
but the chart does not set it: it would make `/api/proxy` require
`X-Proxy-Auth`, and no client sends that header, which would break browsing
external catalogs.

### I-160 · A cluster cannot run a process until it meets the runs-plane prerequisites 🟡

The pipeline half is K-5: `PROCESS_EXECUTOR=kubernetes` (set by the chart)
selects the Kubernetes executor, and the chart passes
`PROCESS_K8S_NAMESPACE` and `PROCESS_K8S_REGISTRY_MIRRORS`. R-8 renders
every object a run needs: the runs namespace, the run ServiceAccount, the
pipeline's Role and RoleBinding, the runs NetworkPolicies including
`stac-higher-runs-default-deny`, Kueue's LocalQueue, ClusterQueue, flavors
and priority classes, the admission policies and the runs NodePools. A run
still cannot start until the chart is installed on a cluster that meets the
prerequisites in the chart README (Kueue installed and configured,
NetworkPolicy enforced, the runs nodes' inputs). Until then every run
requeues without spending an attempt and the run tick logs why once a
minute. R-11 proves it live (I-189).

Tracked in: GitHub #144.

### I-161 · Read-only root filesystems and the open app port are render-verified defaults 🟡

Both are verified by rendering only, not on a cluster. Escape hatches:
`app.readOnlyRootFilesystem`, `pipeline.readOnlyRootFilesystem`, and
`networkPolicy.appIngressFrom`, which should be set to the VPC CIDR for ALB IP
targets.

### I-164 · The chart is not signed or attested, and its version tag can be re-pushed 🟡

The images carry buildx provenance and an SBOM. The chart package carries
neither, and nothing is cosign-signed (spec §12). The release manifest
records the pushed chart's digest (`chart.digest`), but an OCI version tag
can be pushed again: re-running a tag's Containers workflow overwrites
`charts/stac-higher:X.Y.Z`, as it does the image tags. A deployment that
needs immutability checks that the version still resolves to `chart.digest`
before installing (`docs/releases.md` "Consuming a release"), or pins the
digest (Flux `ref.digest`).

`release.yml` (the GitHub release with the app and pipeline assets) runs
independently of the Containers order, so a release can show those assets
while its images or chart failed. Nothing deployable is published until
`release-manifest.json` is attached; `docs/releases.md` "When a release
half-fails" covers cleanup.

Follow-up idea: cosign-sign the chart and the images, and verify on pull.

### I-165 · The catalog plane's database TLS and runtime users are render-verified only 🟡

R-7 runs stac-fastapi, tipg, titiler, the proxy policy and the cube-server
against RDS with `PGSSLMODE=verify-full` and `PGSSLROOTCERT`. libpq reads both
(titiler, proxy policy, cube-server); stac-fastapi and tipg use asyncpg, which
reads the same variables, but no test has connected either through TLS. tipg's
image runs as root, so the chart runs it as uid 65534 on a read-only root
filesystem; the other uids are the images' own users (read from GHCR). All of
this is first exercised on a cluster in the deploy rehearsal.

Tracked in: GitHub #147.

### I-166 · Third-party catalog clients put the role password in their own URL 🟡

The chart passes no credential in a URL, but titiler-pgstac, tipg and
stac-fastapi assemble `postgresql://user:password@…` from `POSTGRES_*` / `PG*`
themselves. Terraform's generated passwords are alphanumeric, so they are safe;
an `existing`-mode deployment must give `stac_api` and `stac_reader` URL-safe
passwords.

### I-167 · tipg as `stac_reader` sees only what PUBLIC may read in `public` 🟡

Spec §7.2 gives `stac_reader` "SELECT on the vector tables it serves", but no
vector tables exist yet: tipg lists PostGIS function collections only (I-126).
R-7 adds no grant. A future seeded vector table must grant SELECT to the
reader role in the same change, or tipg on the chart will not list it.

### I-168 · BFF responses carry the proxy's in-cluster host in their links 🟡

The app forwards BFF writes to `http://<fullname>-auth-proxy:8000`, so
stac-fastapi builds the links in a write response from that host, not the
public catalog URL. The UI does not follow links from write responses today;
anything that starts to should rewrite them or set
`app.builtinCatalogInternalUrl` to the public URL.

### I-169 · The in-cluster demo seed is render-verified; what it cannot do yet 🟡

The demo seed Job (R-10) and `pipeline.demo --in-cluster` are proven by
render tests and pytest only; the first live run is R-11. Known gaps and
accepted deviations:

- **Process runs.** The extractor, GeoColor and the image scans the seeds
  trigger run on the Kubernetes executor, which starts nothing until the
  cluster meets the runs-plane prerequisites (R-11; I-160). Until then `seed`, `goes-seed` and
  `cube-seed` complete and every run they trigger waits, requeued.
- **`images-seed` fails the Job until the cluster meets the runs-plane prerequisites.** It waits for scans that never
  run, then refuses to deploy an image that is not `approved`, so its
  container exits non-zero after `--scan-timeout` (1800 s by default). The
  Job runs with `backoffLimit: 0`, so that failure is final and does not
  re-run the seeds before it: a pod retry would start from the first seed
  and replace every demo process's revisions and run history on each attempt.
  Start-up ordering is handled inside the pod instead (`--wait-ready`). Leave
  `images-seed` out of `demoSeed.seeds` until then.
- **Writes bypass the app.** As on compose, the seeds write catalog documents
  straight to stac-fastapi and `stac_higher` rows straight to the database as
  the owner role, not through the BFF (ADR 0008) or an audited API route, so
  no `audit_log` rows record them. Accepted for an operator-run demo Job, the
  same split `pipeline.loadgen` uses. (`images-seed` adds its images through
  `/api/images`, which audits; its processes are direct writes like the
  rest.)
- **STAC API ingress.** The Job reaches stac-fastapi directly, as compose's
  seeds use `:8082`: `demoSeed.stacUrl`, by default the chart's stac-api
  Service, not the auth-proxy (which refuses the seeds' tokenless writes with
  `auth.mode: oidc`). The stac-api NetworkPolicy admits
  `app.kubernetes.io/component=demo-seed` when `demoSeed.enabled`.
- **`images-seed` bearer.** It authenticates to the app with a static
  operator bearer token from a Secret (`demoSeed.appBearer`). An IdP access
  token expires (Cognito: 1 hour by default), so the deployment refreshes it
  before each run.
- **Teardown.** `teardown` and `goes-teardown` delete platform bytes directly
  and refuse in-cluster (ADR 0011); a deployed demo is removed through the
  app.
- **Re-seed on a new image.** The Job re-runs when its image, seed list,
  args, env or Secret references change (not on a chart-only release with
  the same digests), which replaces the demo processes' revisions and run
  history.

Tracked in: GitHub #147 (R-11 runs it live).

### I-189 · The runs plane is render-verified only 🟡

R-8's objects are proven by render tests, drift tests against
`infra/kubernetes/` and `infra/kueue/`, kubeconform (Kueue v0.20.8's own
schemas) and the contract fixture; no EKS cluster has applied them. The kind
leg applies `infra/kubernetes/` and `infra/kueue/common/` as files and, of
the chart, only the chart-only placement policy (`stac-higher-runs-placement`,
rendered with kind-equivalent values): kind proves that policy compiles on a
real API server, admits every Job the executor builds and refuses foreign
placements. A plain cel-go run proves nothing about compilation: Kubernetes'
CEL environment adds options (homogeneous aggregate literals among them)
that refuse expressions plain cel-go accepts, which is how an earlier render
of this policy passed cel-go and would have failed `helm install`. The
NodeClass and NodePools are proven only by render tests (kubeconform skips
the NodeClass, I-194). R-11 must prove, on EKS: Helm refuses the install before creating anything when the Kueue CRDs
are absent; the NodeClass, NodePools, ClusterQueue and LocalQueue become
ready; the allow policies are enforced (a run pod reaches DNS and the object
store and nothing else; a scan pod reaches a public registry on 443 but not
IMDS, the Pod Identity agent, the API server or a VPC-internal HTTPS
endpoint); the CNI closes the start window (I-188); a scan is admitted with
the flavor's label and toleration injected (I-195); the NodePool headroom is
enough when the quota is full; the placement policy admits every Job the
executor builds and refuses one with another family's or a hand-made
placement. The R-11 issue carries the ordered list.

Tracked in: GitHub #147.

### I-190 · One stac-higher release per cluster 🟡

The runs plane's names are fixed: the runs namespace (`processRuns.namespace`
can move it, but the executor and the chart then follow one value), the
ClusterQueue and LocalQueue `process-runs`, the WorkloadPriorityClasses
`interactive` and `triggered` (generic names another Kueue user in the cluster
may also want), the four admission policies, the NodeClass
`stac-higher-runs` and the NodePools `stac-higher-runs-<family>`. A second
release in the same cluster would fight over them. A cluster that already
has its own `interactive` / `triggered` priority classes must reconcile
them by hand or set `processRuns.clusterObjects: false`.

### I-191 · Runs isolation is IPv4 only; DNS and nodes come from the cluster 🟡

On an IPv6 EKS cluster NetworkPolicies do not filter a pod's IPv4 egress
(EKS User Guide, Auto Mode network policies), so runs are not supported
there. The chart fails closed: it refuses every IPv6 CIDR in the runs values
(DNS included) and renders no IPv6 `ipBlock`, so no rule admits `::/0`. The
CoreDNS peer, the runs node role and its subnet and security-group selectors
are deployment inputs Terraform does not emit (the cluster repo knows
them). `aws.s3PrefixListCidrs` is a snapshot of the region's S3 managed
prefix list: if AWS changes the list, re-apply Terraform and re-install the
chart, or runs lose the object store.

### I-192 · Profile maxima are checked against the family quota, not against a node 🟢

The render refuses a profile whose `cpu.max`, `memory_mb.max` or
`gpu_count.max` exceeds its family's Kueue quota. It cannot check the
NodePool's requirements against an instance size: requirements that exclude
every instance large enough for a profile's maximum leave its largest runs
pending until `waitForPodsReady` evicts them.

### I-193 · The scanner may reach any public address on 443 🟡

An image-scan pod (an untrusted image's inspection, and a Job shape a
compromised pipeline identity can create, I-184) may open TCP 443 to any
address outside link-local, RFC 1918 and CGNAT space (those five ranges
are template constants), plus the private CIDRs named in
`processRuns.network.scannerPrivateCidrs`. It can therefore reach any
public HTTPS endpoint, including the EKS API server's public endpoint when
it is enabled (the scan has no token) and any VPC range outside RFC 1918 and
CGNAT space, unless the deployment lists those in
`processRuns.network.scannerEgressExcept` (chart README "Process runs").
Closing that needs an egress proxy with a registry allow-list for scans.

Tracked in: GitHub #68.

### I-194 · kubeconform skips the NodeClass 🟢

The datree catalog's `eks.amazonaws.com/v1` NodeClass schema predates
`advancedCompute` and is closed, so `-strict` would reject `podPidsLimit`;
`helm.yml` passes `-skip NodeClass`. A render test pins the NodeClass's
fields; a typo outside them is caught only on a cluster (R-11 runs
`kubectl apply --dry-run=server` on it).

### I-195 · Image scans are placed by Kueue's flavor toleration 🟡

A scan carries no hardware profile, so its pod has no node selector and no
toleration (K-5). Each dedicated family's ResourceFlavor tolerates the runs
taint, and Kueue injects the flavor's node labels and tolerations when it
admits the Job (proven on kind by K-6). If that injection ever stops, scans
cannot schedule on the tainted runs nodes and `images-seed` fails; the
fallback is a pipeline change that gives profile-less specs the default
profile's `backend.kubernetes`.

### I-196 · What the `isolated` level still allows 🟡

An `isolated` run may reach DNS and the object store, and both are wider
than they look:
- **All of S3 in the region.** `aws.s3PrefixListCidrs` is the region's S3
  managed prefix list, so the policy cannot tell the platform bucket from any
  other. A run that brings its own credentials (in its code or input data)
  can write to any bucket it can authenticate to. The run's own STS
  credential is bucket-scoped (ADR 0013); egress is not. Mitigations, not
  built: reach S3 through a VPC gateway endpoint whose endpoint policy allows
  only the platform's buckets, or bucket-scoped egress through a proxy.
- **DNS recurses.** The cluster resolver forwards to the VPC resolver and on
  to the internet, so a run can tunnel data out through DNS queries.
  Mitigations, not built: a DNS policy (Route 53 Resolver DNS Firewall on the
  VPC, or a CoreDNS policy for the runs namespace) that answers only the
  names a run needs.
- Tracked in: `infra/helm/stac-higher/templates/runs-networkpolicies.yaml`.

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
- **I-131** · The deploy form's memory input was free-form against a per-profile bound — 🟢 resolved (K-2 hardware picker, GitHub #10)
