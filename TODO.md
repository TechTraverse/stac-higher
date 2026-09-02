# TODO — implementation queues (M3 NOAA-scale readiness · G GOES loop)

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.

**Read first:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` —
the approved design spec. Every slice below names the spec section that
specifies it. The measured evidence behind each one is in
`docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` (M3-S-A…M3-S-F), and
the harness that produced it — and re-runs any of it — is
`services/pipeline/src/pipeline/loadgen/` (its README has the preconditions).

The M3 **scoping** queue is closed; it lives in git history at `b7fb503`
(spec + adversarial review) and its findings are appended to the scoping notes.
Spec approved by the lead 2026-09-01.

## The one thing not to get wrong

**M3-A goes first, and batching must not jump the queue.** Batching before the
pgstac fix would hide an O(n²) behind a bigger constant and let it resurface at
production partition sizes — a far more expensive place to find it. This is why
M3-E was cut rather than reordered (spec §7.4).

**M3-C must land with or before M3-D.** Raising concurrency against
whole-object buffering converts a throughput problem into an OOM: ~9 GB of
resident memory at concurrency 12–16 against the 250 MB size tier, versus
~1.8 GB after streaming (spec §3, S-E).

## Settled — do not relitigate

Decisions the lead settled 2026-09-01 (spec §7, with the reasoning):

- **`use_queue` + `update_collection_extent` are SESSION GUCs** set by the
  pipeline's pgstac writer on its own connection — not a deployment step, not
  a `pgstac_settings` row. `pypgstac`'s `PgstacDB(use_queue=True)` is the seam;
  `get_setting` resolves conf → session GUC → table, so the GUC wins.
- **Worker concurrency default is 12** (conservative end of the measured
  12–16 band). It stays a setting; S-E's envelope is the sizing formula.
- **`update_collection_extent = true`**, on the same connection.
- **M3-E (write batching) is CUT from M3** — an optimisation, not a defect fix.
  Its one non-optimisation part, the `flow_stats` batching, is folded into
  M3-D.
- The 60 items/s total budget; local-first (no AWS, no SQS — Phase 8); the OGC
  facade stays parked (ADR 0016).

## Gate (spec §2)

A load rehearsal on the **auth-enforced** local stack sustaining **60 items/s
total catalog write rate for 30 minutes** — ~30 items/s ingest-origin, a
synthetic process at ≥50% source share contributing ~30 items/s of output, and
delivery fan-out to one destination — with **no lost items** (offered =
catalogued = delivered), **alerting functional throughout**, **bounded memory**
(worker RSS flat across the window, independent of asset size), and a written
load report recorded M-gate style in ROADMAP §9.

## Slices (dependency spine encoded in this order — work top-down)

- [ ] **M3-A · pgstac write path.** Spec §4. The single highest-leverage
      change measured: 2–3.5 → 22 items/s on the real pipeline. Set
      `pgstac.use_queue` and `pgstac.update_collection_extent` as session GUCs
      on the pgstac writer's connection (`PgPgstacWriter._upsert_sync` — use
      `PgstacDB(use_queue=True)`, and set the second GUC on the same
      connection; `PgstacDB` accepts an existing `connection`/`pool`). Add the
      drainer: **pg_cron in cloud, a periodic pipeline job locally**, the
      pipeline job no-opping when a database-side drainer is configured so the
      two cannot fight. `pgstac.run_queued_queries()` is a **PROCEDURE** —
      `CALL`, not `SELECT`. Ship a **`query_queue` depth metric**: with the
      settings session-scoped it is the only outside-the-process evidence they
      are in effect, and a drainer that silently stops is otherwise invisible.
      **Measure and record** what the drain costs — the two `REFRESH
      MATERIALIZED VIEW` calls inside `update_partition_stats` scale with
      partition count, not write rate, so drain cadence should be tuned against
      partition count (§4.5). Verify against the harness, not by inspection.
- [ ] **M3-B · connection pool.** Spec §3, S-D. Every pipeline repo method
      currently opens a fresh `psycopg.AsyncConnection` (4.19 ms measured);
      ~14 per item means ~420 connections/s ≈ 1.8 core-seconds of connect per
      wall-clock second at budget, plus a backend fork each. Invisible at
      concurrency 1 behind the pgstac call; **first-order the moment M3-D
      lands**, which is why this is a precondition rather than an optimisation.
      The pool's `on_connect` is also where M3-A's two GUCs belong.
- [ ] **M3-C · bounded-memory byte path.** Spec §3, S-E; closes I-19/I-26.
      Three parts. (1) **EXTRACT reads through a URI, not a buffer**: swap the
      `MemberByteSource` seam from `-> bytes` to something GDAL can open
      (`/vsis3`). Measured: byte-identical STAC item including
      `with_raster=True` statistics, in less time, at +55 MB instead of
      +145 MB for a 64 MB object. Note `rasterio` **refuses raw `AWS_*` GDAL
      options** — use `rasterio.Env(session=AWSSession(..., endpoint_url=...),
      AWS_HTTPS="NO", AWS_VIRTUAL_HOSTING="FALSE")`. (2) **FETCH: server-side
      copy when the gate allows** (reuse `delivery/transfer.can_server_side_copy`
      and `S3Adapter.copy_object_from` — 7.5× faster, zero bytes through the
      worker), **streamed multipart otherwise**, falling back on copy failure
      as the delivery worker already does. (3) **Make the memory bound explicit
      and configured** (`GDAL_CACHEMAX`, transfer chunk size and concurrency)
      so the envelope is a setting, not an emergent property. **Review point,
      not a footnote:** a reference-mode `/vsis3` read puts a connection's
      decrypted credentials into a GDAL session. Nothing new is exposed — the
      worker already decrypts them via `build_adapter` — but it is a new place
      they live. SFTP/FTP sources keep the buffered `get()` (I-83).
- [ ] **M3-D · concurrency.** Spec §3, S-C. The remaining ~5.5×. Worker
      concurrency as a setting, **default 12** (`run_worker_async()` currently
      takes Procrastinate's `WORKER_CONCURRENCY = 1` for the whole service).
      Split the byte-heavy stages onto their own queue with a smaller
      concurrency (~4) so memory stays bounded without capping the cheap
      stages; every task is on the default queue today. Fix the one ledger that
      cannot survive concurrency: **`ingest_files` GROUP→FETCH is a
      read-then-write guard, not a claim** — make it a compare-and-set
      (`UPDATE … WHERE id = %s AND status = 'settled'`, skip on
      `rowcount = 0`), the shape `process_runs.claim_due_runs` already uses.
      Fold in the **`flow_stats` batching** (spec §7.4 carve-out): it removes a
      per-association row lock (~460 bumps/s, flat past concurrency 4) that
      this slice makes strictly worse — DISCOVER's existing one-rollup-per-tick
      shape is the template. **The review must go beyond S-C's audit**: that
      audit covered database-level claim semantics across eleven legs, not
      module-level mutable state, shared boto3/GDAL client reuse, or
      `asyncio.to_thread` pool sizing under 12 concurrent jobs (spec §5, §8).
- [ ] **M3-F · GC at rate.** Spec §3, S-B. `asset_collect` is the **only**
      byte-deletion path and the one GC leg whose capacity does *not* scale
      with collection count: `list_due_marks(500)` per 5-minute tick = 144k/day
      against 2.6M/day needed, **18× short**, and it **fails quietly** — open
      marks and a storage bill, not an error. Batch the prefix deletions
      (`DeleteObjects`, 1000 keys/call) and size the batch against the
      envelope. Add an **alert on an open-mark backlog** so the quiet failure
      stops being quiet. ADR 0011's mark-first-then-collect ordering and grace
      window are unchanged; only the batch size moves. Keep prefix construction
      in `storage/keys.py` and add a test that a batch never spans two
      collections.
- [ ] **M3-G · queue-table retention.** Spec §3, S-F. Nothing prunes
      Procrastinate's own tables: measured **3 events per job**, ~660 bytes per
      job across both tables, all retained forever — ~43.2M rows and **~7.1
      GB/day** at budget, on the catalog's instance, degrading the claim path.
      M2-G's `history_retention` covers the *application's* tables and says
      nothing about the queue's. Add `Worker(delete_jobs=...)` for the
      successful path plus a periodic `JobManager.delete_old_jobs` for the
      stragglers — **keeping failures**, since a deleted failed job is the
      history an operator needs to debug it.
- [ ] **M3-H · rehearsal driver.** Spec §3. `pipeline.loadgen` gains what the
      gate needs and does not have: a **process leg** (a synthetic process at
      ≥50% source share, so process-generated volume is measured rather than
      assumed — ROADMAP §9 requires M3's arithmetic to count it), a
      **sustained-rate mode** (every scoping run was a `--rate 0` saturation
      probe; the gate holds an offered rate for a window), and **gate-shaped
      verification** — offered = catalogued = delivered, asserted, so "no lost
      items" is checkable rather than eyeballed off counters.
- [ ] **M3-I · M3 gate rehearsal (LEAD ONLY).** Spec §2. Run the rehearsal on
      the **auth-enforced** stack (every scoping measurement was on the plain
      stack — the gate must not be the first time the enforced overlay sees
      this rate). Write the load report, record it M-gate style in ROADMAP §9,
      then open the `ai/main → main` promotion PR.

## G queue — GOES GeoColor loop (runs in PARALLEL with M3)

**Read first:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md`
(approved 2026-09-01). This queue is independent of the M3 queue above and is
worked by a SECOND agent in its own worktrees; the "first unchecked item" rule
applies within each queue, not across them. The two meet in exactly two places
(spec §13): M3-C replaces the EXTRACT byte-source seam with a URI (G-6's
extractor receives a location, never a buffer), and G-3's immediate enqueues
must be exercised at M3-D's concurrency before either is declared done. When a
G slice and an M3 slice touch the same file, the later merge into `ai/main`
resolves it per AGENTS.md; neither queue waits for the other.

Slices G-1, G-2 and G-4 are independent and may run concurrently in separate
worktrees. Detailed task plans exist for those three
(`docs/superpowers/plans/2026-09-01-goes-g{1,2,4}-*.md`); G-3, G-5, G-6 and
G-7 get their plans when their dependencies have merged.

- [x] **G-1 · Anonymous S3 connections.** Spec §8. `anonymous` flag on the s3
      config; credentials optional when set (UI hides the key fields); the
      adapter signs nothing (`botocore.UNSIGNED`); new contract fixture
      `s3-connection-config.json`. Plan: `2026-09-01-goes-g1-anonymous-s3.md`.
- [ ] **G-2 · Process inputs + network profile (isolated only).** Spec §3, §4.
      Staged `inputs/{batch_id}/` with `manifest.json`; platform-held assets
      granted read-only by source-collection prefix in the STS session policy,
      remote assets fetched through the matching reference-mode association's
      adapter (public GET fallback); `STAC_HIGHER_INPUT_PREFIX` /
      `STAC_HIGHER_INPUT_MANIFEST`; finalize skips `inputs/`; dispatcher batch
      entries gain `collection_id` + `op`; `runtime.network` block with
      `PROCESS_NETWORK_MAX` cap (`isolated` only at the write gate); new fixture
      `process-input-manifest.json`; ADR 0018; `docs/processes.md`. Plan:
      `2026-09-01-goes-g2-process-inputs.md`.
- [ ] **G-3 · Latency posture.** Spec §5. Immediate enqueue at the three hops
      (ITEMIZE/finalize → `dispatch_poll`; `trigger_run` → `process_run_now`);
      queued-run coalescing (widen the partial unique index to all queued runs
      per `(process_id, source_id)`); `settle: "immediate"` for s3 ingest
      sources. Depends on G-2 (shares `enqueue_run`). Exercise with the loadgen
      at M3-D's concurrency.
- [x] **G-4 · Tile server href mapping.** Spec §7.1. Derived image
      `infra/titiler/` wrapping both readers' `_get_asset_info` to map
      `/api/assets/{c}/{i}/{f}` → `s3://{PLATFORM_ASSET_BUCKET}/assets/{c}/{i}/{f}`;
      compose switches to it; `containers.yml` builds it; `docs/serving.md` +
      I-68 (local half closed). Plan: `2026-09-01-goes-g4-titiler-hrefmap.md`.
- [ ] **G-5 · Raster preview layer on the item page.** Spec §7.2. Shared
      `RasterTileLayer` over the tile server's item TileJSON (`assets=visual`
      when present), shown when `serving_enabled` and the item `info` call
      succeeds; silent otherwise. Depends on G-4.
- [ ] **G-6 · Extractors.** Spec §6. `processes.kind` (`transform` |
      `extractor`, immutable); `metadata.strategy: "extractor"` +
      `metadata.extractor.process_id` on ingest associations (group-owned,
      kind-checked both ways); ledger status `extracting`; ITEMIZE splits at
      the `build_item` → `validate_item` seam and triggers the extractor run
      with the draft in `input_items`; finalize's extract branch validates
      id/collection/href immutability and calls the shared ITEMIZE continuation;
      run failure fails every ledger row in the batch (no fallback); defaults
      600 runs/h and 120 s; display-only `extractor` graph edge; UI (kind badge,
      association picker); loadgen `--extractor` profile. Depends on G-2, G-3.
- [ ] **G-7 · GOES worked example + live-gated e2e.** Spec §2, §9, §10. The
      `goes-abi-metadata` extractor and `goes-geocolor` process on the CURRENT
      runtime image (rasterio `NETCDF:` subdatasets, numpy true colour + night
      IR, GDAL COG driver, rio-stac item); `docs/processes.md` worked example;
      `app/e2e/goes-loop.spec.ts` skipped unless `E2E_LIVE_NODD=1`, scoping the
      association's `include` to the newest `ABI-L2-MCMIPC` key listed over
      plain HTTPS; `run-e2e` skill update. Depends on G-1…G-6.

## Parked (do not start without the lead)

- **OGC API — Processes facade** (Phase 9 spec §11, I-66) — posture and
  Parts 2–5 outlook recorded in ADR 0016 (2026-08-31). Post-M3, lead-gated;
  re-survey the drafts when green-lit.
- **M3-E — write batching** — cut from M3 (spec §7.4), not abandoned: the
  pgstac upsert batching (3.8 → 375 items/s at batch 100), `delivery_log`
  transition batching via the existing `UNNEST` + `ON CONFLICT` shape, and the
  dispatch drain bound. Revisit in Phase 8, where cloud costs will feel the
  ≈16 statements per item more than a local box does.

## Discovered follow-ups

The M5 queue's follow-ups are archived with it at `59e8087`; the M3 **scoping**
queue's are at `b7fb503`, and its measured findings live in the scoping notes.
Residual gaps that are not slices are logged as issues instead — **I-82** (the
byte-volume model is declared, not measured — a real size census belongs before
any contractual scale or storage-cost claim) and **I-83** (streaming reaches
object stores only).

- **Re-run vs. a leftover container (M3-W-1 discovery).** A re-run reuses the
  run id, so `launch` asks for the container name `stac-run-{run_id}` — which
  collides (409) while that run's orphaned container still exists. The failure
  is self-healing (a 409 surfaces as `ExecutorUnavailable` → infrastructure
  requeue, no attempt spent → the next tick after the reaper's 15-minute pass
  succeeds), so it is a latency wart, not a correctness one. Fix candidates if
  it ever bites: reap-by-run-id before launch, or an attempt suffix on the
  container name. Not worth a slice on its own.
- **Collection extents have been stale since Phase 4.** M3-A turns
  `update_collection_extent` on, which fixes it going forward — but existing
  collections keep whatever extent they were created with until something
  writes to them. If that matters for the UI or for clients filtering by
  collection bbox, a one-off backfill (`SELECT collection_extent(id, TRUE)`
  per collection) is the fix, and it is not in any slice above.

(append here during iterations)
