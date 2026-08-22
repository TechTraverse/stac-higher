# Resolved issues — archive

Fully-resolved entries moved out of [`ISSUES.md`](ISSUES.md) to keep the live
file focused on open risk. Each entry is preserved verbatim as of its
resolution; the live file keeps a one-line stub per ID so inbound references
(`FEATURES.md`, ADRs, commit messages) still land. Issues that retain an open
or amber half (e.g. I-11, I-36) stay in the live file.

---

### I-4 · Adapter `list/get` live coverage 🟢 resolved (narrowed)

The full `StorageAdapter` interface is implemented, and the original concern —
adapters never exercised against live servers — is closed: `test()` is
exercised live via the drain job; **S3 `list`/`get`** were live-verified by the
Phase 4 ingest e2e (2026-07-16, MinIO drop → DISCOVER → FETCH byte-identical);
**SFTP `list`/`get`** by the 2026-07-20 Slice C run (first live SFTP source →
canonical copy → itemize); **SFTP/FTP `put`/`move`** by the Slice B-iii live
delivery verification (see I-45). Remaining slivers, accepted: FTP-source
`list`/`get` and the adapters' `delete` paths are covered by unit tests with
mocked clients only, and FTPS live-exercise stays blocked on arm64 (tracked as
I-6).
- Tracked in: `services/pipeline/tests/test_adapters.py`; I-6 for FTPS.

### I-12 · `connection_checks` accumulation 🟢 resolved (M2-G)

`pipeline.history_retention` (hourly) deletes checks older than
`CONNECTION_CHECKS_RETENTION_DAYS` (default 30) and flips checks stranded
`pending`/`running` on soft-deleted connections to `failed` (the M1
carry-forward). [ADR 0012](decisions/0012-table-hygiene.md).

### I-21 · Reference-mode ingest stalls at `settled` 🟢 resolved (Slice C)

`storage_mode: reference` associations used to run DISCOVER (files reach
`settled`) but stop there — GROUP formed no groups and FETCH skipped the copy,
so nothing advanced to `stored`/`itemized`. **Slice C resolves this**: GROUP
now forms groups for reference mode identically to copy mode; FETCH's
reference branch records a stable, credential-free `source_href`
(`S3Adapter.public_object_url`) in `ingest_files.source_href` and advances the
ledger `settled` → `stored` without copying bytes; EXTRACT's byte-source seam
(`MemberByteSource`/`CanonicalByteSource`/`SourceAdapterByteSource`) reads the
source bytes directly for `build_item`; ITEMIZE is unchanged. The asset route
resolves reference-mode items via `resolveAssetTarget` → `lookupReferenceHref`,
302-ing straight to `source_href` with no presigning and no decryption. Live
SFTP/FTP + a continuous scheduler-driven run (Task 10) was the remaining
verification, since completed.
- Tracked in: `services/pipeline/.../ingest/group.py`,
  `services/pipeline/.../ingest/fetch.py`,
  `services/pipeline/.../ingest/extract.py`; `app/src/lib/storage/resolve.ts`,
  `app/src/lib/storage/reference.ts`.

### I-27 · pgstac requires a non-null geometry — `defaults_only` (and geometry-less `sidecar`) items cannot be catalogued 🟢 resolved (Slice B4a)

**Found during the B4 live verification run (2026-07-17).** pgstac's `items`
table enforces a **NOT NULL `geometry` column**, so an item without a geometry
is rejected on upsert (`NotNullViolation` on `_items_*.geometry`) — even
though the STAC spec and `stac-pydantic` both permit `geometry: null`.
- **`raster_auto` was unaffected** — rio-stac always derives a footprint.
- **`defaults_only` — and `sidecar` when no geometry is parsed — produced
  `geometry: null` items pgstac refused.**

**Resolved by Slice B4a** with a layered, best-effort-first resolution chain in
`build_item` (`services/pipeline/.../ingest/extract.py`), applied whenever the
chosen strategy leaves the item geometry null: (1) strategy geometry
(`raster_auto`/`sidecar`, unchanged); (2) **best-effort GDAL open** of the
primary member (`geometry_from_raster`, gated by `is_gdal_candidate` — covers
COG/GeoTIFF/netCDF/GRIB/Zarr/etc., not just the `raster_auto` raster set) —
recovers a footprint even under `defaults_only`/`sidecar` when the primary
file happens to be georeferenced; (3) an **opt-in collection-extent fallback**
(`metadata.defaults.geometry: "collection"`, cross-runtime Zod contract in
`app/src/lib/associations/schemas.ts`) — `run_itemize` reads the collection's
bbox via `PgstacWriter.get_collection_bbox` and passes a `collection_fallback`
dict into `build_item`, degrading to a `global_fallback` world polygon when
the collection has no real (non-global) extent; (4) **fail-fast** —
`ExtractError` when none of the above yields a geometry, so a null-geometry
item is never emitted (the group lands `failed`, not stuck at `stored`). Every
item that ends with a geometry carries
`properties["stac_higher:geometry_source"]` ∈
`raster`/`sidecar`/`collection_extent`/`global_fallback` for provenance.
- Tracked in: `services/pipeline/.../ingest/extract.py` (`GDAL_CANDIDATE_EXTS`,
  `is_gdal_candidate`, `geometry_from_raster`, `bbox_to_polygon`,
  `build_item`); `.../ingest/itemize.py` (`_build_collection_fallback`,
  `run_itemize`); `.../stac/pgstac_writer.py`
  (`PgstacWriter.get_collection_bbox` + `PgPgstacWriter` impl);
  `app/src/lib/associations/schemas.ts` (`metadataSchema.defaults.geometry`).

### I-35 · Pipeline image was missing `libexpat1` — in-container `raster_auto` EXTRACT failed 🟢 resolved (Slice C live verification)

The runtime stage of `services/pipeline/Dockerfile` installed only `libpq5`.
rasterio's bundled-GDAL wheels dynamically link `libexpat` at runtime, so
`import rasterio` inside the deployed container raised
`ImportError: libexpat.so.1: cannot open shared object file` and `raster_auto`
EXTRACT could not run in-container — breaking the Phase 4 done-when (dropped
file → catalogued item) for any GeoTIFF-bearing ingest. B4's `raster_auto`
verification ran host-side (uv venv), which masked the gap; the first
**in-container** scheduler-driven itemize (Slice C live verification) surfaced
it. Fix: add `libexpat1` to the runtime apt install (one line). Verified by a
fresh image rebuild importing `rasterio`/`rio_stac` cleanly and a full
scheduler-driven reference itemize producing a queryable `ST_Polygon` item.
- Tracked in: `services/pipeline/Dockerfile`.

### I-37 · `on_update` must derive redelivery from `delivery_log`, not the outbox `op` 🟢 resolved (Slice B-ii, as designed)

Live-verified in Slice A: pgstac implements an item update as **delete +
insert** via the transaction API, so an update surfaces as a `delete` then an
`insert` outbox row (never `op='update'` via that path; the pypgstac upsert
path fires a single `update` — see I-46). The constraint this issue demanded
was implemented in Slice B-ii: `deliver_item`'s `on_update: redeliver|ignore`
logic decides first-delivery-vs-redelivery from the prior `delivery_log` row,
never from the outbox `op`.
- Tracked in: [ADR 0007](decisions/0007-outbox-trigger-ownership.md) "Update
  semantics"; `services/pipeline/.../delivery/worker.py`.

### I-38 · Dispatcher item-visibility race is best-effort skip 🟢 resolved (Slice C)

An event whose item is not yet visible is no longer silently drained:
`dispatch_once` releases the claim with a cool-off
(`item_events.dispatch_attempts` + `next_dispatch_at`, migration 012) and a
later wake (NOTIFY or the poll fallback) retries it, up to
`MAX_VISIBILITY_ATTEMPTS`; only then does it drain, with a loud log. The
cool-off keeps deferred events out of the drain-until-empty loop so one wake
cannot burn the retry budget.
- Resolved by: `ai/slice-c` (`dispatcher/loop.py`, `dispatcher/repo.py`,
  migration `012_dispatch_retry_and_backfills`).

### I-39 · `dispatch_once` has no per-event error isolation 🟢 resolved (Slice B-iii)

The API-reachable trigger is closed (`parseAssociationUpdate` validates PUT
`config` against the existing row's direction; `match_item` wraps the whole
per-association body in its isolation guard), and `dispatch_once` now wraps
each event's `get_item`/`match_item` in a per-event guard: a poison event is
logged loudly and drained with the batch (dead-lettered into the logs) instead
of busy-looping the claim.
- Resolved by: `ai/i39-pair` + Slice B-iii (`dispatcher/loop.py`).

### I-44 · `delivery_log.attempts` is a lifetime counter, not reset on redelivery 🟢 resolved (Slice B-ii)

`upsert_pending`'s `ON CONFLICT DO UPDATE` used to reset `status='pending'`
but leave `attempts` untouched, so a legitimately-redelivered item's
`attempts` climbed across independent events (each `mark_delivering`
increments). Harmless in B-i (`attempts` was observability only), but B-iii's
planned `max_attempts` dead-lettering would have dead-lettered a
frequently-redelivered row without a real retry sequence. **Resolved by Slice
B-ii**: `upsert_pending` now resets `attempts = 0` on the redelivery conflict
branch, so `attempts` counts a single delivery cycle, not the item's lifetime.
- Tracked in: `services/pipeline/.../delivery/repo.py` (`upsert_pending`);
  found in the Slice B-i whole-branch review, resolved in Slice B-ii.

### I-45 · Concrete adapter `move()` bodies are inspection-only; SFTP/FTP delivery not live-verified 🟢 resolved (Slice B-iii)

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

### I-49 · Reference-mode delivery residuals from the B-ii whole-branch review 🟢 resolved (Slice B-iii)

(1) a reference basename collision now logs a warning and deterministically
keeps the first source; (2) `mark_failed` persists the partial
`delivered_assets` map, so a retry skips assets the failed cycle already wrote
(unit-proven: only the missing asset re-transfers); (3) the completion
manifest is pruned to the item's current assets before writing; (4) the
missing tests exist (`test_delivery_retry.py`: source-read failure → failed
row, mixed reference+canonical item, md5 + copy-failure fallback combo).
- Resolved by: Slice B-iii (`delivery/{worker,repo}.py`,
  `tests/test_delivery_retry.py`).

### I-50 · UI catalog writes have no token path under auth enforcement 🟢 resolved (ADR 0008 BFF)

Built-in-catalog browser writes now route through `/api/catalog/[...path]`
(transaction endpoints only, writes only): the route injects the caller's
session access token server-side (the token never reaches page JavaScript; the
proxy stays the enforcement point), `stacFetch` routes built-in-catalog
mutations there unconditionally (dev pass-through included, so the seam can't
silently regress), and the guard gates the paths (operator+) with one
`audit_log` row per mutation — the catalog plane is audited. The enforcement
suite gained the UI-path leg (`tests/integration/bff-catalog-writes.test.mjs`):
a real authorization-code session login → BFF write with only the httpOnly
cookie → 201 through the enforced proxy → audit row, replacing the
password-grant client for the browser case.
- Resolved by: `ai/i50-bff` ([ADR 0008](decisions/0008-bff-catalog-writes.md);
  `app/src/pages/api/catalog/[...path].ts`, `app/src/lib/stac-api/client.ts`,
  `app/src/lib/authz/permissions.ts`).

### I-51 · ADR 0009 deletion semantics — soft-delete half + GC half 🟢 resolved (pre-B-iii wave + M2-F)

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
**GC half resolved by M2-F (ADR 0011):** BFF item/collection deletes now mark
`asset_gc` prefixes (collected after the grace window by
`pipeline.asset_collect`), the `archived` state is enforced (item writes and
new associations refused; the retention sweep expires everything), and
reference-mode ASSOCIATION delete now removes its reference-backed items
(the open question settled — aligned with connection delete).
- Tracked in: [ADR 0009](decisions/0009-deletion-semantics.md),
  [ADR 0011](decisions/0011-retention-gc.md); migrations 010/016/017.

### I-52 · Ingest has no crash recovery: stuck-`fetching` rows are unrecoverable, `failed` is terminal 🟢 resolved (Slice B-iii)

If the pipeline dies mid-FETCH (most plausibly an OOM from the buffered
multi-GB `get`, I-19/I-26), the ledger row was stranded at `fetching` forever:
DISCOVER explicitly skips `fetching` rows even on fingerprint change, GROUP
only forms groups from `settled` rows, and nothing swept stalled Procrastinate
`doing` jobs — the file silently never became an item, with no alarm. A
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

### I-53 · Cross-runtime config contracts have no drift test (golden fixtures missing) 🟢 resolved (pre-B-iii wave)

Golden JSON fixtures for both §5.1 config directions live in
`tests/contract-fixtures/` (valid + invalid documents with per-side
accept/reject expectations — the Zod write gate is strict, the Python readers
are lenient by design; the README there documents the semantics). Both suites
consume them: `app/src/__tests__/contract-fixtures.test.ts` asserts Zod's
accept/reject per case and that the minimal document parses to the golden
defaults document; `services/pipeline/tests/test_contract_fixtures.py` runs
the same cases through `parse_ingest_config`/`parse_delivery_config` and
asserts every re-applied default against the same golden values. The standing
rule ("new cross-runtime shape ⇒ new shared fixture") is in AGENTS.md.
Building the fixtures surfaced one real drift, fixed with them:
whitespace-only `source_path`/`path_template` passed Zod's `min(1)` but the
Python parsers `.strip()`-reject it — the Zod schemas now reject
non-blank-violating values too. The direction-aware update schema (the app
half of I-39) landed in the prior iteration.
- Resolved by: the pre-B-iii hardening wave (`ai/i53-fixtures`), 2026-07-25.

### I-54 · pgstac 0.9.10 partition-constraint parser breaks on fractional-second datetimes 🟢 resolved

Found in the M1 demo rehearsal (2026-07-26). After the first item loads into a
collection, pgstac's `update_partition_stats` rewrites the partition's CHECK
constraint to the tight min/max of the loaded data — including **fractional
seconds** (our EXTRACT datetimes carry microseconds). pgstac's
`get_tstz_constraint` then re-parses that constraint with the regex class
`[0-9 :+\-]`, which omits `.`, so the parse fails and
`partition_sys_meta.constraint_dtrange` reads unbounded `(,)`. pypgstac's
loader consults that metadata, concludes no constraint widening is needed, and
every subsequent single-item load with a different datetime dies with
`CheckViolation` on `_items_N_dt` (tenacity retries ~3 min, then the job
fails permanently). Any collection receiving a second item in a later load is
affected — the exact NRT shape M1 demos. Earlier live runs missed it because
they loaded batches in one call or re-upserted the same item (same datetime).
- Found in: M1 rehearsal (ROADMAP §9 M1 evidence).
- Resolved by: `ai/i54-pgstac-migrate`, 2026-07-30. Root-cause correction to
  the analysis above: upstream **already fixed the regex in pgstac v0.9.11**
  (`pgstac.0.9.10-0.9.11.sql`, class becomes `[0-9 :.+\-]`), and compose has
  pinned `pgstac:v0.9.11` since 2026-07-17 — but the pgstac image only
  installs its schema via initdb on a *fresh* volume, so the persisted dev
  volume silently stayed at schema 0.9.10 (a fresh `down -v` stack was in
  fact never broken). Durable fix: a `pgstac-migrate` compose one-shot
  (pipeline image, `pypgstac migrate`, gates `api`/`pipeline` via
  `service_completed_successfully`) migrates existing volumes on every `up` —
  ADR 0001's pgstac bullet amended. Run against the live dev DB: 0.9.10 →
  0.9.11, replacing the manual hotfix with the canonical function. Regression
  tests in `services/pipeline/tests/test_integration_itemize.py`
  (DATABASE_URL-gated): a schema-version drift guard (≥ 0.9.11) and the
  two-sequential-microsecond-loads shape, both verified red (stock 0.9.10
  function → the exact rehearsal `CheckViolation`) then green post-migration.

### I-55 · Ingest jobs have no queue-level retry; an itemize crash strands the ledger at `stored` 🟢 resolved

`itemize.py` deliberately let unexpected exceptions propagate with the comment
"the job retries (transient DB errors)" — but `register_task`
(`queue/procrastinate_backend.py`) registered every task with **no retry
strategy**, so Procrastinate marked the job failed after one attempt. The
ledger rows stayed `stored` (FETCH's mark), a state neither I-52 recovery
sweep covered (`fetching`-stalled and `failed` only) — the file silently never
became an item, with no alarm and no retry. Hit live in the M1 rehearsal via
I-54 (the CheckViolation was the unexpected exception); recovered manually by
flipping the row to `failed` so the sweep re-drove it (which worked exactly as
designed from there).
- Found in: M1 rehearsal (ROADMAP §9 M1 evidence).
- Resolved by: `ai/i55-ingest-retry`, 2026-07-30 — both halves. (1) Queue-level
  retry: `RetrySpec` on the queue interface, mapped to Procrastinate's
  `RetryStrategy`; all four ingest chain stages and `deliver` register with
  4 attempts / 60 s wait (`STAGE_RETRY` / `DELIVER_RETRY`). The deliver audit
  found the same blind spot pre-record: a transient `load_target`/`get_item`
  failure lost the delivery outright (outbox already claimed, no
  `delivery_log` row for the sweep) — batch re-runs are safe because
  `deliver_item` upserts one log row per (association, item) and paths are
  deterministic overwrites. (2) Stored-stall sweep: `sweep_stuck_stored`
  (INGEST_STORED_STALL_SECONDS, default 30 min) re-settles stalled `stored`
  rows against the same `retries` budget as the failed sweep — the idempotent
  GROUP → FETCH → ITEMIZE chain re-drives them — and dead-ends rows at the cap
  to terminal `failed` (no infinite hot loop on a persistent itemize failure).
  Unit tests cover both sweeps and the full crash → sweep → re-drive →
  `itemized` path (`test_ingest_recovery.py`).

### I-56 · Delivery had no record of intent before `deliver_item`, and no sweep for rows stranded mid-flight 🟢 resolved (M2-0)

Two halves of the same hole, carried out of the I-55 `/simplify` pass as a
deferred behavior change:

1. **Nothing was recorded before the fallible work.** The deliver handler ran
   `load_target` → `parse_delivery_config` → `build_adapter` → `get_item`
   before `deliver_item` wrote its first `delivery_log` row. A fault there was
   covered only by the queue-level `DELIVER_RETRY` (I-55: 4 attempts × 60 s),
   and once those were spent the delivery was lost **invisibly** — the outbox
   row was already claimed and the retry sweep had no row to re-drive. An
   `AdapterBuildError` was worse than that: it logged and returned, so the
   whole batch vanished with no retry at all.
2. **`pending` and `delivering` were unrecoverable states.** `list_due_retries`
   only sees `failed` rows with a due `next_attempt_at`, so a worker that died
   mid-transfer left its row at `delivering` forever — the delivery-side twin
   of the ingest `fetching` stall (I-52) — and any row `requeue_for_retry`
   flipped to `pending` was stranded the same way if its job then died.

**Resolved (M2-0).** `pre_record` inserts a placeholder row per item at the top
of the deliver handler, before anything fallible. It is deliberately
**INSERT-only** (`ON CONFLICT DO NOTHING`), not a hoisted `upsert_pending`:
resetting an existing row to `pending` would clobber the state
`deliver_item`'s `on_update: ignore` fire-once gate and log-based overwrite
gate both read. A `load_target` miss (disabled/deleted association) discards
the placeholders this job created — guarded on `status = 'pending' AND
attempts = 0` so a concurrent delivery is never deleted — instead of leaving
phantom rows. Config-parse and adapter-build failures now settle their rows
`failed` with the real cause on the normal retry schedule (dead-lettering at
`max_attempts`) rather than disappearing. `sweep_stalled_deliveries`
(`DELIVERY_STALL_SECONDS`, default 30 min) re-enters `pending`/`delivering`
rows past the stall window into the retry path, preserving `attempts` so
`max_attempts` still converges; it runs at the top of the existing
`delivery_retry_sweep` tick, which re-enqueues them in the same tick.

**Residual, stated honestly:** the pre-record is itself a DB write, so it does
not help when Postgres is wholly unreachable — nothing inside the pipeline can,
and in that state Procrastinate cannot fetch jobs either, so the work stays
queued rather than lost. What it converts from silent loss to a visible,
recoverable row is the far more common partial failure: a heavy
`pgstac.get_item` timing out under load while ordinary writes succeed, bad
credentials, a malformed config. Per the repo's convention the `PgDeliveryRepo`
SQL is `# pragma: no cover` (the `FakeDeliveryRepo` carries the behavioral
contract); the three new statements want confirmation in the M2-I rehearsal.
- Resolved by: M2-0, `ai/m2-0-deliver-prerecord`.
- Tracked in: `delivery/repo.py` (`pre_record`, `discard_pre_records`,
  `sweep_stalled_deliveries`), `jobs/dispatch.py`,
  `tests/test_delivery_prerecord.py`.
