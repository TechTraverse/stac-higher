# M3 — NOAA-scale readiness — design

**Date:** 2026-09-01
**Status:** awaiting lead approval
**Scope source:** ROADMAP §9 "Named milestones" (M3), §2 (scale envelope), §10
(Postgres-queue ceiling, scheduler/monitor HA, processes multiply throughput);
Phase 9 spec §12 (the throughput arithmetic M3 consumes).
**Measured inputs:** `2026-08-31-m3-scoping-notes.md` — every number in this
spec comes from the M3-S-A…M3-S-F findings appended there, not from estimates.
**Issues addressed:** I-19, I-26 (M3-C), I-40 (settled by measurement, not
deferred). **Issues opened:** I-82 (the byte model is declared, not measured),
I-83 (streaming reaches object stores only).

---

## 1. What the scoping found, in one page

The scoping queue was seeded expecting M3 to be a re-architecture: batching,
concurrency, maybe a queue backend change. It is not. **Four specific,
individually small defects account for the entire gap**, and one of them is a
database setting.

| # | Finding | Measured | Where |
|---|---|---|---|
| 1 | `pgstac.update_partition_stats` runs **inline on every write call** and scans the whole destination partition twice | 13 ms/item at an empty partition → **654 ms/item at 5,200**; slope 0.125 ms per existing item. O(n) per insert, O(n²) to fill | S-A |
| 2 | The worker runs at Procrastinate's default **`concurrency = 1`** — one job at a time, for the entire service | ingest and delivery serialize into phases: **~11 items/s end-to-end** | S-A, S-C |
| 3 | Whole-object buffering in FETCH and EXTRACT | **+145 MB RSS for a 64 MB object** (2.3×) — which makes (2) unfixable without (3) | S-A, S-E |
| 4 | **No connection pooling**, anywhere; ~14 fresh connections per item at 4.19 ms each | ~420 connections/s ≈ **1.8 core-seconds of connect per wall-clock second** at budget | S-D |

Fixing (1) alone took the real pipeline from **2–3.5 items/s to a sustained
22 items/s**, single worker, no code change. The remaining ~5.5× to the
60 items/s total budget is (2), which requires (3) and (4) to be safe.

Two further findings are not throughput but would fail the platform at this
rate anyway:

| # | Finding | Measured | Where |
|---|---|---|---|
| 5 | `asset_collect` — the **only** byte-deletion path — is a single global batch of 500 per 5-minute tick | 144k/day capacity against 2.6M/day needed: **18× short**, and it fails *quietly* | S-B |
| 6 | Nothing prunes Procrastinate's own tables; 4 rows retained per job forever | **~7.1 GB/day** at budget, unbounded, on the catalog's instance | S-F |

And one correctness defect that only appears once (2) lands:

| # | Finding | Where |
|---|---|---|
| 7 | `ingest_files` GROUP→FETCH is a **read-then-write guard, not a claim** — the only ledger in the platform that is not `FOR UPDATE SKIP LOCKED`. Under concurrency, two workers fetch the same bytes and double-bump `flow_stats` | S-C |

Three things the scoping **cleared**, so the spec does not spend slices on them:

- **The queue backend.** ~125 jobs/s at budget against a measured ~1,900
  claims/s — ~7% utilisation. No SQS in M3 (S-F).
- **rio-stac EXTRACT as a CPU cost.** ~11 ms on an 8 MB GeoTIFF against a 44 ms
  baseline. I-26 is a *memory* issue, not a throughput one (S-A).
- **`flow_stats` as a throughput ceiling.** ~460 bumps/s per association row
  against 30–90/s needed. Still worth batching — to remove a serialization
  point, not because the ceiling is near (S-D). The seeded brief called this
  "mandatory at this rate"; the measurement says the action is right and the
  stated reason is wrong, and misranking it would cost a slice.

---

## 2. Gate

M3's done-when, firmed from ROADMAP §9:

> A **load rehearsal on the auth-enforced local stack sustains the 60 items/s
> total catalog write rate for 30 minutes** — ~30 items/s ingest-origin through
> a copy-mode association, plus a synthetic process at ≥50% source share
> contributing ~30 items/s of output, plus delivery fan-out to one destination
> — with **no lost items** (offered = catalogued = delivered), **alerting
> functional throughout** (an induced stall raises and auto-resolves), **bounded
> memory** (worker RSS flat across the window, independent of asset size), and a
> **written load report** recorded M-gate style in ROADMAP §9.

Firmed against the seeded gate shape in four places, each for a measured
reason:

- **30 minutes, not "a defined window".** The two-poll settle check means a
  file cannot be fetched for ≥60 s after it lands (S-A §5), and DISCOVER's
  burst-then-drain shape needs several poll cycles before the rate is steady.
  Under ten minutes measures the transient.
- **"No lost items" made checkable as offered = catalogued = delivered.** The
  harness knows what it offered; the gate should not accept "the counters
  looked right".
- **Bounded memory added.** It is the property S-E buys and the one that would
  silently regress: a run can pass on throughput and still be one asset-size
  change from an OOM.
- **Auth-enforced.** Every S-A measurement was on the plain stack; the gate
  must not be the first time the enforced overlay sees this rate.

**Rehearsal driver:** `pipeline.loadgen` (built in M3-S-A), extended with a
process leg — see M3-H.

---

## 3. Slices

Worked top-down by the solo loop, one task per iteration, worktree off
`ai/main`, `npm run verify` (+ pipeline `pytest`/`ruff`) before merge.

| # | Slice | Touches | Rationale |
|---|---|---|---|
| **M3-A** | **pgstac write path**: `use_queue = true`, a drainer (pg_cron in cloud, a pipeline job locally), a `query_queue` depth metric, and a decision on `update_collection_extent` — §4 | pipeline + deployment config | Finding 1. The single highest-leverage change measured; 7–10× on the real pipeline |
| **M3-B** | **Connection pool** across the pipeline repos | pipeline | Finding 4. A *precondition* for M3-C, not an optimisation of it |
| **M3-C** | **Bounded-memory byte path**: EXTRACT reads through a URI (`/vsis3`) not a buffer; FETCH does server-side copy when the gate allows, streamed multipart otherwise; memory bound made explicit and configured | pipeline | Findings 3, I-19, I-26. Must land **with or before** M3-D |
| **M3-D** | **Concurrency**: worker concurrency as a setting; byte-heavy stages on their own queue with their own smaller concurrency; `ingest_files` compare-and-set claim | pipeline | Findings 2 and 7. The remaining ~5.5× |
| **M3-E** | **Write batching**: batch the pgstac upsert in ITEMIZE and finalize; batch `flow_stats` per tick; batch `delivery_log` transitions via the existing `UNNEST` + `ON CONFLICT` shape; bound the dispatch drain with `max_batches` | pipeline | Finding S-D. Stacks with M3-A rather than replacing it |
| **M3-F** | **GC at rate**: raise `asset_collect`'s ceiling (batched `DeleteObjects`, envelope-sized batch), and alert on an open-mark backlog | pipeline + app | Finding 5. The quiet failure |
| **M3-G** | **Queue-table retention**: `delete_jobs` policy on the worker (successful only) + a periodic `delete_old_jobs`, keeping failures | pipeline | Finding 6 |
| **M3-H** | **Rehearsal driver**: `pipeline.loadgen` gains a process leg and a sustained-rate mode; gate-shaped verification (offered = catalogued = delivered) | pipeline | The gate needs it |
| **M3-I** | **M3 load rehearsal** on the auth-enforced stack + load report + ROADMAP §9 evidence + promotion PR | lead only | The gate |

### Dependency spine

```
M3-A ─────────────────────────────────► (independent, do first — biggest win, no coupling)
M3-B ──► M3-C ──► M3-D                  (pool, then bounded memory, then concurrency)
                    └──► M3-E           (batching lands on the rows D's claim touches)
M3-F, M3-G ─────────────────────────────► (independent, may float)
M3-H ──► M3-I                           (driver, then gate)
```

**The one ordering that is not negotiable is `M3-C → M3-D`.** Raising
concurrency against whole-object buffering converts a throughput problem into
an OOM: S-E's envelope predicts ~9 GB of resident memory at concurrency 16
against the 250 MB size tier, versus ~1.8 GB after streaming.

**The second is `M3-A first`.** Batching (M3-E) before M3-A would hide the
O(n²) behind a bigger constant and let it resurface at production partition
sizes, where it is a far more expensive discovery.

M3-D and M3-E touch the same rows and should be designed together even though
they merge separately (S-C, S-D both say so).

---

## 4. M3-A — the pgstac write path

`pgstac_settings.use_queue = true` routes `update_partition_stats` into
`pgstac.query_queue` instead of running it inline. Measured effect: per-item
upsert cost goes from 13→654 ms (rising with partition size) to a **flat
3–4 ms**, and the live pipeline from 2–3.5 to 22 items/s.

### 4.1 What actually defers

All five `run_or_queue` call sites in pgstac 0.9.11, verified against the
pinned migration:

| queued work | fired from |
|---|---|
| `update_partition_stats` (the O(partition) scan + `ANALYZE`) | item write, via `update_partition_stats_q` |
| table CHECK constraints (`create_table_constraints`) | partition creation |
| partition maintenance (`maintain_partitions`) | partition creation |
| constraint validation | `validate_constraints` |
| **collection extent** — an `UPDATE collections` — but only when the separate `update_collection_extent` setting is on | inside `update_partition_stats` |

**Partition `CREATE TABLE … PARTITION OF` is `EXECUTE`d inline**, not queued
(`check_partition`). So an item still lands in the right partition with the
queue enabled and un-drained; what defers is the partition's *maintenance*.
This is what makes the residual risk **staleness, not correctness** — verified
here rather than assumed.

### 4.2 The drainer: pg_cron in cloud, a pipeline job locally

pgstac's intended mechanism is **pg_cron** calling `run_queued_queries`, and
that is the right answer wherever it exists — it keeps the drain in the
database with the queue, and it runs whether or not the pipeline is up.

**But pg_cron is not in our image.** Verified on
`ghcr.io/stac-utils/pgstac:v0.9.11`: `pg_available_extensions` offers only
`pg_partman`; installed are `plpgsql`, `postgis`, `btree_gist`, `unaccent`. It
*is* a parameter-group option on RDS/Aurora, so the split is:

- **Cloud (Phase 8):** pg_cron. The paved path.
- **Local / any Postgres without pg_cron (the M3 gate):** a periodic pipeline
  job. Portable, needs no extension, and is the fallback a self-hosted
  deployment will want anyway.

The spec deliberately keeps **both**, rather than picking one and leaving the
other deployment shape unserved. The pipeline job must be a no-op when a
database-side drainer is already configured, so the two cannot fight.

**`pgstac.run_queued_queries()` is a PROCEDURE — it needs `CALL`, not
`SELECT`** (a `SELECT` errors outright, so a wrong drainer fails loudly rather
than silently no-opping).

### 4.3 `update_collection_extent` — decide it here, and the coupling is helpful

It is a **separate pgstac setting**, currently `false`, and it gates a queued
`UPDATE collections SET content = jsonb_set(…, collection_extent(…))` fired
from inside `update_partition_stats`.

Worth deciding in this slice for a reason that predates M3: **nothing
maintains collection extents today.** Every collection sits at the extent it
was created with — locally, all of them are still `[-180,-90,180,90]`. The
platform's only `collection_extent` code is the ingest *geometry fallback*
(reading a collection's declared extent to stamp an item's geometry), never a
writer. So the advertised extent of every collection is operator-declared and
permanently stale.

The cost is smaller than it looks, and runs the helpful way:
`collection_extent(coll, FALSE)` — the form the queued statement uses —
aggregates `partitions_view`, i.e. the per-partition stats, **not the items**.
It is O(partitions), not O(items). So it is cheap *given* stats are being
maintained, and `use_queue` is precisely what makes maintaining them
affordable. On the write path it never would have been.

**Recommend turning it on together with `use_queue`**, and measuring — it
closes a real gap at a cost the queue already absorbs.

### 4.4 Where the database cost actually lands

Inside `update_partition_stats`, alongside the scan and `ANALYZE`, are two
`REFRESH MATERIALIZED VIEW` calls (`partitions`, `partition_steps`). Those
scale with **partition count**, not with write rate. So the drain cadence
should be tuned against partition count, not ingest rate — and that is a
number to *measure* in this slice, not to guess. Deferring this work does not
make it free; it makes it batched and deduped, which is a different and better
shape, but the slice should report what it costs.

### 4.5 Remaining obligations

1. **Ownership of the settings row.** The app owns migrations (ADR 0001), the
   `pgstac` schema is pypgstac's, and — now that pg_cron may own the drain —
   the setting and its drainer can live in different components entirely.
   **Open decision for the lead** — see §7, revised.
2. **A staleness bound.** The queue dedupes by query text
   (`ON CONFLICT DO NOTHING`), so its depth is bounded by the number of
   distinct partitions, not by write volume. The drainer's cadence therefore
   sets how stale a partition's stats may get, and that is the number to state
   and monitor.
3. **A depth metric on `query_queue`.** A drainer that silently stops is
   otherwise invisible (§5).

---

## 5. Testing & risk

- Per slice: vitest + pytest, `npm run verify` before every merge. No new
  cross-runtime config shapes are introduced, so no new contract fixtures are
  required — but M3-D's queue split changes the *operational* contract and
  belongs in `services/pipeline/README.md`.
- **M3-D is the riskiest slice.** It is the first time this platform runs more
  than one job at a time, and S-C's audit found exactly one ledger that cannot
  survive it. Mitigations: the compare-and-set claim ships *in the same slice*
  as the concurrency raise; concurrency is a setting with a conservative
  default so a deployment can back it out without a release; and S-C's audit
  table is the checklist for the review.
- **Concurrency exposes more than the ledger audit covered.** S-C audited
  *database-level claim semantics* across eleven legs and found one defect. It
  did **not** systematically review module-level mutable state, shared
  boto3/GDAL client reuse, or `asyncio.to_thread` pool sizing under 16
  concurrent jobs. That review is part of M3-D and is not already done — see
  the conceded objection in §8.
- **M3-C is second.** It changes how bytes move and where credentials live: a
  reference-mode `/vsis3` read puts a connection's decrypted credentials into a
  GDAL session. Nothing new is exposed (the worker already decrypts them via
  `build_adapter`) but it is a new place they live, and it belongs in the
  slice's review rather than its footnotes.
- **M3-A is third, and its risk is invisible in two ways.** A drainer that
  silently stops leaves search planning degrading with no error anywhere; and
  a deployment that never sets `use_queue` runs ~10× slower with nothing to
  say so. The slice ships a `query_queue` depth metric *and* a startup
  assert-and-warn on the setting (§7 decision 1) — the observability is the
  mitigation for both, and is not optional garnish.
- **M3-F deletes real bytes at a raised rate.** ADR 0011's mark-first-then-
  collect ordering and grace window are unchanged; only the batch size moves.
  The added risk is a bulk `DeleteObjects` with a wrong prefix, so the slice
  should keep prefix construction in `storage/keys.py` and add a test that a
  batch never spans two collections.
- **Regression shape:** every slice above is measurable with the harness that
  found it. M3-A/D/E have throughput assertions, M3-C has a memory assertion,
  M3-F and M3-G have backlog assertions. The gate rehearsal is the composition,
  not the first measurement.

---

## 6. What M3 explicitly does NOT do

Stated so the gate is not read as a broader claim than it is:

- **No AWS, no IaC, no SQS.** Phase 8, unchanged. M3 pulls the load-gate
  *measurement* forward and nothing else (settled input).
- **No leader election, no partitioned ownership.** S-C settles I-40 with a
  measurement rather than a deferral: the periodic scheduler already dedupes
  across processes (`procrastinate_periodic_defers` UNIQUE, verified live), and
  every "singleton" is a periodic job on the shared worker, so there is no
  leader to elect. Multi-instance already works; M3 does not need it.
- **No streaming for SFTP/FTP sources.** `/vsis3` reaches s3 only. Consistent
  with ROADMAP §2's honest-limits posture, to which S-B now attaches a number:
  feeding one such destination at envelope volume would need ~8 saturated
  10 GbE streams.
- **No change to the OGC facade posture.** Parked post-M3 per ADR 0016.
- **No revalidation of the 2.6M items/day size model.** S-B's distribution is a
  *declared* model, not a NOAA census. Every byte conclusion inherits that
  caveat, and a real census would be worth having before a contractual claim.

---

## 7. Open decisions for the lead

*Revised 2026-09-01 after lead input on pg_cron and `update_collection_extent`
(§4.2–4.4). Decision 1 is narrowed; decision 3 is provisionally settled.*

1. **Who owns the `use_queue` setting, now that pg_cron may own the drain?**
   The setting and its drainer can live in different components: pg_cron in
   cloud, a pipeline job locally (§4.2). That rules out the earlier
   recommendation of having `services/pipeline` assert the setting at startup
   — it would be asserting an invariant it does not own. Remaining options:
   (a) **deployment configuration** — a documented SQL step (compose init for
   local, the RDS/Terraform path for cloud), honest that it is an operational
   choice, at the cost that a deployment which skips it silently runs ~10×
   slower; (b) **an app migration** that writes the row — guaranteed, but
   crosses the ADR 0001 boundary into pgstac's schema; (c) **assert-and-warn
   from whichever component is present** — the pipeline checks the setting and
   the queue depth at startup and logs loudly plus surfaces it on `/health`,
   without writing it. **Recommend (a) + (c)**: configuration sets it,
   observability makes a missed step loud rather than silent. This may deserve
   an ADR, since it is the first deliberate platform dependency on a pgstac
   *setting* rather than its schema.

2. **The concurrency default.** S-C's arithmetic says 12–16 for the
   byte-realistic mix; a conservative default (4?) that deployments raise is
   safer but leaves the box idle. Recommend defaulting low and documenting the
   sizing formula from S-E's envelope.

3. **Whether M3-E (batching) is in M3 at all.** *Lead's read: it is not a
   defect fix, and speed can be optimised later.* Agreed, and after M3-A the
   pgstac path is ~4 ms/item at batch 1 — 8× the target — so the gate does not
   need it. **Provisionally: M3-E is the cut line.** Two things to weigh before
   dropping it outright: the write-amplification count is ≈16 statements per
   item (S-D), which Phase 8's cloud costs will feel more than a local box
   does; and batching `flow_stats` removes a per-association *serialization*
   point that concurrency (M3-D) makes worse, so a small part of M3-E is
   arguably an M3-D concern rather than an optimisation. Suggest keeping the
   `flow_stats` batching inside M3-D and cutting the rest.

4. **Should `update_collection_extent` go on with `use_queue`?** New, from
   §4.3. Recommend yes: nothing maintains collection extents today, the queued
   form is O(partitions) rather than O(items), and the queue is what makes it
   affordable. But it is a behaviour change to what the API advertises for
   every collection, so it is the lead's call rather than an implementation
   detail.

---

## 8. Adversarial review

Recorded per the M2 pattern. Each objection is stated at its strongest, then
answered or conceded.

**"You measured on a dev Mac against MinIO. None of these numbers transfer."**
Partly conceded, and it matters differently per finding. The *absolute* rates
(22 items/s, 125 MB/s transfer, 1,900 claims/s) are laptop numbers and should
not be quoted as platform capacity — the spec uses them only to rank fixes and
to size the gate rehearsal, which runs on the same box. The *shapes* do
transfer, because they are algorithmic rather than environmental: an O(n)
partition scan per write is O(n) on any hardware, `concurrency = 1` is one job
at a time everywhere, and 4 rows retained per job forever is unbounded on any
disk. Finding 1's slope (0.125 ms per existing item) would change; its
existence would not. The gate is explicitly a *local* rehearsal, and Phase 8
still owns the cloud load test — this spec does not claim otherwise.

**"Finding 1's extrapolation to 2.6M items is doing a lot of work, and you only
measured to 5,500."** Conceded, and stated in S-A §6. The extrapolation is the
argument for the fix, not a measurement. But the fix does not depend on the
extrapolation being exact: the fix is justified at 5,200 items, where the
measured cost is already 654 ms/item — 1.5 items/s against a 30 items/s target.
The extrapolation only establishes that batching *alone* cannot rescue it,
which is a claim about the shape (per-call, linear) that was measured directly
at two batch sizes.

**"Turning on `use_queue` trades throughput for stale statistics, and you are
hand-waving the cost."** Fair, and it is why the drainer is inside M3-A rather
than a follow-up. The honest position: pgstac ships this setting for exactly
this reason, the queue self-dedupes so its depth is bounded by partition count
rather than write volume, and the residual risk is search-planner quality
between drains — which is a *performance* degradation on reads, not a
correctness one. What the spec should not do is ship the setting without a
depth metric, which is now §5's explicit requirement.

**"`concurrency = 1` is the default for a reason — maybe Procrastinate expects
one job at a time."** It does not; `WORKER_CONCURRENCY = 1` is a conservative
library default and the parameter exists precisely to raise it. The real force
of the objection is the *second-order* one: this codebase has never run
concurrently, so its concurrency assumptions are untested rather than absent.
That is exactly what S-C's audit was for, and it found one defect in eleven
legs. The spec's response is to ship the fix in the same slice and to keep the
default conservative — not to claim the audit is exhaustive.

**"S-C audited claims. It did not audit shared in-process state, and raising
concurrency exposes that too."** Conceded — this is the strongest unanswered
objection. The audit covered database-level claim semantics; it did not
systematically review module-level mutable state, boto3/GDAL client sharing, or
`asyncio.to_thread` pool sizing under 16 concurrent jobs. **M3-D's review must
cover it**, and this spec should not pretend the audit already did. Added as a
named risk in §5 rather than glossed.

**"You are recommending a pgstac setting as the headline fix. That is a
workaround, not engineering."** It is a configuration change, and it is also
the correct one: the inline stats update is pgstac's default for small
catalogs, and pgstac provides the queue for large ones. Using a library the way
it is designed to be used at our scale is not a workaround. The engineering is
in the parts around it — the drainer, the staleness bound, the metric, and the
ownership decision in §7 — which is why M3-A is a slice rather than a one-line
diff.

**"The gate is a 30-minute local rehearsal. That is not NOAA-scale
readiness."** Correct, and the milestone name overstates what any local gate
can establish. What the gate does establish: the four defects are fixed, the
rate is sustained rather than transient, memory is bounded, and nothing is
lost. What it cannot establish: cloud network behaviour, real NOAA data shapes,
multi-day steady state, or the S-B size model. §6 says so. If the lead wants
the *name* to mean more, the gate needs Phase 8's cloud test — which is a
sequencing decision, not something this spec can fix.

**"You have two drainer mechanisms. That is one more thing to keep working
than a design should have."** Fair, and the honest answer is that the
deployment shapes genuinely differ: pg_cron is not in the pgstac image
(verified) but is a parameter-group option on RDS, so picking pg_cron alone
would leave the local gate — and every self-hosted deployment — without a
drainer, while picking the pipeline job alone would ignore pgstac's own paved
path in the environment that matters most. The mitigation is that the two must
not both run: the pipeline job no-ops when a database-side drainer is
configured, and the `query_queue` depth metric is the single check that either
one is working, regardless of which is in play.

**"Six findings, six slices, and no slice is a re-architecture. Isn't that
suspiciously convenient?"** It is the finding, and it is worth stating plainly
because it inverts the seeded assumption. The reason it came out this way is
that M3-S-A measured before designing: the first run's 2–3.5 items/s looked
like a pipeline-architecture problem and was a database setting. Had the
scoping started from the brief's assumptions (batching mandatory, concurrency
and HA the main question), the batching slice would have landed first, hidden
the O(n²), and the re-architecture would have been designed against a bottleneck
that was never the bottleneck.
