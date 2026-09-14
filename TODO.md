# TODO — implementation queues

**This file holds EIGHT INDEPENDENT QUEUES. It is not one list.** Work only the
queue you were asked for, top-down within it. The first unchecked item in the
FILE belongs to M3 and is rarely the right default.

**If you were not told which queue, stop and ask.** Which one is right depends
on what the human wants that day; guessing costs a worktree and a merge, not
just a wasted read.

| Queue | What it is | State |
|---|---|---|
| **M3** | NOAA-scale readiness: ~60 items/s sustained, measured | Spec approved. **M3-A merged 2026-09-08; M3-B0 (harness hygiene + ADR 0020) merged 2026-09-09; M3-B merged 2026-09-14 (19.5 → 0.10 sessions/item); M3-C next (plan written 2026-09-09).** The ordering below is a dependency spine, not a preference; **M3-C merged 2026-09-14** (measurement owed, host disk full); M3-D plan written (`2026-09-14-m3-d-concurrency.md`) |
| **G** | GOES GeoColor loop: NODD → COG → deliver → tiles | **Queue complete 2026-09-04** (G-1…G-8 merged), standing demo running since 2026-09-04. Only G-8's lead-only live gate remains — see the follow-ups |
| **P** | Pipeline graph: per-product lineage lines + a full graph view + ghost-node fix | **Queue complete 2026-09-04** (P-1…P-4 merged). Two follow-ups in the follow-ups section |
| **X** | Built-in extractor library: stactools packages as one-click extractors | Spec **approved 2026-09-04**, **worked first**. X-1 merged 2026-09-04 (the set is **eleven**, not fourteen — I-107); X-2 next; X-3 coordinates with K-1; X-4 takes migration **028** |
| **K** | Process compute on Kubernetes + Kueue, hardware profiles | Spec **approved 2026-09-04**. K-1 may start; K-3 takes migration **029** (X-4 has 028); K-4 coordinates with M3-D |
| **W** | Ingest date window + retention cap | **Queue complete 2026-09-02** (W-1 and W-2 merged). Only the two lead-only live checks remain — see the follow-ups |
| **V** | Map page: the catalog's products as map layers (footprints, titiler imagery, tipg vector tiles) on one time axis | Spec **approved 2026-09-04**. V-1 merged 2026-09-04, V-2 merged 2026-09-08, V-3 merged 2026-09-14 (closes I-112), **V-4 merged 2026-09-14 — queue complete** (I-126 holds the spec §8 deferrals, I-127 the cached-listing nit). No migrations |
| **D** | Item lineage: `derived_from` links stamped on process outputs at finalize | Written 2026-09-06 (lead question, no separate spec — the slice text is the design). Two slices: D-1 pipeline stamp, D-2 item-page rendering (decisions settled 2026-09-07). No migrations |

Every queue runs the same loop (AGENTS.md): one slice per iteration, a worktree
off `ai/main`, and `npm run verify` — plus the pipeline's `pytest` and `ruff`
when the pipeline is touched — before merging. Slices name their dependencies
within a queue; respect those. Slices in **different** queues never block each
other (the one exception, G-7 needing W-1, is satisfied — W-1 is merged), and when two touch the
same file the later merge into `ai/main` resolves it (AGENTS.md conflict rules).

---

## M3 queue — NOAA-scale readiness

**Read first:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` —
the approved design spec. Every slice below names the spec section that
specifies it. The measured evidence behind each one is in
`docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` (M3-S-A…M3-S-F), and
the harness that produced it — and re-runs any of it — is
`services/pipeline/src/pipeline/loadgen/` (its README has the preconditions).

**Task plans (written 2026-09-07, read the plan before the slice text):**
M3-A → `docs/superpowers/plans/2026-09-07-m3-a-pgstac-write-path.md`;
M3-B → `docs/superpowers/plans/2026-09-07-m3-b-connection-pool.md`.
M3-C onward have no plan yet — the loop writes one (`writing-plans`) from
the spec section + slice text + the measurements M3-A/B record before
starting the slice.

The M3 **scoping** queue is closed; it lives in git history at `b7fb503`
(spec + adversarial review) and its findings are appended to the scoping notes.
Spec approved by the lead 2026-09-01.

### The one thing not to get wrong

**M3-A goes first, and batching must not jump the queue.** Batching before the
pgstac fix would hide an O(n²) behind a bigger constant and let it resurface at
production partition sizes — a far more expensive place to find it. This is why
M3-E was cut rather than reordered (spec §7.4).

**M3-C must land with or before M3-D.** Raising concurrency against
whole-object buffering converts a throughput problem into an OOM: ~9 GB of
resident memory at concurrency 12–16 against the 250 MB size tier, versus
~1.8 GB after streaming (spec §3, S-E).

### Settled — do not relitigate

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

### Gate (spec §2)

A load rehearsal on the **auth-enforced** local stack sustaining **60 items/s
total catalog write rate for 30 minutes** — ~30 items/s ingest-origin, a
synthetic process at ≥50% source share contributing ~30 items/s of output, and
delivery fan-out to one destination — with **no lost items** (offered =
catalogued = delivered), **alerting functional throughout**, **bounded memory**
(worker RSS flat across the window, independent of asset size), and a written
load report recorded M-gate style in ROADMAP §9.

### Slices (dependency spine encoded in this order — work top-down)

- [x] **M3-A · pgstac write path.** (merged 2026-09-08) Spec §4. The single highest-leverage
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
- [x] **M3-B · connection pool.** (merged 2026-09-14; measured 19.5 → 0.10 sessions per item) Spec §3, S-D. Every pipeline repo method
      currently opens a fresh `psycopg.AsyncConnection` (4.19 ms measured);
      ~14 per item means ~420 connections/s ≈ 1.8 core-seconds of connect per
      wall-clock second at budget, plus a backend fork each. Invisible at
      concurrency 1 behind the pgstac call; **first-order the moment M3-D
      lands**, which is why this is a precondition rather than an optimisation.
      **Corrected by M3-A (do not follow the earlier note here):** the two
      pgstac GUCs are NOT a matched pair that travels together, and this
      pool is not where they go. The writer already has its own
      `psycopg_pool.ConnectionPool` carrying `use_queue` ON +
      `update_collection_extent` ON via `configure_pgstac_session`
      (`pipeline/db/pgstac_session.py`), and the queue drainer needs the
      OPPOSITE pairing on a short-lived autocommit connection
      (`DRAIN_CONNECTION_SQL` in `pipeline/stac/query_queue.py`) because
      `CALL pgstac.run_queued_queries()` COMMITs inside itself and cannot
      run in a transaction block. `query_queue.py` says so at the class:
      "M3-B: keep this off the transactional repo pool." M3-B's async pool
      is a THIRD object; `configure_pgstac_session_async` exists for it.
- [x] **M3-C · bounded-memory byte path.** (merged 2026-09-14, `ai/m3-c-byte-path` ec31710; the M3-C build's RSS measurement is OWED — host disk full, see the landed note) Spec §3, S-E; closes I-19/I-26.
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
      **Also owed here (G-3 plan Task 6, lead, Docker):** run the loadgen at
      the new concurrency and confirm no duplicate `process_run_now`
      executions and no `procrastinate_jobs` churn from G-3's per-item
      enqueues — G-3 is functionally verified but not at rate, and this is the
      slice that makes the difference.
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
(`docs/superpowers/plans/2026-09-01-goes-g{1,2,4}-*.md`), and for G-3 and G-5
(`2026-09-02-goes-g{3,5}-*.md`), and for G-6 and G-7
(`2026-09-02-goes-g6-extractors.md`, `2026-09-02-goes-g7-worked-example.md`,
written 2026-09-02 against the spec's §15 addendum). Read the plan, not the
slice text, before implementing.

- [x] **G-1 · Anonymous S3 connections.** Spec §8. `anonymous` flag on the s3
      config; credentials optional when set (UI hides the key fields); the
      adapter signs nothing (`botocore.UNSIGNED`); new contract fixture
      `s3-connection-config.json`. Plan: `2026-09-01-goes-g1-anonymous-s3.md`.
- [x] **G-2 · Process inputs + network profile (isolated only).** Spec §3, §4.
      Staged `inputs/{batch_id}/` with `manifest.json`; platform-held assets
      granted read-only by source-collection prefix in the STS session policy,
      remote assets fetched through the matching reference-mode association's
      adapter (public GET fallback); `STAC_HIGHER_INPUT_PREFIX` /
      `STAC_HIGHER_INPUT_MANIFEST`; finalize skips `inputs/`; dispatcher batch
      entries gain `collection_id` + `op`; `runtime.network` block with
      `PROCESS_NETWORK_MAX` cap (`isolated` only at the write gate); new fixture
      `process-input-manifest.json`; ADR 0018; `docs/processes.md`. Plan:
      `2026-09-01-goes-g2-process-inputs.md`.
- [x] **G-3 · Latency posture.** Spec §5. The item-write → dispatcher hop is
      ALREADY NOTIFY-driven (2026-09-02 finding); remaining: `trigger_run` →
      immediate `process_run_now` (claim by id);
      queued-run coalescing (widen the partial unique index to all queued runs
      per `(process_id, source_id)`); `settle: "immediate"` for s3 ingest
      sources (migration 025 merges duplicates first). Plan:
      `2026-09-02-goes-g3-latency.md`. Lead runs the loadgen check (plan Task 6).
- [x] **G-4 · Tile server href mapping.** Spec §7.1. Derived image
      `infra/titiler/` wrapping both readers' `_get_asset_info` to map
      `/api/assets/{c}/{i}/{f}` → `s3://{PLATFORM_ASSET_BUCKET}/assets/{c}/{i}/{f}`;
      compose switches to it; `containers.yml` builds it; `docs/serving.md` +
      I-68 (local half closed). Plan: `2026-09-01-goes-g4-titiler-hrefmap.md`.
- [x] **G-5 · Raster preview layer on the item page.** Spec §7.2. Shared
      `RasterTileLayer` over the tile server's item TileJSON (`assets=visual`
      when present), shown when `serving_enabled` and the item `info` call
      succeeds; silent otherwise. Depends on G-4. Plan:
      `2026-09-02-goes-g5-raster-preview.md`.
- [x] **G-6 · Extractors.** Landed 2026-09-03 (`ai/goes-g6`, plan
      `2026-09-02-goes-g6-extractors.md`) — see "Discovered follow-ups" below
      for the deviations taken. Spec §6. `processes.kind` (`transform` |
      `extractor`, immutable); `metadata.strategy: "extractor"` +
      `metadata.extractor.process_id` on ingest associations (group-owned,
      kind-checked both ways); ledger status `extracting`; ITEMIZE splits at
      the `build_item` → `validate_item` seam and triggers the extractor run
      with the draft in `input_items`; finalize's extract branch validates
      id/collection/href immutability and calls the shared ITEMIZE continuation;
      run failure fails every ledger row in the batch (no fallback); defaults
      600 runs/h and 120 s; display-only `extractor` graph edge; UI (kind badge,
      association picker); loadgen `--extractor` profile. Depends on G-2, G-3.
      Plan: `2026-09-02-goes-g6-extractors.md` (spec §15 addendum records the
      deviations: association-keyed coalescing, ledger reason/run/mtime
      columns + the I-100 fix, process → collection graph edge, migration 027).
- [x] **G-7 · GOES worked example + live-gated e2e.** Spec §2, §9, §10. The
      `goes-abi-metadata` extractor and `goes-geocolor` process on the CURRENT
      runtime image (rasterio `NETCDF:` subdatasets, numpy true colour + night
      IR, GDAL COG driver, rio-stac item); `docs/processes.md` worked example;
      `app/e2e/goes-loop.spec.ts` skipped unless `E2E_LIVE_NODD=1`, scoping the
      association's `include` to the newest `ABI-L2-MCMIPC` key listed over
      plain HTTPS; `run-e2e` skill update. Depends on G-1…G-6 **and on W-1**
      (the only cross-queue dependency: without the ingest window the live
      association would list the whole product). Plan:
      `2026-09-02-goes-g7-worked-example.md` (both scripts under
      `pipeline/demo/goes/`, `pipeline.demo goes-seed`, inputs read from local
      disk — the runtime image cannot open netCDF over `/vsi`).
- [x] **G-8 · GeoColor night side: NOAA-style tint + solar-zenith blend.**
      Feedback 2026-09-04 item 4 ("the geocolor image looks black and
      white") — measured that day: a night COG is ~1 % colour pixels, a
      daytime one 57 %, so the true-colour path works and the grey is the
      night branch of `compose()` (inverted C13, no tint, `np.maximum` with
      the day side) working as spec §9 scoped it. Lead chose **tint only**
      (no city lights — that needs a static reference-asset input the
      platform lacks; ISSUES I-106). Change `pipeline/demo/goes/geocolor.py`
      `compose()` only: (1) render the inverted-C13 night layer through a
      NOAA-style ramp — warm surface deep blue, cold cloud tops white — instead
      of grey; (2) blend day and night by **solar zenith angle** per pixel
      (lat/lon from the geostationary grid via `rasterio.warp.transform` on a
      coarse grid upsampled, sun position from the scan time — no new
      dependency; a twilight band of roughly 80°–96° fading linearly) instead
      of the hard per-pixel max, so dusk fades instead of flipping. Keep the
      signature, the uint8 RGB + mask output, the float32 discipline and the
      2048 MB envelope. Tests: a synthetic day/terminator/night grid gives
      colour on the day side, blue-to-white on the night side, and a gradient
      across the terminator; the existing compose test keeps passing on its
      day-side assertions. **Live gate (lead):** re-run `goes-seed` (idempotent
      — it re-deploys the revision), wait for a night granule and a day
      granule; the night COG must exceed 30 % colour pixels by the
      2026-09-04 measure (`|r−g| > 8 or |g−b| > 8`), the day COG must stay
      within a few percent of today's. Spec §9 gets a one-paragraph addendum
      recording the ramp constants. Bounded — no plan document.
      **Merged 2026-09-04** (`ai/goes-g8`): the ramp constants and the
      `solar_zenith()` sampling are recorded in the spec's §16 addendum. The
      live gate above is NOT yet run — it needs Docker and the live bucket;
      see the follow-up below.

## P queue — pipeline graph: lineage lines + full graph view (feedback 2026-09-04)

**Read first:** `docs/superpowers/specs/2026-09-04-pipeline-graph-views-design.md`
— **approved 2026-09-04.** Written from `FEEDBACK.md` items 1–3
and the lead's three answers (both views; a line is one PRODUCT's full
lineage; in-house layered layout, no dependency). §9 lists five decisions
the agent took, approved as written. P-1 first, then P-2; P-3 and P-4 are
independent of each other. The screenshot the feedback refers to is
`docs/superpowers/specs/assets/2026-09-04-pipeline-graph-before.png`.

- [x] **P-1 · Ghost nodes in "Not wired".** ISSUES I-104. `loadGraph`'s
      collection-node union (`app/src/lib/graph/storage.ts`) takes
      `process_sources` / `process_outputs` collection ids without excluding
      soft-deleted processes, while `loadGraphEdges` does — so every e2e run
      leaves its `e2e-goes-*` collections as degree-0 orphans. Join
      `processes` and require `deleted_at IS NULL` on both branches; storage
      test with one live and one soft-deleted process; close I-104. Bounded.
- [x] **P-2 · Lineage + layout (shared, pure).** Spec §4.
      `packages/shared/src/lib/graph/{lineage,layout}.ts`: `lineage(graph,
      nodeId)` (transitive closure both ways; `extractor` edges followed
      upstream only) and `layeredLayout(graph)` (longest-path ranks, an
      extractor ranked beside the ingest connection, barycenter ordering two
      sweeps, orthogonal edge paths, total over a synthetic cycle,
      deterministic). Fixture graph = GOES + demo. Unit tests only.
- [x] **P-3 · Pipelines view.** Spec §5.1, §6. Shared `PipelineDag` SVG
      renderer over P-2's output (theme tokens, `NodeChip` visuals, links,
      edge kind on hover, extractor drawn INTO its product); `/graph` gains
      the **Pipelines | Graph** switch (`?view=`, default `pipelines`) and a
      searchable one-row-per-collection list; `LineagePanel` on the
      collection page swaps its two one-hop lists for the same row, keeping
      its 30-day strips; Storybook story; e2e against the seeded demo.
      Depends on P-2.
- [x] **P-4 · Graph view.** Spec §5.2. Full-graph `PipelineDag` with the
      alert-join health dots, click-to-highlight `lineage(node)` with an
      Open link, horizontal-scroll container, orphans row beneath; e2e (≥ 4
      edges rendered against the seeded demo). Depends on P-2; independent
      of P-3.

## X queue — built-in extractor library: stactools packages (feedback 2026-09-04)

**Read first:** `docs/superpowers/specs/2026-09-04-stactools-extractor-library-design.md`
— **approved 2026-09-04.** Written from `FEEDBACK.md` item 5 and the
lead's two answers (curated fourteen-package NOAA/public-archive set; pick
it in the Data flow form and a group-owned read-only process is created).
§3 records the facts that shape it (no uniform stactools entry point;
the §6.1 immutability rules force a MERGE; seven packages are on anonymous
buckets, seven need credentials), §12 the seven agent-taken decisions, approved as written.
**X-3 coordinates with K-1** (both touch `runtimeLimits`; base image +
variant alias compose — whichever lands first adds the other's field).
**Migration 028** for X-4 (settled 2026-09-04 — X is worked first; K-3 takes 029).

- [x] **X-1 · Registry fixture + both readers.** Spec §5.
      `tests/contract-fixtures/builtin-extractors.json` with the fourteen
      entries (`id`, `label`, `package`, `version`, `adapter`, `supports`,
      `products`, `access`, `runtime`, `extensions`); Zod loader in the app,
      Python loader in the pipeline; a CI check that
      `Dockerfile.stactools`'s pins equal the fixture's `package==version`
      pairs. Fixture README entry.
      **Merged 2026-09-04.** The set is **eleven**, not fourteen: `noaa-nwm`,
      `noaa-sst` and `hls` have never been published to PyPI (untagged repos
      only), so nothing can pin them — lead's call, **I-107**, spec §14
      addendum. The pin check is real logic (`pin_drift`, both directions)
      unit-tested against sample text; its assertion against the actual
      `Dockerfile.stactools` skips until X-2 creates that file, then arms
      itself. Packaging (how the fixture reaches each image) is left to X-2
      and X-4 on purpose — neither build context includes `tests/`.
- [x] **X-2 · stactools runtime image + wrapper + adapters.** Spec §6.
      `services/process-runtime/Dockerfile.stactools` (extends the runtime
      image; stactools + the **eleven** pinned packages the registry names —
      I-107; `python -c "import stactools.<pkg>"` smoke for every entry at
      build, the module name derived as X-1's readers derive it). **Also
      decides how the registry reaches the pipeline and the runtime image**
      (COPY, mount or env override — X-1 left it open); the pin check in
      `tests/test_builtin_extractors.py` arms itself the moment the Dockerfile
      exists; the platform module
      `stac_higher_stactools` — `run(builtin_id)` reads the ADR 0018 extract
      manifest, calls the entry's adapter, and MERGES the pystac item onto
      the draft (keep id/collection/asset keys/hrefs; copy properties,
      geometry, bbox, stac_extensions, per-asset metadata; DROP added assets
      and staged-path hrefs, logged); one adapter per package unit-tested
      against the package's own fixture file; `containers.yml` builds it.
      Depends on X-1.
      **Docker is authorised for this slice** (lead, 2026-09-04) — the
      standing "no Docker" constraint is lifted far enough to BUILD the image
      and run its import smoke, because a Dockerfile that has never been built
      proves nothing about spec §11's real risk (a stale package pinning an
      old `pystac`). Cheapest order: install the eleven pins in the pipeline's
      pytest extra FIRST and let the resolver surface conflicts in seconds,
      then write the Dockerfile, then build. Still no dev server, no e2e, no
      compose stack.
      **Merged 2026-09-04.** The resolver surfaced §11's risk in one second:
      `goes-glm` and `noaa-hrrr` cap `pystac<1.12`, `sentinel1` `~=1.9.0`,
      against the platform's 1.15.1 — but all eleven import AND build items
      on 1.15.1, so the image installs with a uv `--override` (same override
      in the pipeline's `[tool.uv]`) rather than dropping three entries, two
      of them gate-B packages — **I-109**, spec §15. Also `setuptools<81`
      (eight packages still import `pkg_resources`, removed in 81). Image
      built and smoke-tested locally: 1.18 GB vs the 525 MB base; the smoke
      is registry-driven (`python -m stac_higher_stactools.smoke` imports
      every entry's module + adapter and checks the installed version equals
      the pin). **Packaging decided**: the registry reaches the pipeline and
      runtime images as `COPY --from=fixtures` out of a named build context
      on `tests/contract-fixtures` (compose `additional_contexts`, CI
      `build-contexts`, `services/process-runtime/docker-bake.hcl` chaining
      base → variant), each image publishing its copy's path in
      `STAC_HIGHER_BUILTIN_REGISTRY`; `load_builtin_registry` falls back to
      the checkout. One file, no vendored copy — X-4 can do the same for the
      app image. Wrapper `stac_higher_stactools` (in the image, unit-tested
      from the pipeline suite via `pythonpath`): `run()` per-item failure,
      dead only when nothing landed; merge fills `datetime` from
      `start_datetime` (noaa-cdr, modis emit null) and matches produced
      assets to draft assets by staged path/basename. Adapters ×11, nine
      tested against the package's own fixture file (vendored, 1.4 MB,
      `tests/data/stactools/README.md`) through the REAL finalize gate; viirs
      and sentinel1 with doubles (no usable in-repo fixture). SAFE products
      and NAIP need the source path (reference-mode hrefs) — **I-110**.
      `containers.yml` builds both runtime images via `docker/bake-action`.
- [x] **X-3 · Image alias.** Spec §8. `runtime_image: "default" |
      "stactools"` on `runtimeLimits` (lenient Python reader — stored
      revisions lack it), `PROCESS_RUNTIME_IMAGE_STACTOOLS` resolved at launch,
      unknown alias ⇒ dead run with reason (write gate AND launch, the
      `PROCESS_NETWORK_MAX` pattern), `process-runtime.json` cases.
      `runtime.image` stays `null` for inline processes (ADR 0013 intact).
      Coordinates with K-1. Depends on X-1.
      **Merged 2026-09-04.** `runtime_image` on `runtimeLimits` (Zod enum
      `default | stactools`, default `default`; Python `RUNTIME_IMAGE_ALIASES`
      with the same default for the revisions that predate it);
      `PROCESS_RUNTIME_IMAGE_STACTOOLS` (empty ⇒ "no such image here");
      `resolve_runtime_image` in `launch.py` feeds `build_run_spec`, and
      `runner.py` dies the run by name before staging/minting when the alias's
      image is unset — an alias OUTSIDE the set never gets that far (schema at
      the form, reader as an unusable revision). Six fixture cases. K-1 was
      not yet on `ai/main`, so the K spec gained the one-line composition
      note (profile `image` = base, alias = variant) and K-1 adds `hardware`
      beside `runtime_image`. No picker: the deploy card posts `default`
      explicitly; X-4's built-in template posts `stactools`.
- [x] **X-4 · Built-in processes in the app.** Spec §7. Migration 028
      (`processes.builtin_id`, unique per live group); `GET
      /api/extractors/builtin`; `POST /api/processes/builtin` create-or-reuse
      (operator+, audited) deploying revision 1 from the two-line template
      in one transaction; the process page's read-only "Built-in" card with
      **Update to current** (the only way its revision moves; code deploy ⇒
      409); `built-in` badge; the ingest form's "Built-in" optgroup filtered
      by `supports` against the grouping rule, storing the returned
      `process_id` exactly as today. **Also decides how the registry reaches
      the APP image** — `GET /api/extractors/builtin` serves it at runtime and
      `app/Dockerfile` copies only `app/` and `packages/shared/`, so the
      fixture is not in the image today (X-1 left the choice open; X-2 makes
      the same call for the pipeline side). Depends on X-1, X-3.
      **Merged 2026-09-04.** Migration **028** (`processes.builtin_id text
      NULL` + partial unique index `(group_id, builtin_id) WHERE builtin_id IS
      NOT NULL AND deleted_at IS NULL` — the create-or-reuse arbiter);
      `GET /api/extractors/builtin` (member+); `POST /api/processes/builtin`
      `{builtin_id, group_id}` → 200 the group's live process or 201 a new
      one — `kind: extractor`, named from the label, 600 runs/h, revision 1
      from the template (`from stac_higher_stactools import run` /
      `run("<id>")`, `runtime_image: "stactools"`, the entry's memory/timeout/
      network) in ONE transaction; two racing picks converge on the index
      winner; a NAME collision with a hand-written process is a 409 (I-111).
      **Update to current** = `POST …/revisions {from_builtin: true}` on a
      built-in process (same deploy verb + audit row); a code deploy on one is
      a 409, `from_builtin` on a hand-written one a 400, a registry id that
      vanished a 409 naming the drift. UI: the process page swaps the code
      editor for the read-only **Built-in** card (label, package==version,
      image `stactools`, products, the two-line body, Update to current);
      `built-in` badge on the list and the page; the ingest form's picker
      gains a **Built-in** group (registry entries the group has NOT yet
      instantiated, filtered by `supports` vs the grouping rule — `grouped` ⇔
      `shared_basename`, `single_file` ⇔ `none`) beside "Your extractors"
      (group processes, built-ins suffixed `· built-in`); picking one calls
      create-or-reuse in the connection's group and stores the returned
      `process_id` exactly as before. **APP packaging decided**: the registry
      is imported at BUILD time (`app/src/lib/extractors/registry.ts` →
      `tests/contract-fixtures/builtin-extractors.json`, bundled by Vite);
      `app/Dockerfile` `COPY --from=fixtures` places it where the import
      resolves before `npm run build` and `containers.yml` supplies the same
      `fixtures` context the pipeline and runtime images use.
- [ ] **X-5 · Live gates (LEAD ONLY, Docker + internet).** Spec §10. Gate A:
      switch the standing demo's `goes-abi-mcmipc` association to built-in
      `stactools-goes`; the next granule must match the hand-written
      extractor's scan-time `datetime` to the second, overlap its footprint
      ≥ 95 % IoU, and carry `platform` + the `goes:*` fields. Gate B: one
      granule each through `goes-glm`, `noaa-hrrr`, `noaa-mrms-qpe` and
      `noaa-cdr` from their public buckets, recorded as a table (`noaa-nwm`
      and `noaa-sst` are not in the registry — I-107). ISSUES entry for the
      credentialed six (I-105). Depends on
      X-2, X-4.

## K queue — process compute: Kubernetes + Kueue + hardware profiles (AFTER G-6/G-7)

**Read first:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md`
— **approved 2026-09-04.** ADR 0019 (accepted) records the decision: GPU/CUDA
processes need hardware the Fargate backend ADR 0013 recommended cannot
provide, so the cloud backend is **Kubernetes Jobs admitted by Kueue**,
operators pick a **hardware profile** in the UI, and the executor becomes
**submit-then-reconcile** (today `execute_run` blocks the worker's event loop
for the whole run at worker concurrency 1). Spec §13 lists eight decisions
the agent took without a lead answer, approved as written.

Sequencing: the K queue is worked by the process agent after G-6/G-7 (same
files). **G-6 and G-7 are both done** and the spec is approved — K-1 may start;
nothing in the K queue is waiting on the G queue. K-4 changes the worker's job model and must **coordinate with M3-D**
(the later merge into `ai/main` resolves; neither queue waits). K-7 is
independent and may run in parallel with anything. K-8/K-9 are Phase 8 work
and need a cloud account — lead-gated.

- [ ] **K-1 · Hardware-profile contract + runtime `hardware` block.** Spec
      §3, §4. New fixture `hardware-profiles.json` + a loader in both
      runtimes (`PROCESS_HARDWARE_PROFILES_FILE`; in-repo default sets under
      `infra/hardware-profiles/`); `runtimeLimits` in
      `app/src/lib/processes/schemas.ts` gains `hardware {profile, cpu,
      gpu_count}` (absent ⇒ `standard`, lenient reader on the Python side —
      every stored revision lacks it); `ProcessRuntime` gains the flattened
      fields; write-gate AND launch-time bounds checks (the
      `PROCESS_NETWORK_MAX` dual-enforcement pattern; unknown profile ⇒ dead
      run); `RunSpec` gains `cpu`, `gpu_count`, `profile`, `priority`;
      `GET /api/processes/hardware-profiles` (member+, `backend` block
      stripped). `process-runtime.json` gains cases. No executor change.
- [ ] **K-2 · Hardware picker in the UI.** Spec §9. `CodeCard` gains a
      Hardware fieldset: profile `<select>` grouped by tier, description +
      accelerator badge, CPU/memory inputs bounded by the profile (replacing
      the hard-coded `128`/`86400` literals), GPU count only when the profile
      has one, a one-line summary. Sync the current revision's `hardware`,
      `memory_mb` and `timeout_seconds` into the form (today only `code`/`env`
      are — every deploy form starts at 512/900). Run rows show the pinned
      hardware. `docs/processes.md` gains a "Hardware" section. Depends on K-1.
- [ ] **K-3 · DockerExecutor honours the profile.** Spec §8. `NanoCpus` from
      `cpu`; `DeviceRequests` from the profile's docker block (only when the
      host exposes an NVIDIA runtime — Docker Desktop on macOS has no GPU);
      per-profile `capacity`: the claim path counts `running` rows on the
      profile and, when full, releases the run back to `queued` with
      `phase = pending_capacity` and `next_attempt_at = now + 15 s`, no
      attempt spent. Migration **029** (026 taken by W-2, 027 by G-6's
      extractors, 028 by X-4's `builtin_id`) lands the `executor_backend`,
      `executor_handle`, `phase`, `phase_detail`, `submitted_at`,
      `cancel_requested_at` columns (the `cancelled` status waits for K-4).
      Depends on K-1.
- [ ] **K-4 · Submit-then-reconcile executor + cancel.** Spec §5. New ABC
      (`submit / status / cancel / logs / reap / list_launched`, optional
      `watch()`); `wait` removed; the Docker backend's `watch()` off the
      Engine `/events` stream filtered by the run-id label; the **run
      watcher** singleton asyncio task in the worker (dispatcher-listener
      pattern) applies events; the 1-minute run tick reconciles every
      `running` row via `status()`; **credential-expiry requeue** (mint for
      `max_queue_wait + timeout + grace`; a still-pending run with less than
      `timeout + grace` left is cancelled and requeued without spending an
      attempt); `cancelled` status + `POST /api/processes/[id]/runs/[runId]/
      cancel` (operator+, audited `cancel`) + the UI button and badge
      (closes I-81; ADR 0016's `dismiss` gap closes with it). Verify: a
      worker restart mid-run and the run still finalizes. **Coordinate with
      M3-D.** Depends on K-3.
- [ ] **K-5 · KubernetesExecutor + `build_executor` + `/health` executor
      key.** Spec §6. `PROCESS_EXECUTOR=docker|kubernetes` through a factory
      replacing the two inline `DockerExecutor(...)` constructions in
      `jobs/process.py`; one Job + one Secret per run in `stac-higher-runs`
      (labels `stac-higher.run-id`, `kueue.x-k8s.io/queue-name`,
      `kueue.x-k8s.io/priority-class`; `backoffLimit: 0`,
      `activeDeadlineSeconds = timeout`, requests = limits, no SA token,
      non-root, `RuntimeDefault`, read-only root); `status()` off the Job +
      the Kueue Workload (`QuotaReserved` reason/message → `pending_capacity`
      detail); `watch()` = Job + Workload watches; `logs` via `pods/log`;
      `cancel` = foreground delete after logs; `list_launched` raises
      `ExecutorUnavailable` on any API error. RBAC manifests under
      `infra/kubernetes/`. Unit tests against a fake API server. Depends on
      K-4.
- [ ] **K-6 · Kueue manifests + kind CI leg.** Spec §7, §8. `infra/kueue/`:
      a ResourceFlavor per profile family, ClusterQueue `process-runs`
      (BestEffortFIFO, `withinClusterQueue: LowerPriority` preemption),
      LocalQueue, `WorkloadPriorityClass` `interactive` > `triggered`,
      `waitForPodsReady`, the ValidatingAdmissionPolicy rejecting label-less
      Jobs; `infra/hardware-profiles/kind.json` (CPU flavors, a 1-CPU quota
      so `pending_capacity` is exercised); a CI job that creates a kind
      cluster, installs Kueue (server-side apply) and runs the executor's
      integration tests. Depends on K-5.
- [ ] **K-7 · CUDA runtime image.** Spec §10. `services/process-runtime/
      Dockerfile.cuda` (CUDA 13 runtime base, Python 3.12, the blessed raster
      stack, **cupy + numba — no PyTorch**), built in `containers.yml`,
      selected by a profile's `image`; a cupy smoke process in
      `pipeline.demo`. Independent.
- [ ] **K-8 · EKS deployment (Phase 8, LEAD-GATED).** Spec §11. Auto Mode
      cluster, one NodePool per GPU flavor (`eks.amazonaws.com/instance-gpu-
      name`, taint `nvidia.com/gpu`, **limits = Kueue quota**), ECR mirrors,
      `infra/hardware-profiles/eks.json`, NetworkPolicy, pipeline
      ServiceAccount + Pod Identity. Depends on K-5, K-6, K-7.
- [ ] **K-9 · Gate rehearsal (LEAD ONLY).** Spec §2. Local leg (compose,
      capacity 1, cancel, worker restart) and cluster leg (`gpu-l4` cupy run,
      visible wait for a `g6` node, quota wait, `interactive` admitted
      first). Record queue-wait and cold-start numbers M-gate style in
      ROADMAP §9; close I-61's cloud half; measure I-93.

## W queue — ingest date window + retention cap (unblocks a real NODD association)

**Read first:** `docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md`
— **approved 2026-09-02.** Measured that day: a single GOES product
on `noaa-goes19` is ~250,000 objects / ~12 TB, and NOTHING in the ingest path
bounds a listing, a fetch, or what is kept. G-3 made it more eager still (s3
sources settle on first sight). This queue adds the three knobs that make
pointing an association at a public archive a safe thing to do, which G-7's
live e2e needs and the GOES loop spec §5 flagged as an unsettled gap.

The two slices are independent and may run in parallel in separate worktrees.
Both have detailed plans. **W-2 took migration 026**; K-3's spec and its
slice text above were renumbered to 027 as part of W-2 Task 1, and then to
**028** once G-6 (G queue) took 027 for its own migration, and to **029** on
2026-09-04 when the X queue, worked first, took 028.

- [x] **W-1 · Ingest date window + prefix expansion + per-poll cap.** Spec §3.
      New pure `ingest/window.py` (bound grammar `-<n>[smhd]` or RFC3339,
      window resolution against a supplied `now`, `{Y}{m}{d}{j}{H}` template
      expansion with a `max_prefixes` refusal); `window` / `path_template` /
      `max_files_per_poll` on the ingest config (cross-runtime — Zod, the
      Python reader and `ingest-config.json` together, all three optional so
      existing associations are untouched); DISCOVER lists per expanded prefix,
      filters on `FileEntry.mtime`, and admits at most N new files per tick
      oldest-first; new counters `out_of_window` / `undateable` /
      `deferred_by_cap` / `prefixes_listed`; the fields in the Data flow form.
      Plan: `2026-09-02-w1-ingest-window.md`.
- [x] **W-2 · Retention count cap.** Spec §4. Migration **026** adds
      `collection_settings.retention_max_items` (nullable, `>= 1`);
      `list_gc_collections` widens its predicate and `list_expired_items` gains
      a UNIONed "beyond the newest N by `datetime DESC, id DESC`" branch —
      `archived` still overrides and ignores the cap; the existing `retention`
      reason, the mark-then-collect ordering and the grace window are all
      unchanged (ADR 0011 is not amended). Write path through the settings
      schema, the impact dry-run (`?retention_max_items=N`, DISTINCT across
      both rules) and a Maximum-items control on the Settings tab. Renumbers
      K-3 to migration 027 (later 028 — G-6 took 027). Plan:
      `2026-09-02-w2-retention-cap.md`.

## V queue — map page: products as map layers on one time axis (feedback 2026-09-04)

**Read first:** `docs/superpowers/specs/2026-09-04-map-page-design.md`
— **approved 2026-09-04.** A `/map` page (sidebar, operate group) where
built-in-catalog products are added as layers: item footprints (GeoJSON,
any collection), imagery through titiler-pgstac's collection mosaic with
`datetime` pinned (the collection preview's approach, commit `935ebf7` —
NOT search registration), and tipg vector tiles. Every time-aware layer
keeps its own frames; the page's time axis is their union, resolved
hold-last per layer; playback is the preview's tiler-paced rule. V-1
extracts the preview tab's frame machinery into shared pieces and makes the
tab consume them — the preview's four test files must stay green unchanged.
No migrations. The tilers are still called straight from the browser
(I-1/I-69 unchanged).

**Task plans (written 2026-09-07):** V-2 →
`docs/superpowers/plans/2026-09-07-v2-map-page-shell.md`; V-3 →
`docs/superpowers/plans/2026-09-07-v3-map-imagery-time-axis.md`; V-4 has
none yet (the loop writes it after V-2 merges). V-1's plan:
`2026-09-04-v1-shared-map-pieces.md`.

- [x] **V-1 · Shared pieces + preview refactor.** Spec §3. `RasterFrameStack`
      (previous + current + one lookahead, opacity swap, no transition) in
      `packages/shared/src/components/map/`; `FootprintLayer` gains `id` /
      `opacity` / `beforeId` and `lib/map/styles.ts` becomes functions of the
      source id; new `VectorTileLayer` (fill + line + circle on one MVT
      source); `tipgBaseUrl` + `tipgCollectionsUrl` + `tipgTileJsonUrl` in
      `serving/urls.ts` replacing the two inline `PUBLIC_TIPG_URL` reads;
      `CollectionPreviewTab` mounts `RasterFrameStack`; stories for both
      new components. Goes first.
- [x] **V-2 · Page shell, state, footprint layers.** (merged 2026-09-08) Spec §4.1, §4.2, §4.3
      (footprints), §4.5 (list + Products/Footprints), §4.6, §4.7.
      `map.astro` + `MapPage` island, sidebar entry + top-bar title, the
      `lib/map/state.ts` reducer + tests, layer list (visibility, opacity,
      move, remove), Add-layer popover, hover tooltip + click-through,
      first-add camera fit, `MapPage` component tests, `e2e/map.spec.ts`
      smoke (pgstac only). Depends on V-1. **Build spec §11.1–11.2 first**
      (`visible` prop on the three layer components; the stack's
      `${id}-anchor` layer + `rasterFrameStackAnchorId`), plus the single
      opacity clamp in `styles.ts`.
- [x] **V-3 · Imagery layers + the shared time axis.** (merged 2026-09-14) Spec §4.3 (imagery),
      §4.4. `lib/map/axis.ts` (`buildAxis`, `resolveLayerFrame`) + tests;
      the Imagery option gated on serving + a newest-items probe; per-row
      asset select; the docked `TimeSlider` with `areTilesLoaded` pacing;
      span select resets the axis. Live check on the standing GOES demo
      (`goes-geocolor` imagery + `goes-abi-mcmipc` footprints, 50 frames).
      Depends on V-2. Fixes **I-112** per spec §11.3 (chained `beforeId`
      inside `RasterFrameStack`).
- [x] **V-4 · tipg vector layers + docs.** (merged 2026-09-14, `ai/v4-vector` 2e1896a) Spec §4.5 Vector tiles section,
      §7, §8. `useTipgCollections` (silent on failure), `VectorTileLayer`
      on the page with the source-layer name verified against the running
      tipg, `docs/FEATURES.md` + `docs/serving.md`, the §8 deferrals logged
      in `docs/ISSUES.md` / follow-ups. Depends on V-2 (not V-3).

**Carried forward from the V-1 final review (2026-09-04)** — constraints the
remaining slices must honour; they lived only in the review ledger until now:

- **V-2:** the layer-row opacity control clamps to 0..1 BEFORE the value
  reaches `footprintLayers` / `vectorTileLayers` / `RasterFrameStack` — none
  of them clamp, and maplibre rejects an opacity above 1 as a style error.
  One `clamp01` in `packages/shared/src/lib/map/styles.ts` used by all three
  is the cheapest shape. Decide the `beforeId` anchoring scheme BEFORE
  writing the chaining code: `RasterFrameStack`'s frame layer ids
  (`${id}-frame-${n}-layer`) come and go every step, and react-map-gl's
  `addLayer` throws on a `beforeId` naming a layer that is not there (the
  pinned `ItemDetailView` regression). Give each stack a stable anchor layer
  (an always-mounted `${id}-anchor`, or one spacer layer per list slot) and
  chain only on stable ids — never on a frame layer.
- **V-3:** `resolveLayerFrame` must never hand a stack an out-of-range index
  — a negative index throws (`frames[-1].key`), an index ≥ count renders
  every frame at opacity 0 and reads as "the tiler is down". (`RasterFrameStack` has been total since V-1's fix wave `a575c91` — any index wraps into the series, tests included — so this is belt and braces.) Fix the frame DRAW ORDER on a
  backwards step: react-map-gl only calls `moveLayer` when a layer's
  `beforeId` prop changes, so after stepping 0→1→2→1 the still-mounted frame
  2 sits above frame 1 in maplibre's order and, both being opaque, paints
  over it. Pre-existing in the preview tab (forward playback is always
  correct, which is why the live GOES check never saw it); routine once a
  scrubbable shared axis drives several stacks. The robust fix chains
  `beforeId` inside the stack so a reorder triggers `moveLayer`; that
  inverts the child order `collection-preview-tab.test.tsx`'s "keeps the
  previous frame painted" assertion reads — **the lead permits that
  assertion to change in V-3** (the behaviour it protects, two opaque
  frames with the current one on top, stays). Keep the `hintSettled` gate
  from §4.3: react-map-gl's source update applies only one changed key per
  render and warns on the rest.
- **V-4:** delete the now-consumerless `footprintFillLayer` /
  `footprintLineLayer` exports and the stale `app/src/lib/map/styles.ts`
  proxy (or sync it); add an ids-only `vectorTileLayerIds` beside
  `vectorTileLayers` for `interactiveLayerIds` / anchors; assert the
  default-opacity paint values (fill 0.2, line 1) in
  `vector-tile-layer.test.tsx`. (The `FootprintLayer` `useMemo` landed in V-1's fix wave `a575c91`.)

## D queue — item lineage: `derived_from` links on process outputs (lead question 2026-09-06)

**Read first:** `services/pipeline/src/pipeline/finalize/process_run.py`
(the process-run producer hooks), `finalize/steps.py` (`_rewrite_document`
is the only in-memory edit the neutral steps make today), `process/repo.py`
(`process_runs.input_items` — the run's triggering refs), and
`docs/processes.md` "Publishing outputs". No design spec: the question was
"should the processor write `derived_from`, or can the platform apply it?"
and the answer below IS the design. **Decision: both — the platform stamps a
batch-level default at finalize, and a processor that knows the real fan-in
writes its own links and the platform leaves them alone.**

Why the platform can do it: every run row already carries its triggering
items (`input_items`), and the process-run finalize hook sees both that list
and every output document before the pgstac upsert. So the lineage exists at
exactly one seam and costs the author nothing. Why it stays a *default*: runs
coalesce per `(process_id, source_id)`, so a batch may hold several inputs
and the outputs come back as a flat set of documents — the platform knows
the SET of inputs and the SET of outputs, not which came from which. One-in /
one-out and many-in / one-out (the common cases) are exactly right under
"every output ← every input in the batch"; many-in / many-out is not, and
that is the case the processor must handle itself (the manifest already
tells it every input's `collection` + `item.id`, so it can).

- [x] **D-1 · Stamp `derived_from` at finalize, processor override, docs.** (merged 2026-09-07; **live check PASSED 2026-09-08** — lead)
      Live check: the running pipeline image predated the D-1 merge, so it was
      rebuilt and redeployed (`docker compose build pipeline && up -d pipeline`)
      before checking — the standing demo had been producing unstamped outputs
      until then. On the first post-deploy scene
      (`…s20262510416177…-geocolor`, 04:16:17Z),
      `GET :8081/collections/goes-geocolor/items?limit=1` returned a
      `rel: "derived_from"` link to
      `/collections/goes-abi-mcmipc/items/OR_ABI-L2-MCMIPC-M6_G19_s20262510416177_e20262510418550_c20262510419051`.
      **pgstac passes the link through the upsert unchanged** — no stripping.
      In `ProcessRunResolver.resolve` (or a step-side hook — keep the neutral
      `steps.py` producer-free per ADR 0014; the resolver is the producer's
      place), after a document resolves: if it has **no** link with
      `rel: "derived_from"`, append one per triggering ref in the run's
      `input_items`, `type: "application/geo+json"`, href
      `{base}/collections/{collection_id}/items/{item_id}`. The resolver
      only has `provenance.run_id` today — carry the refs in via the
      request (`build_process_request` gains the run's `input_items`, the
      call site already holds the run row) rather than re-reading the row
      in the resolver. Skip refs the input planner recorded as `not_found`
      (`manifest.skipped`) — an item that vanished before the run must not
      be linked. If the document ALREADY carries any `derived_from` link,
      leave the whole link set untouched: that is the override, and it is
      the contract for many-in / many-out. Href base: a new pipeline
      setting `CATALOG_HREF_BASE` (default `/` → root-relative
      `/collections/…/items/…`, mirroring `ASSET_HREF_BASE`'s
      root-relative default; an absolute value produces absolute hrefs).
      Do NOT invent a run link (`rel: "via"` or similar) — the run id is
      already on the ledger (`output_items`), which is the run-level
      provenance. Scope guards: **extractor runs get nothing** (an
      extractor builds an item from a file, not from another item;
      `finalize/extract_run.py` unchanged); **cron-triggered runs get
      nothing** (`input_items` is empty by construction); an output item
      that is also one of the inputs (an in-place update) must not link to
      itself. Tests in `tests/test_process_finalize.py`: stamped once per
      input, override respected verbatim, skipped ref excluded, self-link
      excluded, extract branch untouched, and the ADR 0014 check
      (`test_the_real_process_hooks_route_through_the_unchanged_steps`)
      still green. Docs: `docs/processes.md` "Publishing outputs" gains a
      "Lineage" paragraph — the default, when to write your own, and the
      manifest fields to compute it from; `docs/FEATURES.md` one line;
      `docker-compose.yml` pipeline env for the new setting (beside `ASSET_HREF_BASE`). **Live check
      (lead, Docker):** confirm the upsert path keeps the link — stac-
      fastapi-pgstac regenerates `self`/`root`/`parent`/`collection`/`item`
      and is expected to pass every other rel through, but that is a
      belief until an item on the standing GOES demo shows it in
      `GET /collections/goes-geocolor/items/{id}`. If pgstac strips it, the
      slice is not done — log it and stop. Delivery is unaffected by design
      (destinations receive the document as published, link included).

- [x] **D-2 · Render `derived_from` on the item page.** (merged 2026-09-07; **live click-through PASSED 2026-09-08** — lead, with D-1's)
      Live check: on the item page for the 04:16:17Z geocolor scene the
      Properties tab showed the "Derived from" block with the
      `goes-abi-mcmipc / OR_ABI-…c20262510419051` chip; clicking it navigated to
      `/collections/goes-abi-mcmipc/items/OR_ABI-…c20262510419051` and rendered
      the source item. Decisions settled
      with the lead 2026-09-07: a **"Derived from" block on the Properties
      tab** (above the properties table, beside the extension badges;
      nothing rendered when the item has no such link — no empty state);
      **upstream only**; **both surfaces** (product item page AND the
      catalog browser); **id + collection parsed from the href, no fetch**
      of the linked item. `ItemDetailView` is catalog-agnostic by design
      (product page and `BrowseItemPage` render the same view and must
      not drift) — keep it so: the view gains an optional
      `resolveLink?: (link: StacLink) => LinkTarget` prop and renders one
      chip per `derived_from` link; `ItemDetail` and `BrowseItemPage` pass
      a resolver built from their own catalog context. The resolver is a
      pure helper in `app/src/lib/browse/paths.ts` beside `itemHref`:
      parse `{collection, item}` out of an href that is either
      root-relative `/collections/{c}/items/{i}` (D-1's default output —
      on the product page this is the page's own catalog) or absolute
      `{catalogUrl}/collections/{c}/items/{i}` for a catalog in
      `$catalogs` (match by `normalizeCatalogUrl` prefix; the page's own
      catalog first, then the others), then hand `itemHref(catalog, c, i)`
      the match so the built-in catalog lands on the product page and any
      other on its browse page with `?src=` (UI-15). An href that matches
      no catalog renders as a plain external anchor (`target=_blank`,
      `rel=noopener noreferrer`) showing the link's `title` or the href's
      last two path segments; never a dead in-app route. Chip text is
      `{collection} / {item}`; the link `title` wins when present. Tests:
      `src/__tests__/browse-paths.test.ts` for the parser (root-relative,
      absolute matching the page catalog, absolute matching ANOTHER
      configured catalog, absolute matching nothing, encoded ids, trailing
      slash on the catalog URL) and a component test beside
      `item-detail-preview.test.tsx` (no links → no block; two links →
      two chips with the expected hrefs; external href → anchor).
      Independent of D-1 at build time — a hand-written `derived_from`
      link on any item exercises it — but the **live check** is the
      D-1 output on the standing GOES demo: open a `goes-geocolor` item,
      click the chip, land on the `goes-abi-mcmipc` source item. Docs:
      `docs/FEATURES.md` one line beside D-1's. Depends on nothing;
      D-1 and D-2 may run in parallel.

**Not in this queue:** downstream lineage (items derived from THIS one —
a STAC API cannot search by link, so it needs an app route over the
`process_runs` ledger and is built-in-only); a general Links tab; and
per-output lineage for many-in / many-out batches, which is the
processor's job by the decision above and stays so.

---

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

- **M3-C landed 2026-09-14 (`ai/m3-c-byte-path`, merge ec31710) — merged and gated, measurement HALF done.**
  EXTRACT opens rasters in place through `MemberByteSource.locate()` →
  `RasterLocation` + `open_raster` (`ingest/raster_io.py`, the single
  `rasterio.Env` builder; `GDAL_CACHEMAX` is integer MB, converted to bytes
  there); `S3Adapter.gdal_location/copy_source/open` and the platform
  client's `PlatformS3Access`/`raster_location` (credentials sealed in an
  `AWSSession`, pinned endpoints, `repr` guards); FETCH server-side-copies
  when `can_server_side_copy` allows (ledger `checksum=NULL`, by design) else
  streams a bounded multipart upload through `HashingStream`
  (`max_in_memory_upload_chunks` pinned to `FETCH_TRANSFER_CONCURRENCY`, so
  the real bound is ≈ (concurrency + 1) × chunk ≈ 40 MiB, not 32), falling
  back on copy failure; `pipeline_ingest_fetch_transfers_total{mode}` +
  `transfer: {mode: count}` on the group-done log. Gates on `ai/main`: pytest
  1181 passed / ruff clean; `npm run verify` reached 604 tests and then died
  on ENOSPC (host disk full) — RE-RUN IT. **Review-driven deviations:** GDAL
  cache MB→bytes at the coercion point (rasterio's int path is bytes); the
  s3transfer in-memory chunk bound; two tests monkeypatch the pin (live DNS);
  `_best_effort_raster_geometry` swallows a raising `locate()` while
  `build_item` lets it raise (by design); a final-review fix wave whose
  `HashingStream.read(-1)` guard was a Critical regression (s3transfer's
  single-part path calls `read()` with no argument below the threshold) —
  corrected in round 2 with a botocore-Stubber test through a real client.
  **Measured (baseline, `ai/main` BEFORE the merge, label `m3c-base2`, fresh
  pipeline process, 60 × 64 MB `raster_auto` rasters, `--rate 0`):** RSS
  70.5 → **205.5 MiB peak** (+135 MiB), `ingest_fetch` mean **0.359 s**,
  `ingest_itemize` mean **0.063 s**, 60 items catalogued, torn down. (A first
  baseline against the 12-hour-old process read 584 → 649 MiB — the
  high-water mark hid the transient; restart before measuring.) **OWED (lead,
  Docker):** the same run on the M3-C build (`scratchpad m3c-measure.sh m3c`,
  restart the pipeline first): peak RSS lower and FLAT, `mode="copy"` ≈ 60
  and `copy_fallback` = 0, itemize mean ≤ 0.063 s, one item's `raster:bands`
  statistics identical to the baseline item (`m3c-base2.item.json` in the
  session scratchpad; otherwise re-derive from a baseline run). The image
  build failed on the full disk; the deployed pipeline is still the
  pre-M3-C image (built 03:25Z). **Deferred minors (final review, logged not
  fixed):** `gdal_session_options` lives in `storage/platform` (raster_io is
  the neutral home); no test asserts the counter's label strings; the
  counter increments before the ledger write; `S3Adapter.open` is typed
  `BinaryIO` for a non-seekable body; anonymous connections pay one failed
  copy + WARNING per member before falling back (I-128); reference-mode
  `locate()` resolves the pin on the event loop; the itemize job's
  `RasterAccess` wiring is untested; s3transfer raises `multipart_chunksize`
  below S3's 5 MiB floor (README note owed); the WARNING close path is
  unasserted. `config.py`'s RSS comment corrected in this commit.
- **2026-09-14 HALT (lead):** the host disk filled (143 MiB free of 926 GiB;
  Docker Desktop's 1 TB sparse `Docker.raw` holds 142 GB and could not grow),
  Docker's VM remounted read-only, the database container stopped and the
  STAC API returned no features. Nothing in the stack was touched. Resume:
  free host space, restart Docker Desktop, `docker compose up -d --wait`,
  canary, then the owed M3-C measurement, the K-1 Task 3 review, M3-D Task 1.
- **V-4 landed 2026-09-14 (`ai/v4-vector`, merge 2e1896a), live-checked.**
  `useTipgCollections` (quiet, `enabled` only while the picker is open) lists
  tipg's `/collections`; the Add-layer popover's "Vector tiles" section adds a
  `vector` layer (no camera fit, no frames, not hoverable) that `MapLayerView`
  — now a hook-free dispatcher (`StacLayerView` / `VectorLayerView`) — draws
  through the shared `VectorTileLayer` from `tipgTileJsonUrl(id)` (source
  layer `default`); `layerAnchorId` chains a STAC layer's `beforeId` to
  `vectorTileLayerIds(id).fill`; the V-1 carry-forwards landed (the
  consumerless `footprintFillLayer`/`footprintLineLayer` exports and the stale
  `app/src/lib/map/styles.ts` proxy are gone). Gates: verify 1505 tests; whole
  e2e 46 passed / 1 skipped (baseline). **Deviations, all review-driven:**
  Added-state variant is `ghost` (matches `AddLayerCollectionRow`, brief said
  `secondary`); the `map-page.test.tsx` react-map-gl mock became a
  `forwardRef` + `onLoad` spy so the no-camera-fit assertion is live (with a
  positive control on a footprints first-add); the section body renders
  nothing while the listing is loading (the final review caught a
  "no vector tiles published" flash during the pending fetch); the §8
  deferrals are **I-126** (the plan's I-122 collides with the peer branch
  `ai/c-images-spec`, which holds I-122–I-125); the serving.md paragraph
  moved below the consumer list it names. **Live check (Chrome, standing
  stack):** the section lists tipg's six `public.*` function collections;
  adding `public.st_hexagongrid` shows the hexagon layer row, fetches the
  TileJSON (200) and draws nothing — every tile answers 422 `Missing Required
  parameters … size`, maplibre marks the source errored and stops requesting
  (no error UI, spec §4.7); of the six, only `public.postgis_srs_all` serves a
  200 tile (5 MB, no geometry) and `public.st_subdivide` 500s — so nothing in
  today's tipg can draw (I-126's seeded-table bullet stands). With tipg
  stopped the picker shows "no vector tiles published" — but only with a
  cold HTTP cache: tipg answers `Cache-Control: public, max-age=3600`, so a
  browser that listed within the hour keeps showing the stale list (I-127).
  **Deferred minors (final review, logged not fixed):** a shared Source/Layer
  test-mock helper (verbatim in four test files); read `vector_layers[0].id`
  from the TileJSON instead of hardcoding `default`; a page test for a STAC
  layer chained beneath a vector layer; `fetchTipgCollections` ignores the
  query's AbortSignal; a `max-h` cap on the popover now that it has two
  sections; `StacLayerView`'s implicit `undefined` return for a future kind.
- **2026-09-14 session note (lead):** the canary was 8.5 h stale at 14:28Z
  after the host Mac spent the night in maintenance-sleep cycles (`pmset -g
  log`: DarkWake ~5 s every 15 min, 22:00–08:27 MDT); the pipeline log shows
  one "extractor run died; its ingest batch is failed" per wake and 277
  `ingest_files` rows in `failed` (the recovery sweep requeues them); the loop
  resumed unaided at 14:28Z and the canary was fresh (13:41Z frame) by
  14:30Z. Not a pipeline defect; no rows touched. A Sonnet session rate limit
  (resets 02:00 MDT) killed three subagents at 22:04 MDT; all were
  re-dispatched/resumed 08:28 MDT.
- **M3-B landed 2026-09-14 (`ai/m3-b-pool`, merge 3abdc70), measured.**
  `pipeline/db/pool.py`: one `psycopg_pool.AsyncConnectionPool` per DSN,
  process-wide, lazily opened on first use under a module lock, sized by
  `DB_POOL_MIN`/`DB_POOL_MAX` (2/16), `configure=configure_pgstac_session_async`
  (ADR 0020). Twelve repos + two one-offs (`jobs/process.py` `_resolve`,
  `pgstac_writer.get_collection_bbox`) check out of it; the four exemptions
  (Procrastinate's connector, the dispatch LISTEN connection,
  `setup()`/`check_connection()`, the pgstac queue drainer) stay direct.
  `main.run()` closes the async pool, the writer pool and the queue in that
  order, each isolated (`except Exception`, so a `CancelledError` still
  propagates); `/health` gained `db_pool` keyed `host:port/dbname`.
  **Deviations from the plan, all review-driven:** (1) the plan's
  `pool.open()` (psycopg_pool default `wait=False`) let the min-size fill race
  the first checkout — the DB-gated test saw 3 backends for 5 sequential
  checkouts — so the pool opens with `wait=True`, bounded by
  `POOL_OPEN_TIMEOUT_SECONDS = 10` (not psycopg_pool's 30 s: the lock is
  module-global and N callers would queue N×30 s against a dead database) and
  closes itself on any failure/cancellation before registration; a broken
  `configure` hook now surfaces as one `PoolTimeout` at first use with the
  cause in psycopg_pool's WARNING log. (2) `run()`'s `try` was widened to wrap
  `queue.setup()` so a schema-apply failure releases the already-open
  Procrastinate connector (a real pre-existing leak). (3) The DB-gated
  session-delta assertion is a tolerance (20 calls, `< 10` new sessions), not
  an exact count — the shared stack's other clients open sessions too.
  **Measured (label `m3b`, 900 items at 30/s, copy/`defaults_only`, watch
  overlapping the feed, torn down):** `pg_stat_database.sessions` **19.5 →
  0.10 new sessions per item** (17,570 → 92 for 900 items); `/health
  db_pool` at the end: `pool_size 2`, `connections_num 2`, `requests_num
  17,895` (~19.9 checkouts per item — S-D's "~14" plus the periodic ticks),
  `requests_waiting 0`; `ingest_fetch` 24 → 12 ms, `ingest_itemize` 32 → 9 ms;
  the pooled pipeline kept pace with the 30/s feed (all 900 itemized inside
  the feed window) where the baseline lagged at 21–24/s; `pgstac_queue_drain`
  0.096 → 0.131 s at 5 partitions; pgstac queue flat at 0. Deferred minors
  (logged, not fixed): no negative caching of a failed warm-up (N callers
  still serialize N×10 s against a dead DB — documented trade); the
  `except BaseException` close path has no unit test; `flow/repo.py` and
  `delivery/repo.py` carry module-level "short-lived connection" docstrings
  in different wording that are now stale; `pool_stats()` iterates the
  registry unsnapshotted (safe on one loop); the `/health` sample omits
  nothing now but `version` was only just added.
- **V-3 landed 2026-09-14 (`ai/v3-map-axis`, merge f2923cd), live-checked.**
  Six tasks as planned plus one final fix wave: `lib/map/axis.ts`
  (`buildAxis` / `resolveLayerFrame`, hold-last, binary search, never an
  out-of-range index); the I-112 chain inside `RasterFrameStack` (frames
  rendered top-first, the anchor last; the preview-tab assertion inverted as
  the lead permitted); `useLayerData` (one items query per layer, the same
  key as the Preview tab); the Imagery option gated on serving + a five-item
  tileable probe with `AddLayerCollectionRow` extracted so the two probe hooks
  are legal; `MapLayerView` (footprints per frame, imagery as a stack, `null`
  tick draws nothing); the docked `TimeBar` with the shared
  `FRAME_MAX_WAIT_TICKS` cap and the span select resetting to the newest
  tick. Review rounds fixed brief defects: the asset-change dispatch was
  untested (now a real Radix Select interaction + a page-level test), and
  `data-testid="map-layer-icon-<kind>"` was added to the row icon. Issues
  opened: **I-119** (positional `axisIndex` moves the parked tick when the
  axis reshapes), **I-120** (N settings + N items probes per popover open),
  **I-121** (basemap swap lags the theme toggle by ~8–10 s). **Live check
  (lead, Chrome, standing demo, 2026-09-14):** `goes-abi-mcmipc` (netCDF)
  offers Footprints only, `goes-geocolor` offers Imagery; both stacked, the
  bar docked at 51 ticks for the 50-frame span; the GeoColor night frame
  renders under the CONUS footprint; scrubbing 51 → 48 painted a different,
  correct frame after ~15 s of tiler cold-render latency (I-108); the theme
  toggle swapped the basemap ~8–10 s later with both layers surviving; no
  console errors on `/map`. **One defect found:** the collection Preview tab
  logged `Cannot add layer "preview-anchor" before non-existing layer
  "preview-frame-49-layer"` — the stack's anchor chains to a frame whose
  Source is not registered yet (self-heals on `styledata`); fixed and merged
  (60a3599): the stack chains only to frame layers `map.getLayer` already
  has, falls back to the caller's target otherwise, and re-evaluates the
  chain on every `styledata` event (a `useMap()` subscription — without it a
  fresh mount's fallback never resolved and the anchor could sit above the
  frames until an unrelated re-render). Re-checked live: the Preview tab
  opens and scrubs backward with no console error. The Task 2 review had
  flagged the cascade as a knowledge note; the live check showed it is loud.
  **Whole e2e suite after the merges (2026-09-14): 46 passed, 1 skipped** —
  the baseline, unchanged. Screenshots
  `screenshot-1789355865324-6.jpg` … `-1789356018266-12.jpg` (session temp
  dir).

- **A-1 landed 2026-09-09 (`ai/a1-alert-anchors`, merge 6600ebf), live-checked 2026-09-14.**
  Lane A (lead-defined, closes **I-84**): `ApiAlert` gained `process_id` (the
  EFFECTIVE process — `COALESCE(a.process_id, ps.process_id)`, so a
  source-anchored `process_stalled` attributes without a client-side source
  lookup) and `source_id` (raw); `buildProductRows` claims process alerts
  through the product's `process_source`/`process_output` edges and colours
  process lineage nodes (firing `error`, acknowledged `warn`, undeployed
  `warn`, else `alertsAreComplete ? ok : unknown` — the group rollup now seeds
  at `unknown`, so a group of unverified connections reads `unknown`, not a
  false `ok`); `processVerdict` takes the open alert list (one fetch per page)
  and an alert outranks the run ledger; the graph's `unhealthyNodeIds`
  indicts `proc:<id>` and deployed processes are `ok` (gated on completeness
  for process nodes only). The product Overview's "may relate" card and
  `unanchoredAlerts()` are gone; the home residual line stays (it also covers
  channel alerts) with reworded copy. No migration, no fixture. Three review
  rounds fixed brief defects: a vacuous SQL assertion, node health `ok` on an
  incomplete list, and the graph's ungated `ok`. Issues opened: **I-117**
  (state-blind graph indictment), **I-118** (extractor alerts unattributed +
  "No trigger" masks an extractor's alert — seen live). Live check (lead,
  Chrome, standing demo): `/` shows `goes-geocolor` "Failing · process runs
  dead-lettered" with a red process dot and the residual line counting only
  the extractor's alert; the product Overview paints the process node red;
  `/graph` colours the process nodes; `/processes` shows the alert verdict.
  Screenshots: `claude-chrome-screenshots-*/screenshot-1789355186550-0.jpg`
  … `-1789355219956-5.jpg` (session temp dir).

- **M3-B0 landed 2026-09-09 (`ai/m3-b0-harness-hygiene`, merge 08bea6a).** Lead-defined
  pre-slice (not in the M3 slice list): (1) `pipeline.loadgen teardown` is
  queue-aware — `loadgen/pgstac_hygiene.py` resolves the probe collection's
  `_items_<key>` partition WHILE the row exists, deletes that partition's
  `pgstac.query_queue` + `query_queue_history` rows (quoted `ILIKE`), then
  `delete_collection`; the JSON reports `queue_rows_cleared`. Six unit tests on
  a recording cursor pin the order. Proven on the first `m3b` baseline teardown
  (`queue_rows_cleared: 0` — the drain had already emptied the queue, and no
  orphan error followed). (2) **ADR 0020** records the opposite GUC pairings
  (writer: `use_queue` ON; drainer: `update_collection_extent` ON +
  `use_queue` explicitly FALSE) as an invariant; README index + key-invariants
  bullet, code comments at both sites and the FEATURES M3-A row point at it.
  Review found two defects in the lead's own ADR text (a non-existent gauge
  name `…oldest_age_seconds` → `…oldest_seconds`; a present-tense claim about
  M3-B's `pool.py`, which lands on a sibling branch) — both fixed before
  acceptance. Deferred minors (logged, not fixed): the `fnmatch` stand-in in
  `test_loadgen.py` models ILIKE without `_` as a wildcard; the pattern helper
  duplicates `Collection.queue_pattern` in `test_integration_pgstac_queue.py`;
  `drop_probe_collection`'s pattern would miss a `partition_trunc`
  sub-partition (loadgen never creates one).
- **M3-B baseline measured 2026-09-09 (lead, pre-pool `ai/main` build with M3-A, label `m3b`, torn down).**
  `feed --rate 30 --count 900` copy/`defaults_only`, `watch --seconds 240`
  overlapping the feed: `pg_stat_database.sessions` **429,066 → 446,636 =
  17,570 new backend sessions for 900 catalogued items ≈ 19.5 sessions per
  item** (S-D predicted ~14 from the repo count alone; the rest is the
  periodic ticks and the standing demo's own ingest sharing the window).
  Itemized 21.1 / 23.9 per interval while active (~40 s of work for 900
  items); `ingest_fetch` mean 24 ms, `ingest_itemize` 32 ms (901 calls each);
  pgstac queue flat at 0. The pooled number goes in the M3-B landed note.

- **V-2 landed 2026-09-08 (`ai/v2-map-page`), e2e + live-checked.**
  `npm run test:e2e:ci -- map` → **3 passed** (run with `E2E_PORT=4399`: the
  editor holds :4321, which is the documented `run-e2e` gotcha). Lead visual
  checks on a real map, all of which cover paths NO automated test reaches
  (jsdom leaves `mapRef.current` null, and the smoke spec deliberately avoids
  the canvas):
  - `/map` fills the viewport; basemap renders; nav + route title wired.
  - Adding a product draws its footprints, and **the first-add `fitBounds`
    fired** (scale 2000 km → 500 km, centred on the CONUS extent) — the one
    path with no coverage at any level.
  - Hover shows the tooltip with item id + datetime, and the feature-state
    highlight paints.
  - **Hide → show does NOT leave the hover highlight stuck on** — the fix from
    Task 9 fix round 2, which is unreachable from jsdom and was the one
    self-inflicted regression of the slice.
  - **Click-through opens a NEW TAB** at `/collections/goes-geocolor/items/<id>`
    (spec §4.6). Nothing in unit or e2e asserts this end to end.
  - **Theme toggle with two layers stacked: both survive and redraw.** This was
    the final review's Important #1 — `StacMap` swaps `mapStyle` on `$theme`,
    which makes maplibre rebuild the whole style and drop every layer, and
    `addLayer` with a missing `beforeId` fails silently. The chain
    re-establishes itself correctly.
  - **The `${id}-anchor` background layer does NOT black out the collection
    preview tab** (the Task 2 risk: a maplibre `background` layer defaults to
    `#000` and only `background-opacity: 0` keeps it invisible). goes-geocolor's
    Preview renders the basemap plus the GeoColor imagery frame normally.
    *Note for whoever checks this next:* the preview map is genuinely black for
    ~10 s while the dark basemap and tiles load — do not mistake that for the
    anchor bug, as I nearly did.
- **Basemap style swap on theme change is slow (~10 s+).** Toggling the theme
  leaves the map on the previous basemap for several seconds after the app
  chrome has already switched. Pre-existing (`StacMap` swaps `mapStyle` on
  `$theme`), not introduced by V-2, and harmless — but it reads as a bug and is
  worth a look when V-3 touches the map surface.

- **M3-A landed 2026-09-08 (`ai/m3-a-pgstac-queue`).** Session GUCs via the
  writer's pool `configure` hook (pypgstac's `PgstacDB(pool=…, use_queue=True)`
  seam — a handed-in `connection` would have skipped pypgstac's own SET, so the
  pool is what it gets; and `SET` is transactional, so the hook commits).
  **Spec correction made during the slice:** the two GUCs are NOT a matched
  pair. `update_collection_extent` is read inside `update_partition_stats`,
  which under the queue runs in the session that DRAINS, so setting it only on
  the writer (as §4.2/§4.4 direct) was a silent no-op. The drain connection
  therefore carries the opposite pairing — `update_collection_extent` TRUE,
  `use_queue` explicitly FALSE (not merely unset: `get_setting` COALESCEs
  through `pgstac_settings`, so an operator enabling the queue globally would
  otherwise re-arm the bug). Explicitly FALSE matters because the extent branch
  re-enters `run_or_queue`; a drain session with `use_queue` on would re-queue
  the extent UPDATE one hop further every tick, forever. Proven live by
  `tests/test_integration_pgstac_queue.py` (2 passed against the compose
  Postgres): the collection extent is the world bbox before the drain and the
  item bbox after.
- **M3-A measured 2026-09-08 on the shared stack** (labelled `m3a` probe, torn
  down). `--rate 0 --count 6000 --asset-bytes 65536`, watch 240 s / 20 s
  intervals: **itemized ~20–23 items/s while the pipeline was actively
  itemizing** (per-interval 19.97, 23.33, 19.87) against the S-A baseline of
  2–3.5/s and decaying — consistent with S-A's ~22/s with the setting.
  `pipeline.ingest_itemize` mean **33 ms** (1,260 calls; 35 ms over 2,001 calls
  in an earlier 2,000-item run). `BACKLOG pgstac queue` stayed flat at ~0
  (peak 0.10/interval) and returned to 0 within one tick.
  **§4.5 number: `pipeline.pgstac_queue_drain` mean 0.223 s at
  `pgstac_partitions` = 5** (0.253 s in the second run) — re-measure when
  partition count grows, since the drain's two `REFRESH MATERIALIZED VIEW`
  calls scale with partitions, not write rate.
  *Caveat on the window:* the plan's Step 4 puts a 60 s settle before the
  watch, but the pipeline drains a 2,000-item backlog faster than that, so the
  first run's 300 s average (6.67/s) was ~280 s of idle diluting ~40 s of work.
  The numbers above come from a re-run with the watch overlapping the load. A
  future measurement should start the watch with the feed, or feed enough to
  keep the pipeline saturated for the whole window.
  `ingest_files_failed: 88` is the harness's synthetic opaque granules failing
  GDAL open ("not recognized as being in a supported file format"), not a
  pipeline defect.
- **Collection extents are now maintained live.** `pgstac.update_collection_extents()`
  was run (plan Step 5) but was effectively a no-op: the demo's collections
  already carried real extents (goes-geocolor / goes-abi-mcmipc CONUS
  `[-142.69, 14.56, -52.92, 55.31]`, demo-scenes `[-104, 37, -94, 43]`) because
  the drain had been maintaining them for ~20 minutes by then. This is §4.4's
  stated purpose — "nothing maintains collection extents today" — working.
- **Loadgen teardown is not queue-aware, and M3-A just made that matter.**
  `loadgen teardown` drops the probe's collection (and its partition) without
  first draining `pgstac.query_queue`, so any `update_partition_stats('_items_N')`
  still queued for that partition is orphaned and errors on the next tick. Seen
  live: a tick logged `executed: 2, errors: 2` after the 2026-09-08 teardown,
  and `pipeline_pgstac_query_queue_queries_total{outcome="error"}` sits at 2 as
  a result. Harmless (the drain records the error and continues — that is
  exactly the error path M3-A added) but it dirties the counter and leaves rows
  a human has to reason about. **Fix in the harness: drain, or delete the
  partition's queued rows, before dropping the collection.** The same shape bit
  the DB-gated test before it was scoped to its own partition.
- **The drain's error path was exercised in production, unintentionally, and
  behaved correctly.** The orphaned statements above made a real tick take the
  `except` branch: it incremented the error counter, logged with `exc_info`, and
  still published both gauges, pruned history and evaluated staleness — which is
  the behaviour the M3-A review round added, and the failure mode the metrics
  exist to expose.

The M5 queue's follow-ups are archived with it at `59e8087`; the M3 **scoping**
queue's are at `b7fb503`, and its measured findings live in the scoping notes.
Residual gaps that are not slices are logged as issues instead — **I-82** (the
byte-volume model is declared, not measured — a real size census belongs before
any contractual scale or storage-cost claim) and **I-83** (streaming reaches
object stores only).

- **Collection preview: tiler-paced first pass, and no e2e.** Both logged as
  **I-108** — they are residual limitations rather than slices. The tab is
  covered by unit + component tests and was verified live against the standing
  `goes-geocolor` demo.

- **`LineageStrip size="full"` is now unused.** P-4 replaced the `/graph`
  columns with `PipelineDag`, and nothing else renders the `full` size. The
  `mini`/`medium` sizes are still the product-card and Overview glyphs (P spec
  §3 non-goal), so the component stays; the dead `full` branch and its
  `LineageGroup` column plumbing could be trimmed in a later tidy-up.
- **Clicking an extractor in the Graph view highlights only itself.** That is
  `lineage()` behaving exactly as the P spec §4 specifies — an `extractor` edge
  is followed upstream only, which is what keeps two collections that share an
  extractor in separate rows. In the whole-graph view an operator may instead
  expect the product it feeds to light up. Not changed unilaterally: the rule
  is approved and the Pipelines view depends on it. If the lead wants the other
  behaviour, it is a Graph-view-only tweak (seed the highlight set with the
  extractor's outgoing edges), not a change to `lineage`.

- **G-8's night-side live gate — MEASURED 2026-09-04, criterion met.** The
  gate asked for a night granule over 30 % colour pixels and a day granule
  within a few percent of the baseline. Both were measured directly on real
  NODD granules (no compose stack needed — G-8 changed `compose()` only, and
  the surrounding loop is G-7's, unchanged): night **0.0 % → 100.0 %**,
  terminator 13.9 % → 91.4 %, day 62.5 % → 62.5 % and bit-identical. Numbers
  and method: spec §16. What is NOT proven is only that the run CONTAINER
  produces the same arrays — the same read/write code G-7 already gated — so
  what remains is a look at the standing demo's next night frame after a
  re-seed, not a gate.

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

- **Live-check findings, 2026-09-02 (lead, Docker).** Three defects the unit
  suites could not see, all fixed and pinned:
  1. The repo-root `.dockerignore` excluded `infra/`, so the derived titiler
     image built from an almost-empty context and its `COPY` failed. Now a
     documented gotcha in `AGENTS.md`.
  2. The item preview's overlay named `beforeId: item-geometry-fill` while
     rendering BEFORE that layer existed; maplibre refuses that outright, so
     the layer was never added. Mocked map primitives accept any order —
     the regression test now pins it and was confirmed to fail on the old code.
  3. (Measured, not a defect.) After G-3 a triggered run is claimed **3–16 ms**
     after its row is created, against up to 60 s before, and an 8-item burst
     produced 2 runs — one immediate, one coalescing the other 7.
- **G-3's loadgen concurrency check is still owed** (plan Task 6): confirm no
  duplicate `process_run_now` executions at M3-D's worker concurrency, and
  watch `procrastinate_jobs` for churn from the extra per-item enqueues. The
  functional behaviour is verified; the throughput behaviour is not.

- **W-1 landed 2026-09-02 (`ai/w1-window`), two plan deviations worth knowing:**
  (1) the plan's year-rollover expansion test expected the prefix of the hour
  the window ENDS on; with the half-open `[begin, end)` the spec chose, that
  hour holds nothing admissible, so the test now expects two prefixes, not
  three. (2) The fixture marks bad-template-token cases `app: reject`, but the
  plan's Zod only checked shape — the token guards (≥ 1 known token, no
  unknown) are now mirrored in `ingestConfigSchema` so the form refuses them at
  write time. The bound GRAMMAR stays pipeline-only, as designed.
- **W-1 live gate MET 2026-09-02 (lead, Docker, real NODD).** Anonymous s3
  connection to `noaa-goes19`, collection `goes-abi-mcmipc`, reference-mode
  association `source_path "ABI-L2-MCMIPC/"`, `path_template "{Y}/{j}/{H}/"`,
  `window {begin: "-1h"}`, `max_files_per_poll 4`, 60 s polls. Measured
  ticks: `prefixes_listed 2` (a -1h window floors to two hourly prefixes, not
  the three the plan guessed), `listed 21`, `out_of_window 9`,
  `deferred_by_cap 8 → 4 → 1`, `settled 4` per poll — 12 items catalogued in
  three polls, oldest first, then steady state. Two findings for G-6/G-7
  planning: `metadata.defaults.datetime: "file_mtime"` resolves to the ledger
  SETTLE time (item datetime 03:49Z for a scan at 03:16Z whose object was
  last-modified 03:19Z — I-100), and `raster_auto` on the MCMIPC netCDF fell
  back to `collection_extent` geometry silently (I-101), so the association
  was switched to `defaults_only` to stop downloading ~39 MB per file for
  nothing. The association is left ENABLED as the seed for G-7.
  Residuals I-97…I-99 are in ISSUES.md.

- **W-2 landed 2026-09-02 (`ai/w2-retention`).** One naming deviation from
  the plan: the GC dataclass is `RetentionCollection` (existing name), not the
  plan's `GcCollection`. The assembled `list_expired_items` SQL was run against
  the live pgstac (offset 0 and a huge offset, plus the UNION) — syntax proven.
  **Live check MET 2026-09-02:** `demo-scene-002` (a copy of 001's COG under
  its own prefix, newer datetime) + `retention_max_items: 1`, `gc_grace_days
  0` → the impact dry-run said `1 of 2`; the next `retention_gc` tick marked
  `assets/demo-scenes/demo-scene-001/` with reason `retention` and deleted
  the item; `asset_collect` removed the object in the same minute
  (`deleted_objects 1, errors 0`). `demo-scenes` now holds only 002 and keeps
  the cap — run `pipeline.demo seed` to restore the original scene.

- **G-6 landed 2026-09-03 (`ai/goes-g6`), deviations from the plan:**
  - Task 9b (unplanned): the run planner treated every canonical href as
    platform-held, but a reference-mode item is catalogued with a canonical
    href too — a gap found in G-2's planner while wiring G-6, fixed by
    `ProcessRepo.reference_source_hrefs` staging from `ingest_files.source_href`.
  - `graph-cycles.test.ts`'s extractor assertion was retargeted: the plan's
    original `wouldCycle(p → src)` case was already a real cycle via the
    existing `process_source` edge, unrelated to the new extractor edge.
  - The extractor process-id rule (extractor strategy needs a `process_id`)
    is single-sourced in `ingest/config.py`, not duplicated in `extract.py`.
  - `check_extract_output` was made total over malformed/non-dict document
    shapes instead of raising `AttributeError` out of the batch.
  - A partial-rejection error is now recorded on the run row, not silent.
  - Draft asset keys are filename stems (`build_assets`), not the raw
    filename — carries to G-7.
  - The loadgen teardown removes extractor rows AFTER the association
    delete, not before as the brief said; no FK path makes the order matter.

- **G-6 live gate MET 2026-09-03 (lead, Docker, real NODD, reference mode).** Merged
  `ai/goes-g6` → `ai/main` (598c8ae); migration 027 applied on first app request.
  A pass-through extractor (the loadgen `EXTRACTOR_CODE`) created through the API
  got the 600/h default and a 409 on `POST …/sources`; the seeded reference-mode
  `goes-abi-mcmipc` association was switched to it. Next NODD file: `stored →
  extracting` (item id kept, run id stamped) at 16:45:01Z → run claimed the same
  second → `succeeded` 16:45:18Z (17 s including staging the ~50 MB file from
  NODD through the ledger's `source_href`) → row `itemized` 16:45:19Z with the
  extractor's properties; `source_mtime` = the object's modified time (I-100
  closed for real). Failure leg: a `raise SystemExit(1)` revision → attempt 1
  `failed` (row stays `extracting`), attempt 2 → run `dead` → row `failed`,
  `reason = "extractor run <id>: run exited 1"`, `item_id` cleared. The
  association was restored to `defaults_only` and the check extractor
  soft-deleted. Owed still: a copy-mode extractor pass (the live check was
  reference-only) and the loadgen `--metadata extractor` run at M3-D concurrency.

- **G-7 landed 2026-09-03 (`ai/goes-g7`), deviations/details from the plan:**
  - **Footprint bisection.** `extractor.footprint()` reprojects the densified
    ring in one bulk `rasterio.warp.transform` call; GDAL raises for the
    WHOLE call the instant any single vertex is off the Earth's disk rather
    than returning it as infinities, so the extractor catches the exception
    and bisects the point list, retrying each half recursively, until only
    the off-limb vertices are dropped.
  - **Per-item `{out_id}.tif`, not a shared `visual.tif`.** `geocolor.py`
    writes and publishes one filename per output item (asset key stays
    `visual`) — the spec's `visual.tif` would let a batch of more than one
    output overwrite a shared name.
  - **`scan_time` reads the href tail, not the asset key.** G-6 keys draft
    assets by filename STEM (`build_assets`); the `_s…` scan-time token is
    only reliably present in the href's actual filename, so the extractor
    parses `asset["href"]`'s trailing path segment (falling back to the
    asset key only if the href is missing) rather than the manifest's asset
    key directly.
  - **`REQUIRED_MIGRATION` is `027_extractors`** in both `pipeline.demo
    __main__.py` and `goes/seed.py` — the same migration G-6 landed, since an
    ingest association cannot name an extractor before it.
  - **The e2e spec resolves its own directory with ESM's `import.meta.url`**
    (`dirname(fileURLToPath(import.meta.url))`), not `__dirname`, to read the
    two GOES scripts as the single source of truth for the deployed code —
    `app/e2e/goes-loop.spec.ts` runs under Playwright's ESM config.
  - **`proj:wkt2` is pinned, not `proj:epsg`.** The output COG's CRS is the
    source file's own geostationary projection, which has no EPSG code, so
    rio-stac's `with_proj=True` always lands on `proj:wkt2`.
  - **The e2e datetime gate compares an instant, not a string.** Gate 1
    asserts `Date.parse(source.properties.datetime) === expectedMs`, since
    the extractor's ISO string and the test's independently-computed
    expectation are not guaranteed to be byte-identical, only the same
    instant.
  - **The live gate (Task 7) is owed by the lead** — this task (Task 6) only
    lands the docs and runs the offline gates (`npm run verify`, pytest,
    ruff); nothing here was run against the real NODD bucket.
  - **Live gate finding 2026-09-03**: rio-stac's `with_proj=True` bounds
    transform raises `Full reprojection failed` on the real MCMIPC grid;
    `geocolor.py` now reuses the source footprint; `OGR_ENABLE_PARTIAL_REPROJECTION`
    alone still raises on the installed GDAL, so the rio-stac call is retried
    with an identity `geographic_crs` and the source geometry/bbox substituted
    (the branch review had rated this a minor).
  - **Live gate finding 2 (2026-09-03)**: finalize rejected every process
    output over 8 MB as `checksum_mismatch` — the copy verification compared a
    multipart-upload ETag with the copy's single-part ETag; fixed in
    `finalize/steps.py` (size always, ETag only when single-part).

- **G-7 live gate MET 2026-09-03 (lead, Docker + internet, real NODD).**
  `E2E_LIVE_NODD=1 E2E_PORT=4322 npm run test:e2e:ci -- goes-loop` → 1 passed
  in 1.5 min (run 4). Measured on the last run: file admitted 21:25:06Z →
  extractor `succeeded` +33 s → GeoColor COG published +20 s → delivered to
  `stac-higher-deliveries` within the same minute; tile rendered from the
  tiler; source item carried the scan-time `datetime`, `platform goes-19`,
  `goes:*` and an extractor-computed footprint. Three defects the unit suites
  could not see, each fixed and pinned before the passing run: (1) rio-stac's
  `with_proj=True` bounds transform raises on the real geostationary grid —
  `geocolor.py` now reuses the source footprint (b7f6672); (2) finalize
  rejected every process output over 8 MB as `checksum_mismatch` — a
  multipart-upload ETag can never equal the copy's single-part ETag; the
  move is now verified by size, ETag only for single-part uploads (da501ce);
  (3) the new multipart INFO log used `filename` in `extra`, a reserved
  LogRecord key, and raised on every finalize — renamed, and the test now
  captures INFO so the log path executes (89ac324). Preconditions that bit:
  the user's :4321 dev server has no `CREDENTIALS_MASTER_KEY` (the delivery
  connection needs it), and Astro 7 refuses a second `astro dev` from the
  same checkout — the gate ran from a detached worktree on :4322. Still owed:
  the manual `pipeline.demo goes-seed --deliver` recipe against the full hour
  (the W-1 seed association on `goes-abi-mcmipc` must be deleted first).

- **Standing GOES demo seeded 2026-09-04 (lead, Docker + internet, real NODD)**
  via `pipeline.demo goes-seed --deliver` — the manual recipe the G-7 gate left
  owed. First run found ONE defect the e2e could not see: the seed wrote a NULL
  `credentials` column for the anonymous NODD connection, but `build_adapter`
  treats NULL as "connection has no stored credentials" and refused it, so
  every `ingest_discover` poll failed before listing (the e2e passed because
  the app's `POST /api/connections` stores an encrypted `{}` for exactly this
  reason). Fixed on `ai/goes-seed-anon-creds`: the seed seals `{}` with the
  master key, which every `goes-seed` now requires up front, not only
  `--deliver` (`platform.upsert_connection`'s docstring had called `None` the
  anonymous case — corrected). Measured after the fix, window `-1h`, cap 2 per
  poll: first two polls admitted 4 granules → extractor `succeeded` 26 s
  after claim → GeoColor run of 4 `succeeded` ~1.5 min later → 4 COGs
  (~3.2 MB each) delivered to `s3://stac-higher-deliveries/goes/` within the
  same minute; the tiler served a z4 JPEG tile of `goes-geocolor`; source
  items carry the scan-time `datetime`, `platform goes-19`, `goes:*` and a
  93-vertex extractor footprint. Claim latency 95–265 ms. Preconditions that
  bit: the W-1 seed association on `goes-abi-mcmipc` had to be deleted first
  (its 230 `defaults_only` reference items went with it, by design), and the
  seed must be run with the repo `.env` exported (`set -a; source .env;
  set +a`). The demo is left ENABLED and keeps ingesting the trailing hour.
  Still owed: the loadgen `--metadata extractor` run at M3-D concurrency.

- **Feedback triage 2026-09-04 (lead's `FEEDBACK.md`, five items).** Items 1–3
  (graph) → the P queue + its draft spec; item 4 (grey GeoColor) → G-8,
  after measuring that night COGs are ~1 % colour and a daytime e2e COG 57 %
  — the compose night branch, not a bug; item 5 (stactools) → the X queue +
  its draft spec. Both specs were approved by the lead the same day; every
  P, X and G-8 slice may start. Issues opened: I-104 (ghost graph nodes), I-105 (credentialed
  stactools packages ship without a live gate), I-106 (no static
  reference-asset input for processes — what city lights would need).

- **Post-review fix wave 2026-09-08 (`ai/review-fixes`).** Four findings from
  the review of M3-A + V-2, all fixed and merged; full e2e now **46 passed, 1
  skipped** (was 45 passed, 1 failed).
  1. **The full e2e suite had been RED since G-1 (2026-09-01)** and no slice
     noticed, because a slice runs only its own filtered spec
     (`test:e2e:ci -- map`). `connections.spec.ts`'s `getByLabel("Bucket")`
     matches by SUBSTRING, so G-1's "Anonymous (public bucket)" switch made it
     resolve to two elements. **Run the whole suite at least at queue
     boundaries**, not just the slice's spec — a filtered run cannot tell you
     the suite is green.
  2. **Any test that creates and drops a pgstac collection must now clear its
     partition's queue rows before dropping it.** Since M3-A the writer runs
     with `use_queue` ON, so an upsert QUEUES
     `update_partition_stats('_items_<key>')`; `delete_collection` then strands
     it and the next drain tick errors it into `query_queue_history` forever
     (measured: 4 orphans per `test_integration_itemize.py` run, errors 4 -> 8).
     `test_integration_pgstac_queue.py` had the right shape; the older itemize
     test did not, and now does. Applies to any future DB-gated test.
  3. A failed drain CALL now increments
     `pipeline_pgstac_query_queue_drain_failures_total`, not
     `..._queries_total{outcome="error"}` — "the drainer is broken" and "one
     queued statement failed" want different responses.
  4. ISSUES.md heading spacing.

(append here during iterations)
