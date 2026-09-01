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
