# TODO — implementation queues

**This file holds SIX INDEPENDENT QUEUES. It is not one list.** Work only the
queue you were asked for, top-down within it. The first unchecked item in the
FILE belongs to M3 and is rarely the right default.

**If you were not told which queue, stop and ask.** Which one is right depends
on what the human wants that day; guessing costs a worktree and a merge, not
just a wasted read.

| Queue | What it is | State |
|---|---|---|
| **M3** | NOAA-scale readiness: ~60 items/s sustained, measured | Spec approved. **M3-A goes first** — the ordering below is a dependency spine, not a preference |
| **G** | GOES GeoColor loop: NODD → COG → deliver → tiles | **Queue complete 2026-09-04** (G-1…G-8 merged), standing demo running since 2026-09-04. Only G-8's lead-only live gate remains — see the follow-ups |
| **P** | Pipeline graph: per-product lineage lines + a full graph view + ghost-node fix | **Queue complete 2026-09-04** (P-1…P-4 merged). Two follow-ups in the follow-ups section |
| **X** | Built-in extractor library: stactools packages as one-click extractors | Spec **approved 2026-09-04**, **worked first**. X-1 merged 2026-09-04 (the set is **eleven**, not fourteen — I-107); X-2 next; X-3 coordinates with K-1; X-4 takes migration **028** |
| **K** | Process compute on Kubernetes + Kueue, hardware profiles | Spec **approved 2026-09-04**. K-1 may start; K-3 takes migration **029** (X-4 has 028); K-4 coordinates with M3-D |
| **W** | Ingest date window + retention cap | **Queue complete 2026-09-02** (W-1 and W-2 merged). Only the two lead-only live checks remain — see the follow-ups |

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
- [ ] **X-2 · stactools runtime image + wrapper + adapters.** Spec §6.
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
- [ ] **X-3 · Image alias.** Spec §8. `runtime_image: "default" |
      "stactools"` on `runtimeLimits` (lenient Python reader — stored
      revisions lack it), `PROCESS_RUNTIME_IMAGE_STACTOOLS` resolved at launch,
      unknown alias ⇒ dead run with reason (write gate AND launch, the
      `PROCESS_NETWORK_MAX` pattern), `process-runtime.json` cases.
      `runtime.image` stays `null` for inline processes (ADR 0013 intact).
      Coordinates with K-1. Depends on X-1.
- [ ] **X-4 · Built-in processes in the app.** Spec §7. Migration 028
      (`processes.builtin_id`, unique per live group); `GET
      /api/extractors/builtin`; `POST /api/processes/builtin` create-or-reuse
      (operator+, audited) deploying revision 1 from the two-line template
      in one transaction; the process page's read-only "Built-in" card with
      **Update to current** (the only way its revision moves; code deploy ⇒
      409); `built-in` badge; the ingest form's "Built-in" optgroup filtered
      by `supports` against the grouping rule, storing the returned
      `process_id` exactly as today. Depends on X-1, X-3.
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

- **Collection preview: no e2e coverage.** The Preview tab (2026-09-04) is
  covered by unit + component tests and was verified live against the standing
  `goes-geocolor` demo, but the suite has no spec for it: the tab needs a tile
  server, which `test:e2e:ci` does not assume. If it earns one, gate it like
  `goes-loop.spec.ts` does (`E2E_LIVE_NODD`-style env gate) rather than adding
  a titiler precondition to the default run.
- **Preview playback is paced by the tiler, not by `fps`.** The first pass
  through 50 frames runs at ~1.5 fps because each frame is a fresh titiler
  render off MinIO; replays run at the full rate off the browser cache. If a
  demo needs the first pass smooth, the fix is warming the frames server-side
  (a mosaic cache or a pre-render), not a deeper client lookahead — more
  mounted sources starve the visible frame's own tile requests.

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

- **G-8's night-side live gate (LEAD ONLY, Docker + internet).** The unit
  tests pin the ramp and the terminator blend on synthetic grids, and a
  synthetic night scene (25 % cloud at 200–260 K over surface at 285–300 K)
  measures 100 % colour pixels by the 2026-09-04 `|r−g| > 8 or |g−b| > 8`
  rule against ~1 % before. What is unverified is the real thing: re-run
  `pipeline.demo goes-seed` (idempotent — it re-deploys the revision), wait
  for one night granule and one day granule, and check that the night COG
  clears 30 % colour pixels and the day COG stays within a few percent of the
  2026-09-04 baseline of 57 %.

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

(append here during iterations)
