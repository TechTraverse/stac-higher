# M3 scoping notes (seeded 2026-08-31)

Inputs to the M3 (NOAA-scale readiness) design spec — the M2/P9 pattern:
scoping items first, spec + lead approval last (M3-S-G), implementation
only after approval. This file is the working brief; scoping iterations
append findings under their item's heading.

**Scope sources:** ROADMAP §2 (byte-volume envelope), §9 M3 milestone, §10
(Postgres-queue ceiling, scheduler/monitor HA, processes multiply
throughput); Phase 9 spec §12 (throughput arithmetic — the number M3
consumes); ISSUES inventory below.

**Settled inputs — do not relitigate:**

- M3 targets **~30 items/s ingest-origin** (~2.6M/day), and per Phase 9
  spec §12 process-generated volume is budgeted at ≤ 1× ingest, so M3
  sizes and rehearses against **~60 items/s total catalog write rate**,
  with a synthetic process (≥1 output per input on a 50% source share, or
  equivalent) in the load rehearsal.
- **Local-first**: M3 is measured on the docker-compose stack. It pulls
  the Phase 8 load-gate *measurement* forward; AWS/IaC and the SQS backend
  stay in Phase 8. Where the local measurement says a knob only matters in
  cloud (e.g. network), record it for Phase 8 rather than chasing it.
- **Finalize is the correct choke point** for process/push volume
  (per-item validation ≈ ITEMIZE cost; pypgstac batch upserts amortize) —
  Phase 9 spec §12.
- The §7 per-process ceiling bounds any single process (default
  60 runs/h × batch size).
- The M2-A follow-up — **batching per-transition `flow_stats` writes** —
  is *mandatory* at the M3 rate (Phase 9 spec §12 declared it so). Today
  every ingest/delivery transition bumps `flow_stats` jsonb per event.

**Issue inventory (scale-relevant, by scoping item):**

| Issue | What it says at 60 items/s |
|---|---|
| I-40 | dispatcher claim is concurrency-safe; leader election / partitioned ownership deferred to "Phase 8 / M3" — M3 is now. Scheduler/monitor/dispatcher are singletons (throughput, not correctness) |
| I-43 | delivery is at-least-once; concurrent dispatch dedup deferred — fine per current posture, re-confirm at rate |
| I-19 | adapter `get()` buffers whole objects; copy-mode FETCH buffers get→put. Multi-GB assets unsafe at envelope scale |
| I-26 | EXTRACT buffers rasters in memory (same class as I-19) |
| I-3 | ADR 0004 bridge drains are 1-minute-granular — irrelevant to item throughput, matters only for operator UX |
| I-41 | malformed CQL2 filter silently matches nothing — at M3 scale a misconfigured high-volume flow is more expensive; config-validity alert becomes more attractive |
| I-58/I-59 | notification at-least-once + GC residuals — re-read under load assumptions |
| I-71 | no rate limiting / per-group quota on push — a push client at M3 rates is unthrottled |
| I-79 | delete event draining at the retry cap orphans bytes — bulk retention deletes at scale widen exposure |
| I-80 | delete+insert pair residuals — bulk transaction-API writes at rate would multiply the pair volume |

---

## M3-S-A · Baseline throughput measurement (the load-bearing input)

Nothing else can be sized until we know where today's stack actually
saturates. Build the minimal synthetic harness + measure, per stage:

- **Feed generator**: synthetic S3 source (MinIO bucket, parametrized
  items/s and asset size) driving a copy-mode and a reference-mode ingest
  association; a direct-outbox seeder for isolating dispatcher/delivery
  from ingest.
- **Measure per stage** off `/metrics` + table counts: DISCOVER→ITEMIZE
  rate, outbox drain rate (events/s per dispatch tick), delivery
  throughput (items/s per destination), finalize throughput (staged
  items/s), and the DB hot spots (`flow_stats` bump contention,
  `item_events` claim query, `delivery_log` upserts).
- **Record the ceiling and the first bottleneck** in a measurements
  section appended here. The spec's slice plan is built from this number,
  not from guesses.

Constraint: the harness must be runnable by the solo loop (`npm run
verify`-adjacent — a pipeline-side script + compose, no new infra), and
become the M3 gate's rehearsal driver later.

## M3-S-B · §2 byte-volume arithmetic redo

ROADMAP §2's arithmetic was computed against 100k items/day. Redo at 2.6M
items/day (+1× process output): asset-size distribution assumptions
(NOAA-class granule sizes), copy vs reference split, bytes/day through
FETCH, canonical storage growth/day against retention, delivery fan-out
bytes. Output: a table in the spec + a recommendation on the copy-mode
posture at M3 scale (feeds M3-S-E). GC/retention arithmetic too: expiring
2.6M items/day means the retention sweep and `asset_gc` collector are
bulk paths (ROADMAP §1 said so; verify the shipped implementation batches
accordingly).

## M3-S-C · Concurrency & HA plan (I-40)

Decide the multi-instance story for M3: scale WORKERS only (keep
scheduler/dispatcher/monitor singletons — today's safe posture) vs leader
election vs partitioned ownership. Inputs: the S-A measurement (is the
dispatcher the bottleneck at 60 items/s, or the workers?), Procrastinate's
multi-worker behavior, the ingest-ledger claim audit (are `ingest_files`
transitions atomic under concurrent workers the way `item_events` claims
are?). Bias per ROADMAP §10: batch-oriented jobs keep job rate low — if
the singleton dispatcher drains 60 items/s comfortably, M3 scales workers
and documents the singleton, and leader election stays Phase 8.

## M3-S-D · Hot-path write batching

The known per-event write amplifiers, to be measured in S-A then designed:

- `flow_stats` per-transition jsonb bumps (the mandatory M2-A follow-up) —
  batch per tick/job, or move counts to an aggregate-on-read model.
- `delivery_log` / `ingest_files` row churn at rate (UNIQUE-upsert model
  is the ADR 0012 anchor — batching must not break the upsert semantics).
- Outbox claim batch sizing + `mark_processed` round trips.
- `/metrics` counter overhead is presumed fine (in-process) — confirm.

## M3-S-E · Streaming decision (I-19 / I-26)

At 30 items/s copy-mode, whole-object buffering is a per-worker memory
bomb only if NOAA-class assets are large — S-B's size distribution
decides. Options: (a) true streaming get→multipart-put in the s3 adapter
(the only protocol that matters at envelope volume); (b) declare copy-mode
size limits and lean on `reference` mode at M3 scale (honest-limits
posture, ROADMAP §2 already half-says this); (c) defer to Phase 8.
Decide, with the memory envelope per worker written down.

## M3-S-F · Queue-backend headroom arithmetic

Paper check (feeds Phase 8's Procrastinate→SQS boundary): at 60 items/s
batch-oriented — with S-A's observed batch sizes — what is the actual
jobs/s against Procrastinate, and where is its comfort ceiling on the
shared Postgres? Include the LISTEN/NOTIFY wake path and the M2-G sweep
load. No backend work in M3; this is the number that tells Phase 8 where
the boundary sits per deployment size.

## M3-S-G · Design spec + lead stop point

Write `2026-09-XX-m3-noaa-scale-design.md` (the M2 pattern: gate/done-when
firmed from ROADMAP §9 M3, slices with a dependency spine, testing & risk,
throughput arithmetic carried from S-B), run the adversarial review
rounds, then **STOP for lead approval**. The implementation queue replaces
the scoping queue in `TODO.md` only after approval. Gate shape to firm up:
a measured load rehearsal sustaining the 60 items/s total write rate for a
defined window on the auth-enforced local stack — ingest + a synthetic
process at 50% share + delivery fan-out — with no lost items, alerts
functional throughout, and a written load report recorded M-gate style in
ROADMAP §9.

---

# Findings

## M3-S-A · Baseline throughput measurement — RESULTS (2026-09-01)

**Harness:** `services/pipeline/src/pipeline/loadgen/` (`python -m
pipeline.loadgen`), run from the host against the plain compose stack.
Preconditions, profiles and how to read the report: that directory's
`README.md`. Unit-tested where a silent error would poison a measurement
(`tests/test_loadgen.py`): the pacer, the rate arithmetic, the windowed job
means, and a round-trip of the ingest config through the pipeline's own
`parse_ingest_config`.

Environment: docker-compose stack on the dev Mac, plain (not auth-enforced),
pipeline container at its shipped settings, MinIO on the same host. Ingest
config: `grouping.rule = none` (one file = one item), `poll_frequency_seconds
= 60`. All numbers are single-container.

### Headline

> **Today's ceiling was ~2–3.5 items/s and DECAYING. The cause is not the
> pipeline's code: it is `pgstac`'s inline `update_partition_stats`, which
> scans the whole destination partition on every write call. Turning it off
> (`pgstac_settings.use_queue = true`) took the same run to a sustained
> ~22 items/s with no code change. The next ceiling after that is the
> Procrastinate worker's `concurrency = 1`.**

### 1. The first bottleneck: `pgstac.update_partition_stats`, O(partition) per CALL

`PgPgstacWriter.upsert_items` calls `pypgstac`'s `Loader.load_items` **once per
item** (ITEMIZE publishes one item at a time). Every such call ends up in
`pgstac.update_partition_stats(partition)`, which does

```sql
SELECT min(datetime), max(datetime), min(end_datetime), max(end_datetime) FROM <partition>;
ANALYZE <partition>;
```

— two full scans of the destination partition, per write call.

Measured, single-item upserts into one collection as it fills:

| items already in partition | ms/item | items/s |
|---|---|---|
| 0 | 13.2 | 75.5 |
| 1,040 | 144.2 | 6.9 |
| 2,080 | 272.5 | 3.7 |
| 3,120 | 398.2 | 2.5 |
| 4,160 | 525.4 | 1.9 |
| 5,200 | 654.5 | 1.5 |

Linear, slope ≈ **0.125 ms per item already in the partition**. O(n) per
insert ⇒ O(n²) to fill a partition. This exactly explains the observed live
behaviour: the first 2000-granule run itemized at 7 items/s falling to 2/s over
ten minutes.

**It is per CALL, not per item** — batching amortizes it but does not remove
it. At batch 500 into the same growing partition:

| items already in partition | call seconds | ms/item |
|---|---|---|
| 0 | 0.147 | 0.29 |
| 2,000 | 0.360 | 0.72 |
| 4,000 | 0.586 | 1.17 |
| 5,500 | 0.793 | 1.59 |

Same 0.125 ms/existing-item slope, spread over 500 items. Extrapolated, batching
**alone** does not reach the envelope: at a 2.6M-item partition the fixed
per-call term is ~325 s, so holding 30 items/s would need batches of ~10,000
and a single call taking five and a half minutes.

**The fix is a pgstac setting.** `pgstac_settings.use_queue = true` routes the
stats update through `pgstac.query_queue` instead of running it inline. Same
bench, same growing partition, batch = 1:

| items already in partition | ms/item | items/s |
|---|---|---|
| 0 | 4.6 | 215 |
| 1,040 | 3.2 | 310 |
| 2,080 | 3.5 | 285 |
| 3,120 | 3.8 | 266 |
| 4,160 | 3.7 | 268 |
| 5,200 | 4.1 | 242 |

**Flat.** ~250–310 items/s at batch 1 — 8× the M3 ingest target with no
batching at all.

Confirmed end to end on the real pipeline, same 2000-granule run as the
baseline: sustained **22 items/s** (21.3 / 22.0 / 22.3 / 21.2 per 20 s
interval) versus 2–3.5 before.

Constraints this fix carries into the design:

- **Nothing drains `pgstac.query_queue` today** — no `run_queued_queries` call
  exists in either runtime. Enabled without a drainer, partition stats and
  constraints go permanently stale, which degrades search partition pruning.
  M3 must own a periodic drainer.
- `pgstac.run_queued_queries()` is a **PROCEDURE**: it needs `CALL`, not
  `SELECT`. (`SELECT` fails outright, so this is a loud error, not a silent
  no-op.)
- The setting lives in the `pgstac.pgstac_settings` table, i.e. in a database
  the app owns migrations for (ADR 0001) but whose `pgstac` schema is
  pypgstac's. Who writes that row, and when, is an M3 design question.
- Not shipped: `use_queue` was set for the measurement and **reverted**
  afterwards, and the queue drained, so the local stack is as it was. Turning
  it on is an M3 implementation slice, not a scoping change.

Connection overhead is a red herring: opening `PgstacDB` costs 4.2 ms
(a raw `psycopg.connect` is 4.19 ms), against 245 ms for a batch-1
`load_items` on a reused connection. Pooling would buy ~2%.

### 2. The second bottleneck: `concurrency = 1`

`ProcrastinateQueue.run_worker` calls `app.run_worker_async()` with no
arguments, so the worker runs at Procrastinate's defaults:
**`WORKER_CONCURRENCY = 1`** and a 5 s `fetch_job_polling_interval`. One job
at a time, for the whole service — ingest, dispatch, delivery, processes,
every sweep.

Consequences measured:

- After the pgstac fix, ingest and delivery **serialize into phases** rather
  than overlapping. A 1500-item run with a delivery fan-out ingested at ~22
  items/s, then delivered at ~24 → 21 → 18 items/s, finishing both in ~140 s:
  **~11 items/s of full-pipeline (ingest + 1× delivery) work**.
- A batch job monopolises the single slot. `dispatch_poll` draining 3000
  seeded outbox events took **11.8 s per call** — twelve seconds during which
  no ingest job can run.

Against the M3 budget (30 items/s ingest-origin, ~60/s total catalog write
rate), ~11 items/s end-to-end is **~5.5× short**, and the deficit is now
concurrency. Handed to **M3-S-C**, which should decide worker concurrency
before it decides multi-instance anything — the singleton question is
premature while the singleton is running one job at a time.

### 3. Per-stage costs (windowed means, post-fix)

| stage | mean s | notes |
|---|---|---|
| `ingest_fetch` (64 KB, copy) | 0.025 | get→put through the platform client |
| `ingest_fetch` (8 MB, copy) | 0.067 | ≈125 MB/s each way; scales with bytes |
| `ingest_itemize` (`defaults_only`) | 0.044 | includes the ~4 ms pgstac upsert |
| `ingest_itemize` (`raster_auto`, 8 MB GeoTIFF) | 0.055 | **EXTRACT adds only ~11 ms** |
| `deliver` (64 KB) | 0.051 | |
| `ingest_discover` (2000 files) | 3.5–4.5 | one call per association per poll |
| `dispatch_poll` (3000 events) | 11.8 | ≈127 events/s |

**rio-stac EXTRACT is not a throughput problem** at 8 MB single-band rasters —
11 ms against a 44 ms baseline. That reframes I-26 as purely a MEMORY
question, not a CPU one; see below and hand it to **M3-S-E**.

### 4. Memory envelope (I-19 / I-26 input for M3-S-E)

Pipeline container RSS during the 8 MB raster run at concurrency 1:
baseline **~255 MiB**, peak **~299 MiB**. So the whole-object buffering costs
roughly **2–3× the asset size, transiently, per concurrent item**, on top of a
~255 MiB floor. CPU peaked at ~80% of one core — consistent with the single
slot being the limit.

The envelope for M3-S-E is therefore: `RSS ≈ 255 MiB + 3 × (largest asset) ×
concurrency`. Harmless at 8 MB and concurrency 1; it is the product with
concurrency — the very knob §2 says M3 must turn — that makes I-19/I-26 bite.

### 5. Two structural facts the arithmetic must carry

- **DISCOVER needs two polls to settle a file** (a file goes `seen`, then
  `settled` only when the next poll finds it unchanged). At a 60 s poll that is
  a fixed ≥60 s latency per file before it can be fetched — throughput-neutral,
  but it sets the floor on end-to-end latency and on how long a gate rehearsal
  must run before steady state.
- **Ingest is two queue jobs per item** (`ingest_fetch` + `ingest_itemize`),
  each a separate Procrastinate job, on top of the per-association DISCOVER and
  GROUP. At 30 items/s that is ≥60 jobs/s before dispatch and delivery — the
  number **M3-S-F** should start its arithmetic from.

### 6. What was NOT measured

Stated so the spec does not mistake silence for a clean bill of health:

- **Delivery in isolation.** `seed-outbox` events name items that do not exist
  in pgstac, so run D measured dispatcher drain only. Delivery was measured
  live (run F), interleaved with ingest.
- **A process leg.** The M3 gate wants a synthetic process at ~50% source
  share; the harness has no process driver yet. Gate-rehearsal work.
- **The auth-enforced overlay.** All runs used the plain stack.
- **Reference mode.** The harness supports `--mode reference`; the copy/
  reference comparison belongs with M3-S-B's size distribution.
- **Sustained-rate runs.** Every run above was a saturation probe (`--rate 0`).
  Holding an offered 30/s for a long window is the gate's shape, not the
  bottleneck-finding shape.
- **Anything above ~5,500 items in one partition.** The O(n) slope was measured
  over that range and extrapolated; the extrapolation is the argument for the
  fix, not a measurement of a 2.6M-item partition.

### 7. Recommendation to M3-S-G

The spec's slice order falls out of the above, and it is cheaper than the
scoping brief assumed:

1. **`use_queue = true` + a periodic `CALL pgstac.run_queued_queries()`** — the
   single highest-leverage change measured here (10× on the real pipeline, one
   settings row plus a sweep). Carries the stale-stats obligation.
2. **Worker concurrency** as a setting, decided by M3-S-C against the
   `ingest_files` transition audit. This is where the remaining ~5.5× lives.
3. **Batching** (M3-S-D) — still worth doing, and it stacks with (1): a batched
   ITEMIZE would cut the per-item pgstac cost from ~4 ms to well under 1 ms and
   drop the job count per item. But it is now an optimisation, not the fix,
   and the `flow_stats` per-transition batching stays mandatory on its own
   merits.

Note the ordering matters: batching first, without (1), would have hidden the
O(n²) behind a bigger constant and let it resurface at scale.

---

## M3-S-B · §2 byte-volume arithmetic at 2.6M items/day — RESULTS (2026-09-01)

ROADMAP §2 was written against 100k items/day and concluded "tens to hundreds
of TB/day". This redoes it at the M3 envelope and pins which of §2's three
absorption mechanisms actually carry the load.

### Stated assumptions (the numbers below are only as good as these)

No NOAA size census was available, so the distribution is a declared model of
NOAA-class products, chosen to span the three shapes that behave differently:

| tier | example class | size | share of ITEMS | share of BYTES |
|---|---|---:|---:|---:|
| small | radar / gridded products (NEXRAD chunk, MRMS grid) | 2 MB | 60% | 3.6% |
| medium | geostationary imager slice (GOES ABI L1b, per band/sector) | 25 MB | 30% | 22.3% |
| large | polar-orbiter granule (VIIRS/JPSS SDR) | 250 MB | 10% | 74.2% |

**Mean 33.7 MB/item.** The distribution is deliberately heavy-tailed, because
that is the property every conclusion below turns on: 10% of items carry 74% of
the bytes.

Item rates from the settled inputs: **2.6M items/day ingest-origin**, plus
≤1× process output ⇒ 5.2M catalog writes/day. Process outputs are assumed
**derived and smaller** — 25% of ingest bytes — since a transform that emitted
its input verbatim would not be worth running.

### Byte volume

| flow | TB/day | sustained |
|---|---:|---:|
| ingest-origin, read from source | 87.6 | 1.01 GB/s |
| ingest-origin, written to canonical (copy mode) | 87.6 | 1.01 GB/s |
| process outputs written | ~21.9 | 0.25 GB/s |
| **through workers, copy mode, no delivery** | **~197** | **~2.3 GB/s** |
| each delivery destination, streamed | +87.6 | +1.01 GB/s |
| each delivery destination, S3→S3 server-side copy | ~0 | ~0 |

§2's "tens to hundreds of TB/day" holds and now has a number on it:
**~88 TB/day in, ~197 TB/day through the workers in full copy mode.**

Against M3-S-A's measured transfer rate (an 8 MB `ingest_fetch` get→put in
67 ms ≈ 125 MB/s each way, local MinIO), 1.01 GB/s of copy needs **≥9
concurrent fetch workers doing nothing else** — before ITEMIZE, dispatch or
delivery. This is the same conclusion S-A reached from the other side, and it
is the arithmetic case for the M3-S-C concurrency decision.

### Copy vs reference — the correction that matters

§2 mechanism 1 (`storage_mode: reference`) is worth less than §2 implies,
because of a detail in the shipped implementation:

> **Reference mode avoids the WRITE. It does not avoid the READ when the
> metadata strategy is `raster_auto`.**

`SourceAdapterByteSource.read` pulls the whole object from the source adapter
so rio-stac can open it (`ingest/extract.py`), exactly as copy mode reads it
back from canonical storage. Reference mode therefore removes 87.6 TB/day of
writes and 87.6 TB/day of canonical storage growth, and removes **nothing**
from the read path. Only `defaults_only` and `sidecar` avoid the read — and
even `defaults_only` falls into the I-27 best-effort GDAL open (another full
read) unless `defaults.geometry = collection` is set.

Consequences for the M3 posture:

- **Reference mode is a STORAGE lever, not a bandwidth lever**, unless the
  association also uses a metadata strategy that does not open the raster.
- The high-leverage configuration is therefore **reference + sidecar** (or
  `defaults_only` with a collection geometry) for the large tier, where the
  producer already ships metadata alongside the granule. That is a
  documentation and defaults question, not an engineering one.
- The engineering lever for the read path is **ranged/streaming reads**, which
  is precisely M3-S-E's decision. rio-stac only needs the GeoTIFF header and
  overviews, not the whole object; today it gets the whole object because
  `MemoryFile` takes bytes.

### Storage growth vs retention

Canonical storage growth per day, by posture (ingest + process outputs):

| posture | TB/day | 7-day retention | 30-day retention |
|---|---:|---:|---:|
| copy everything | 109.5 | 767 TB | 3.3 PB |
| reference the LARGE tier only, copy the rest | 44.5 | 312 TB | 1.3 PB |
| reference everything ingested (outputs still copied) | 21.9 | 153 TB | 657 TB |

Referencing the large tier alone — 10% of items — removes **74% of the ingested
bytes** and, once process outputs are added back, **59% of total storage
growth**. That is the single highest-leverage byte decision available, and it
needs no code: it is which associations operators configure as `reference`.
Process outputs are the floor: they are written by the platform, so no
reference posture removes them.

### GC and retention are NOT bulk paths today — a real gap

ROADMAP §1 said the retention sweep and `asset_gc` collector are bulk paths.
The shipped implementation is batched but **under-sized by more than an order
of magnitude** for this envelope:

- `retention_gc` and `asset_collect` run on `*/5 * * * *` — 288 ticks/day.
- Each tick processes `GC_BATCH_ITEMS` (default **500**), *per collection* for
  the retention half and *globally* for the collect half
  (`gc/sweep.py`, `gc/repo.py`).

Steady state at 2.6M items/day expiring means 2.6M marks/day and 2.6M prefix
deletions/day.

| leg | capacity/day at defaults | needed | shortfall |
|---|---:|---:|---:|
| `retention_gc` (per collection) | 144,000 | 2.6M for a single high-volume collection | **18×** |
| `asset_collect` (global) | 144,000 | 2.6M+ | **18×** |

The retention half is per collection, so it scales with the number of
collections carrying retention — 18 busy collections would cover it. The
**collect half does not**: `list_due_marks(batch_limit)` is a single global
query, so its 144k/day is the platform's total byte-deletion capacity
regardless of how the load is spread. **`asset_collect` is the harder ceiling
and the one M3 must raise.**

Worse, its unit of work is a *prefix deletion* (list + delete every object
under `assets/{collection}/{item}/`), so the tick's cost is object-count, not
mark-count: 2.6M items/day at ~1.5 files/item is ~4M object deletions/day
(~45/s sustained) — well within S3's `DeleteObjects` (1000 keys/call) if
batched, but the sweep deletes prefix-by-prefix, serially, on the same
single-concurrency worker S-A found. This lands squarely on M3-S-C and
M3-S-D as well.

Note the failure mode is quiet: marks that are not collected simply stay open,
so the symptom is a storage bill and a growing `asset_gc` table, not an error.
A backlog alert on open marks is worth considering in the M3 spec.

### Delivery fan-out

§2 mechanism 2 (S3→S3 server-side copy) is implemented and correctly gated
(`delivery/transfer.py: can_server_side_copy`) — but only when the destination
is an `s3` connection **on the platform's own endpoint**, or both sides are
real AWS with no custom endpoint. Every other destination streams the bytes
through a worker: +87.6 TB/day, +1.01 GB/s, per destination.

§2 mechanism 3 (honest protocol claims) therefore holds and should be restated
in the M3 spec as a hard number rather than a posture: **an FTP/SFTP delivery
destination cannot be fed at envelope volume** — at 1 GB/s it would need ~8
saturated 10 GbE streams through the workers. FTP/SFTP destinations are an
NRT-subset feature, and the spec should say so with this arithmetic attached.

### Recommendation to M3-S-G

1. **Restate §2's envelope with these numbers** (88 TB/day in, 197 TB/day
   through workers in full copy mode) rather than "tens to hundreds".
2. **Correct the reference-mode claim**: it is a storage lever; it becomes a
   bandwidth lever only when paired with a metadata strategy that does not open
   the raster. Document the reference + sidecar combination as the intended
   high-volume configuration.
3. **Raise `asset_collect`'s ceiling** — it is the one GC leg whose capacity
   does not scale with the number of collections, and it is 18× short. Batch
   the prefix deletions (`DeleteObjects`, 1000 keys/call) and size the batch
   against the envelope, not against 100k/day.
4. **Feed the read-path number into M3-S-E**: 88 TB/day of reads exists whether
   or not bytes are copied, and it is what a streaming/ranged read would
   actually remove.

---

## M3-S-C · Concurrency & HA plan (I-40) — RESULTS (2026-09-01)

I-40 asked leader election vs partitioned ownership vs scale-workers-only.
M3-S-A reframes the question: **the platform is not running one worker per
box, it is running one JOB at a time in total.** `run_worker_async()` is called
with no arguments, so Procrastinate's `WORKER_CONCURRENCY = 1` applies to
ingest, dispatch, delivery, processes and every sweep, together. Multi-instance
anything is premature while that is true.

### Decision

**Scale in-process concurrency first; keep every component a singleton;
leave leader election in Phase 8.** In order:

1. **Make worker concurrency a setting** and raise it. This is the entire
   remaining ~5.5× gap S-A measured, and it costs one parameter.
2. **Split the byte-heavy stage onto its own queue with its own, smaller
   concurrency** (see the memory collision below). Procrastinate supports
   per-queue workers; tasks are all on the default queue today.
3. **Multi-instance stays available but unneeded.** It already works safely
   (below); M3 does not need it to reach the envelope, so it is a deployment
   option, not an M3 slice.

Leader election earns nothing here: the periodic scheduler already dedupes
across processes, and every singleton component is a periodic *job* on the
shared worker rather than a separate process, so there is no leader to elect.

### Multi-instance safety audit

Running N pipeline containers today is safe on the scheduling side:
`procrastinate_periodic_defers` carries `UNIQUE (task_name, periodic_id,
defer_timestamp)` — verified on the live schema — so N processes cannot
double-defer a periodic tick. Any worker may execute it; exactly one does.

Per-leg claim audit (the I-40 question, extended to every ledger):

| leg | mechanism | safe under concurrency |
|---|---|---|
| `item_events` dispatch | `FOR UPDATE SKIP LOCKED` | ✅ |
| `connection_checks` drain | `FOR UPDATE SKIP LOCKED` | ✅ |
| `delivery_backfills` | `FOR UPDATE SKIP LOCKED` | ✅ |
| `process_runs`, `process_checks` | `FOR UPDATE SKIP LOCKED` + conditional UPDATE | ✅ |
| staged-upload finalize | claim + `stale_claim` no-op | ✅ |
| `flow_stats` rollup | `SELECT … FOR UPDATE` | ✅ |
| `ingest_files` sweeps | single-statement `UPDATE … WHERE status = …` | ✅ |
| **`ingest_files` GROUP → FETCH** | **read-then-write guard, not atomic** | ❌ |
| `asset_gc` collect | list-then-delete, no claim | ⚠️ duplicated, idempotent |
| retention expiry | list-then-mark-then-delete | ⚠️ duplicated, tolerated |

### The one real defect: `ingest_files` has a guard, not a claim

`fetch_stage` re-reads the row and skips it unless it is still `settled`
(`ingest/fetch.py`) — an idempotency guard, and a good one. But it is
**read-then-write**:

```python
latest = await repo.get_latest_ledger(...)          # reads 'settled'
if latest is None or latest.status != STATUS_SETTLED:
    continue
await repo.set_ledger_fields(latest.id, status=STATUS_FETCHING, ...)   # unconditional UPDATE
```

Two workers can both read `settled` and both proceed. `group_stage` compounds
it: `list_ledger_by_status(association_id, 'settled')` is a plain SELECT with
no claim, so two overlapping GROUP jobs for one association hand the same rows
to two FETCH batches.

Consequence is **duplicated work and inflated telemetry, not corruption**: both
workers fetch the same bytes and write the same canonical key, both ITEMIZE and
upsert the same item id (idempotent), but `flow_stats` is bumped twice and two
outbox events are emitted — so an operator's ingest counters and any expectation
judged against them would be wrong, and the byte cost doubles. At the S-B
envelope, doubling the byte path is the expensive half.

**Fix (M3 slice, cheap and well-precedented here): compare-and-set.**

```sql
UPDATE stac_higher.ingest_files
   SET status = 'fetching', item_id = %s, updated_at = now()
 WHERE id = %s AND status = 'settled'
```

Skip the file when `rowcount = 0`. Atomic, no lock held across the fetch, no
new table, and it makes the guard the claim it already reads as. This is the
same shape `process_runs.claim_due_runs` uses.

The two ⚠️ rows need no fix: `gc.delete_item` is deliberately failure-tolerant
(a second delete of a gone item is swallowed), `mark_asset_prefix` is idempotent
by the open-key unique index, and S3 prefix deletion is idempotent. They waste
work under concurrency; they do not break. Worth a note in the spec, not a
slice.

### How much concurrency, and the collision with I-19/I-26

From S-A's per-stage costs and S-B's size model:

| workload | per-item work | slots for 30 items/s |
|---|---:|---:|
| metadata-light (64 KB, `defaults_only`) | fetch 25 ms + itemize 44 ms | ~3 |
| delivery (64 KB) | 51 ms | ~2 |
| byte-realistic (33.7 MB mean, copy) | fetch ~0.28 s + itemize ~55 ms | **~10** |

So ~12–16 slots for the byte-realistic ingest + delivery mix — which agrees,
from the other direction, with S-B's "≥9 concurrent fetch workers for 1.01
GB/s".

**And that is exactly where I-19/I-26 detonate.** S-A measured the envelope as
`RSS ≈ 255 MiB + 3 × asset_size × concurrency`. At concurrency 16 against the
250 MB large tier that is ~12 GB of resident memory in one container — for a
workload whose whole point is that 10% of items carry 74% of the bytes.

> **Concurrency and streaming are one decision, not two.** Raising concurrency
> without addressing whole-object buffering converts a throughput problem into
> an OOM. M3-S-E must not be deferred past the concurrency slice.

Three ways out, for the spec to choose between (E decides):
- **(a) Separate queues.** A `bytes` queue (FETCH, and reference-mode EXTRACT
  reads) with concurrency ~4, and a `metadata` queue with concurrency ~16.
  Bounds memory at `4 × 3 × max_asset` without capping the cheap stages.
  Costs a `queue=` argument on `register_task` and a second worker config;
  every task is on the default queue today.
- **(b) Streaming** (the S-E option): per-item memory becomes a chunk, and
  concurrency stops being memory-coupled at all.
- **(c) A computed cap**: `concurrency = memory_budget / (3 × max_asset_size)`.
  Honest, self-limiting, and leaves the platform unable to reach the envelope
  on large-tier data — the honest-limits posture, and the right answer only if
  (a) and (b) are both refused.

(a) is the cheapest thing that makes a raised concurrency *safe*, and it
composes with (b) rather than competing with it. Recommend (a) in M3 and let
S-E decide whether (b) lands in M3 or Phase 8.

### What this hands forward

- **M3-S-D**: the compare-and-set above is a write-path change on the same
  rows D is batching; they should be designed together, not sequenced.
- **M3-S-E**: the memory collision is now quantified, and E is on M3's critical
  path rather than optional.
- **M3-S-F**: at concurrency 16 the job rate rises with it — F's arithmetic
  should be run at the chosen concurrency, not at 1.
- **Phase 8**: leader election and partitioned ownership stay deferred, with
  the reason now recorded as *measured* rather than assumed — the periodic
  scheduler already dedupes, so there is nothing a leader would add.

---

## M3-S-D · Hot-path write batching — RESULTS (2026-09-01)

Measured against the live stack, then read off the code for the amplification
count. The brief called `flow_stats` batching *mandatory*; the measurement
says it is mandatory as **headroom and lock hygiene**, not as the blocker —
and it turned up a bigger write-path cost the brief did not list.

### Write amplification per ingested item

Copy mode, one source file, one delivery destination, `defaults_only`. Each
row below is a separate statement **on its own new connection and
transaction** (see the pooling finding):

| stage | write | batched today? |
|---|---|---|
| DISCOVER | INSERT `ingest_files` (`seen`) | per file, per tick |
| DISCOVER (next poll) | UPDATE → `settled` | per file |
| DISCOVER | `bump_flow_stats(files, bytes)` | ✅ **once per tick** |
| FETCH | UPDATE → `fetching` | per file |
| FETCH | UPDATE → `stored` | per file |
| ITEMIZE | pgstac upsert (+ outbox trigger INSERT) | ❌ one call per item |
| ITEMIZE | UPDATE → `itemized` | per item (multi-row capable) |
| ITEMIZE | `bump_flow_stats(items, bytes, latency)` | ❌ **per item** |
| DISPATCH | claim `item_events` | ✅ batch 100 |
| DISPATCH | INSERT/UPSERT `delivery_log` (`pending`) | per item |
| DISPATCH | `_apply_flow_stats` (→ pending) | ❌ **per item** |
| DISPATCH | `mark_processed` | ✅ batch |
| DELIVER | UPDATE `delivery_log` → `delivering` | per item |
| DELIVER | `_apply_flow_stats` (→ delivering) | ❌ **per item** |
| DELIVER | UPDATE `delivery_log` → `delivered` | per item |
| DELIVER | `_apply_flow_stats` (→ delivered) | ❌ **per item** |

**≈16 statements and ≈14 connections per item**, of which **4 are jsonb
read-modify-write under `SELECT … FOR UPDATE` on an association row.**

### `flow_stats` — measured

200 real `bump_flow_stats` calls against a live association row:

| case | ms each | bumps/s |
|---|---:|---:|
| serial | 4.86 | 206 |
| concurrent × 4 | 2.17 | 461 |
| concurrent × 16 | 2.18 | 459 |

**Flat past concurrency 4 — that is the row lock, exactly as designed.** The
ceiling is ~460 bumps/s **per association row**, and it does not move with
workers.

At the M3 target one high-volume ingest association does 30 bumps/s and its
delivery association 90/s, so **`flow_stats` is not the binding constraint**
— it sits at ~10–20% of a ceiling it cannot exceed. What it *does* cost is
queueing: at concurrency 16 every ITEMIZE on one association waits behind the
others for ~2.2 ms, which is meaningful against a 44 ms itemize and is pure
waste.

So: batch it (it is cheap, and DISCOVER already shows the pattern — one rollup
per tick, not per file), but the spec should say **why**: to remove a
per-association serialization point and reclaim ~5% of the hot path, not
because 460/s is close to 60/s. Overstating it would misrank the slices.

One latent hazard worth naming: `_apply_flow_stats` rebuilds `counts` with a
full `SELECT status, count(*) … GROUP BY status` over `delivery_log` for the
association whenever the `counts` key is absent. On a delivery association with
millions of rows that is a table scan **inside the row lock**. It fires once
per association today, but any migration or manual edit that drops the key
re-arms it.

### The cost the brief did not list: no connection pooling

Every repo method is `async with await psycopg.AsyncConnection.connect(...)`
— a **fresh connection per statement**, everywhere in the pipeline. Measured
connect cost: **4.19 ms**.

At ~14 connections per item and 30 items/s that is **~420 connections/s ≈ 1.8
core-seconds of connect work per wall-clock second** on the client, plus a
backend fork per connection on Postgres. At the 60 items/s total budget it
doubles.

This is invisible today because concurrency is 1 and the pgstac call dominated
everything. Once S-C raises concurrency, connection churn becomes a first-order
cost and a Postgres `max_connections` risk. **A pool belongs in the same slice
as the concurrency raise**, not after it.

(Note the contrast with M3-S-A's finding that pooling would buy ~2% on the
pgstac path: there, one 4 ms connect sat against a 245 ms call. Here it sits
against sub-millisecond statements, and the ratio inverts.)

### `delivery_log` / `ingest_files` and the ADR 0012 constraint

Both are UNIQUE-key upsert models, and ADR 0012 anchors table hygiene on that.
Batching must not break it — and it need not: the codebase already contains the
pattern that satisfies both. `delivery/repo.py`'s backfill insert is a single
statement over `UNNEST(%s::text[], %s::timestamptz[])` with
`ON CONFLICT (association_id, item_id) DO NOTHING … RETURNING id`. One
statement, N rows, upsert semantics intact, and the returned ids say which rows
were actually new.

Every per-item write above can take that shape. Specifically:

- **ITEMIZE ledger + flow_stats**: accumulate per ITEMIZE batch, then one
  `UPDATE … WHERE id = ANY(...)` (already exists as `set_ledger_status_many`)
  plus one aggregated bump.
- **`delivery_log` transitions**: batch per delivery batch, `UPDATE … FROM
  UNNEST(...)`, preserving the CASE-based attempts logic already in the
  single-row statement.
- **pgstac upsert**: the big one. S-A measured 3.8 items/s at batch 1 versus
  375/s at batch 100 and 709/s at batch 500 (and with `use_queue` on: ~250/s
  flat at batch 1). Batching stacks with the `use_queue` fix rather than
  replacing it — see the ordering warning in S-A §7.

### Outbox claim batch sizing

`dispatch_once` claims 100 events per call, drain-until-empty across up to
`max_batches`. Measured: draining 3000 seeded events took 11.8 s per
`dispatch_poll` call ≈ **127 events/s**, and it **monopolised the single worker
slot for those 11.8 s**.

Two consequences, and they pull in opposite directions:
- The batch is not too small for throughput (127/s ≫ 60/s).
- It is too *coarse* for fairness on a shared worker. A 12-second job is a
  12-second ingest outage.

With S-C's concurrency raise the fairness problem mostly dissolves (other slots
keep running). The spec should nonetheless bound the drain — `max_batches` per
tick — so one enormous backlog cannot hold a slot indefinitely, rather than
shrinking the batch, which would only trade throughput for latency.

### `/metrics` overhead

Presumed fine and confirmed fine: the counters are in-process
`prometheus_client` increments against a local registry, and the exposition
endpoint is scraped on demand. No measurable cost appeared in any run. No
action.

### Recommendation to M3-S-G

Slice order within the write path, cheapest-first and each independently
verifiable:

1. **Connection pool** — one change, benefits every statement above, and is a
   *precondition* for the concurrency raise rather than an optimisation of it.
2. **Batch the pgstac upsert** in ITEMIZE and finalize (the largest single
   win after S-A's `use_queue`).
3. **Batch `flow_stats`** per job/tick — DISCOVER's existing shape is the
   template. Removes the per-association serialization point.
4. **Batch the `delivery_log` transitions** using the `UNNEST` + `ON CONFLICT`
   pattern already in the backfill path, so the ADR 0012 upsert model is
   preserved by construction.
5. **Bound the dispatch drain** with `max_batches`, not a smaller batch.

Items 1–4 all touch the same rows M3-S-C's compare-and-set claim touches; per
S-C, design them as one write-path slice rather than sequencing them.

---

## M3-S-E · Streaming decision (I-19 / I-26) — RESULTS (2026-09-01)

The brief offered three options: (a) true streaming, (b) documented copy-mode
size limits leaning on `reference`, (c) defer to Phase 8. It expected the
decision to hinge on whether NOAA-class assets are large enough to matter.

**They are, and the decision changed for a different reason: streaming turns
out to be CHEAP, and — per M3-S-C — it is a precondition for the concurrency
raise rather than an optimisation of it.**

### Decision: **(a) — true streaming, in M3, both halves.**

Measured, against the live stack, on a 64 MB tiled GeoTIFF.

**EXTRACT (I-26).** Today: `rasterio.MemoryFile(raster_bytes)` over the whole
object. Alternative: hand GDAL a `/vsis3/` path and let it range-read.

| | seconds | RSS delta | resulting STAC item |
|---|---:|---:|---|
| `MemoryFile` (today) | 0.400 | **+145 MB** | — |
| `/vsis3` ranged | 0.302 | **+55 MB** | **byte-identical** |

`create_stac_item(..., with_proj=True, with_raster=True)` — i.e. including the
`raster:bands` statistics that actually touch pixels — produced an identical
item both ways (same bbox, same `proj:code`, same band mean to three decimals),
in less time, at a third of the memory. GDAL's own block cache does the work,
and it is capped by `GDAL_CACHEMAX`: memory becomes a **knob**, not a multiple
of the asset.

Note the 145 MB for a 64 MB object — 2.3× — which is exactly the "2–3× asset
size" envelope M3-S-A inferred from container RSS. The two measurements agree.

**FETCH (I-19).** Today: `adapter.get() -> bytes` then `platform.put_object`.

| strategy | seconds | RSS delta | scales with object size? |
|---|---:|---:|---|
| buffered get → put (today) | 0.870 | +64.8 MB (≈1×) | **yes** |
| streamed get → multipart put | 0.444 | +49.8 MB | no — bounded by `chunksize × concurrency` (32 MB as configured) |
| **server-side copy** | **0.116** | **0** | no — **zero bytes through the worker** |

Server-side copy is **7.5× faster than today and moves no bytes at all**. The
gate for it already exists in this codebase, written for the delivery path
(`delivery/transfer.py: can_server_side_copy`), and `S3Adapter` already
implements `copy_object_from`. FETCH is the same question in the other
direction and can reuse both.

### The memory envelope, written down

M3-S-C asked for this explicitly, because concurrency and memory are one
decision:

| | per-worker peak RSS |
|---|---|
| **today** | `255 MiB + ~2.3 × asset_size × concurrency` |
| **after streaming** | `255 MiB + (GDAL_CACHEMAX + multipart_chunksize × transfer_concurrency) × concurrency` |

The second is **independent of asset size**. That is the whole point: it is
what makes S-C's concurrency of 12–16 safe. At concurrency 16 against the
S-B large tier (250 MB), today's formula predicts ~9 GB; the streamed formula
with a 64 MB GDAL cache and 32 MB transfer buffers predicts ~1.8 GB.

### Why not (b) or (c)

- **(b) — document size limits, lean on `reference`** — is now clearly wrong,
  and M3-S-B says why: reference mode **does not avoid the read**. A reference
  association with `raster_auto` buffers the whole object in EXTRACT exactly as
  copy mode does. Leaning on reference to dodge a memory problem would not have
  dodged it.
- **(c) — defer to Phase 8** — would leave M3 unable to raise concurrency,
  which is the entire remaining throughput gap (S-C). Deferring the cheap half
  of a coupled pair is how the expensive half becomes unreachable.

### Caveats to carry into the slice

- **Credentials reach GDAL.** Copy-mode EXTRACT reads the *canonical* bucket
  with the platform's own credentials — trivial. Reference-mode EXTRACT reads
  the *source* bucket, so the connection's decrypted credentials would go into
  a GDAL/`rasterio` session. The worker already decrypts them (`build_adapter`)
  so nothing new is exposed, but it is a new place they live and belongs in the
  slice's review, not in its footnotes.
- **`/vsis3` only reaches s3.** SFTP/FTP sources keep the buffered `get()`.
  That is consistent: reference mode is already s3-only, and ROADMAP §2's
  honest-limits posture (with M3-S-B's arithmetic attached) says those
  protocols are NRT-subset volumes.
- **rasterio refuses raw `AWS_*` GDAL options** — `rasterio.Env(AWS_S3_ENDPOINT=…)`
  raises `EnvError: GDAL's AWS config options can not be directly set`. The
  working form against MinIO is
  `rasterio.Env(session=AWSSession(..., endpoint_url="host:port"), AWS_HTTPS="NO",
  AWS_VIRTUAL_HOSTING="FALSE")`. Recorded because it cost a debug cycle and the
  error message points the wrong way.
- **Server-side copy is conditional**, and FETCH's condition is not delivery's:
  the destination (the platform bucket) must be able to read the *source*
  bucket. Same endpoint is necessary but not sufficient across accounts. The
  slice needs the streamed path as the general case and server-side copy as the
  fast path, with a fallback on failure — which is exactly what the delivery
  worker already does.

### Recommendation to M3-S-G

One slice, "bounded-memory byte path", with three verifiable parts:

1. **EXTRACT reads through a URI, not a buffer.** Swap the `MemberByteSource`
   seam from `-> bytes` to something GDAL can open. The seam already exists
   and already has two implementations, so this is a change of return type,
   not of architecture.
2. **FETCH: server-side copy when the gate allows, streamed multipart
   otherwise.** Reuse `can_server_side_copy` and `copy_object_from`.
3. **Make the memory bound explicit and configured** (`GDAL_CACHEMAX`,
   transfer chunk size and concurrency), so the envelope above is a setting a
   deployment can size against its box rather than an emergent property.

Sequence it **with or before** the concurrency raise (S-C), never after.

---

## M3-S-F · Queue-backend headroom — RESULTS (2026-09-01)

The brief called this "paper only". It is mostly paper, and the paper answer is
reassuring — but a live check of the queue's own tables turned up the one thing
about Procrastinate that *does* threaten the shared Postgres at M3 scale, and
it is not throughput.

### Jobs per second at the M3 budget

From M3-S-A's structural count, per **item**:

| leg | jobs per item |
|---|---:|
| ingest | **2** (`ingest_fetch`, `ingest_itemize`) |
| delivery | **1 per destination** (`deliver`) |
| dispatch | batched — 1 job per tick, not per item |
| finalize / process runs | batched — 1 job per batch |
| DISCOVER / GROUP | 1 each per association per poll tick |

At the settled budget — 30 items/s ingest-origin + 30 items/s process output,
one delivery destination each:

```
ingest         30 × 2 =  60 jobs/s
deliver        60 × 1 =  60 jobs/s
process runs   30 / 10 =  3 jobs/s   (batch 10)
periodic ticks         ≈  0.3 jobs/s (≈15 tasks, mostly per minute)
                        ─────────────
                        ≈ 125 jobs/s
```

### Measured Procrastinate costs (live, this stack)

| operation | ms each | per second |
|---|---:|---:|
| defer, one statement each | 0.207 | **4,830** |
| defer, `executemany` (the `batch_defer` shape) | 0.025 | **40,766** |
| `procrastinate_fetch_job_v2` (claim, one at a time, committed) | 0.520 | **1,923** |

The claim is the tighter side, as expected — it is the contended one. **125
jobs/s against ~1,900 claims/s is ~7% utilisation.**

`enqueue_batch` already uses `batch_defer_async`, so the enqueue side is on the
40k/s path rather than the 4.8k/s one wherever it matters.

**Verdict: Procrastinate has roughly an order of magnitude of throughput
headroom at the M3 budget on a single shared Postgres.** The comfort ceiling
sits around **~1,000 jobs/s** (half the measured claim rate, leaving room for
the application's own ~840 writes/s from M3-S-D on the same instance) — i.e.
around **8× the M3 budget**. Nothing here argues for SQS in M3, and the number
Phase 8 wants for its Procrastinate→SQS boundary is: *a deployment sustaining
more than a few hundred items/s of catalog write, or one that cannot give the
queue its own Postgres.*

Two caveats on the number: it was measured single-connection and serial, so it
is a floor, not a peak; and it was measured on an idle instance, so real
contention with the application's writes will reduce it.

### The real risk: nothing prunes the queue's tables

Live, after the S-A load runs (a few thousand items):

```
procrastinate_jobs      25,087 rows (25,087 succeeded, 45 failed)   7,952 kB
procrastinate_events    75,396 rows  =  3 events per job            8,656 kB
```

**Every finished job and its three status events are retained forever.**
Nothing in this repo prunes them: M2-G's `history_retention` sweep (ADR 0012)
covers `item_events`, `audit_log`, `delivery_log`, `ingest_files` and
`connection_checks` — the *application's* tables — and says nothing about the
queue's.

At ~660 bytes per job across both tables:

| rate | rows/day | growth/day |
|---|---:|---:|
| 125 jobs/s (M3 budget) | 43.2M | **~7.1 GB/day** |

That is larger than every application table combined, on the same instance the
catalog is on, growing without bound — and it degrades the claim path, since
`procrastinate_fetch_job_v2` works against a table that is 99.9% dead rows.

The facility exists and is simply not configured:
- `JobManager.delete_old_jobs(...)` — a Python-side prune, the natural fit for
  a periodic job alongside the M2-G sweep.
- `Worker(delete_jobs=...)` — a deletion policy applied as jobs finish
  (`never` today, by omission).

**Recommend `delete_jobs` on the worker for the succeeded path plus a periodic
`delete_old_jobs` for the stragglers**, so the steady state is bounded by
retention rather than by uptime. Note the trade: deleting finished jobs removes
the per-job history an operator might want when debugging a failure, so the
policy should keep failures. That is exactly what a "successful only" policy
does.

This belongs in M3, not Phase 8: it is a consequence of the *rate*, and M3 is
the milestone that creates the rate.

### LISTEN/NOTIFY

The defer path fires `procrastinate_notify_queue_job_inserted_v1` per insert —
~125 NOTIFYs/s at budget. Postgres handles that comfortably (NOTIFY is cheap
and the payload is empty); it is also the wake path that keeps the worker off
its 5 s poll floor. No action, but it is a reason **not** to batch defers so
aggressively that wakes become rare — latency, not throughput.

### M2-G sweep load

`history_retention` is hourly and conservative by design (ADR 0012). At M3
rates its per-run working set grows with the tables it prunes; nothing measured
here suggests it needs to change, but it should be re-checked once the M3
implementation raises the write rate, since ADR 0012 sized it against a much
smaller platform.

### Recommendation to M3-S-G

1. **No queue-backend change in M3.** ~7% utilisation with ~8× headroom to a
   conservative ceiling. Record the number so Phase 8's Procrastinate→SQS
   boundary is an argument rather than a preference.
2. **Add queue-table retention** (`delete_jobs` policy + a periodic
   `delete_old_jobs`), keeping failures. Unbounded growth of ~7 GB/day is the
   only queue-side finding that bites at M3 scale.
3. **Re-run this arithmetic at the chosen concurrency** (M3-S-C): the job rate
   above is a property of the workload, not of concurrency, but the *claim*
   rate is contended and its measured ceiling was single-connection.
