# TODO — M3 scoping queue (NOAA-scale readiness)

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.

Scope source: **ROADMAP §9 M3** + the seeded scoping brief
**`docs/superpowers/specs/2026-08-31-m3-scoping-notes.md`** (settled inputs,
issue inventory, and per-item briefs live there — read it first). This is a
**scoping queue**, the P9 pattern: investigation/decision items culminating in
a design spec and a **lead stop point** (M3-S-G). No M3 implementation slice
starts before the spec is approved.

The M5 (Phase 9) queue is closed — it lives in git history at `59e8087` (gate
evidence: ROADMAP §9 Phase 9; promoted to `main` 2026-08-31).

**M3 in one line:** sustain ~30 items/s ingest-origin (+ ≤1× process output ⇒
~60 items/s total catalog write rate) on the local stack, measured, with a
written load report — the Phase 8 load-gate measurement pulled forward.

Settled — do not relitigate (details in the scoping notes): the 60 items/s
total budget; local-first (no AWS, no SQS work — Phase 8); finalize is the
choke point for process/push volume; `flow_stats` per-transition write
batching is mandatory at this rate; the OGC facade stays parked post-M3
(posture: ADR 0016).

## Warm-up — M5 residuals (small, self-contained; one worktree each)

- [x] **M3-W-1 · orphaned run-container reaper.** A worker killed between
      launch and reap leaves a run container behind; nothing prunes them
      (deliberate M5-C deferral — `process/sweep.py` docstring says why the
      DB sweep must not do it). Add a dedicated reaper leg on the executor
      side: list containers by the `stac-higher.run-id` label through the
      socket proxy, reap those whose run row is terminal or whose age exceeds
      the run timeout + slack. Pipeline-only; pytest with a fake executor.
- [x] **M3-W-2 · env editor + secret-ref picker + docs.** The `env` contract
      (fixture `process-env.json`: `{name, value}` | `{name, secret_ref:
      {connection_id, key}}`) round-trips through storage and deploy, but the
      UI deploys `env: []` and `docs/processes.md` never mentions env or
      secrets. Build the minimal editor on `/processes/[id]` (literal rows +
      a connection/key picker for secret refs, group-scoped connections
      only), VERIFY the deploy route rejects a `secret_ref` whose connection
      is outside the process's owning group (the M5 follow-up flagged this as
      route-check-only — add it if absent), and document the deliberate
      connection-scoped limit in `docs/processes.md` (an arbitrary-secret
      store is a future decision, not a reinterpretation of this ref shape).

## M3 scoping (spec §-briefs in the scoping notes; dependency spine A → B/C/D/E/F → G)

- [x] **M3-S-A · baseline throughput measurement.** The load-bearing input:
      synthetic feed harness (parametrized S3 drop generator + direct-outbox
      seeder) + per-stage measurement (ingest, outbox drain, delivery,
      finalize, DB hot spots) off `/metrics` and table counts. Record today's
      ceiling and first bottleneck by APPENDING a measurements section to the
      scoping notes. The harness must be loop-runnable and reusable as the M3
      gate's rehearsal driver.
- [x] **M3-S-B · §2 byte-volume arithmetic redo.** ROADMAP §2 at 2.6M
      items/day + 1× process output: size-distribution assumptions, copy vs
      reference split, storage growth vs retention, GC bulk-path check.
      Append to the scoping notes; feeds S-E's decision.
- [x] **M3-S-C · concurrency & HA plan (I-40).** Scale-workers-only vs
      leader election vs partitioned ownership, decided from S-A's numbers;
      audit `ingest_files` transitions for multi-worker atomicity the way
      `item_events` claims already are. Bias: if the singleton dispatcher
      holds 60 items/s, document the singleton and defer election to Phase 8.
- [x] **M3-S-D · hot-path write batching design.** `flow_stats`
      per-transition bumps (mandatory), `delivery_log`/`ingest_files` churn
      (must not break the ADR 0012 UNIQUE-upsert model), outbox claim batch
      sizing. Design informed by S-A's contention measurements.
- [x] **M3-S-E · streaming decision (I-19/I-26).** Streaming s3 get→
      multipart-put vs documented copy-mode size limits + reference-mode
      posture vs defer to Phase 8 — decided against S-B's size distribution,
      with the per-worker memory envelope written down.
- [ ] **M3-S-F · queue-backend headroom arithmetic.** Procrastinate jobs/s
      at 60 items/s with S-A's observed batch sizes; where the comfort
      ceiling sits on the shared Postgres. Paper only; feeds Phase 8's
      Procrastinate→SQS boundary.
- [ ] **M3-S-G · design spec + lead stop point.** Write the M3 design spec
      (M2 pattern: gate, slices, dependency spine, testing & risk; carries
      S-B's arithmetic), run adversarial review, then **STOP for lead
      approval**. Only after approval does the implementation queue replace
      this one.

## Parked (do not start without the lead)

- **OGC API — Processes facade** (Phase 9 spec §11, I-66) — posture and
  Parts 2–5 outlook recorded in ADR 0016 (2026-08-31). Post-M3, lead-gated;
  re-survey the drafts when green-lit.

## Discovered follow-ups

The M5 queue's follow-ups are archived with it at `59e8087`; the two still
actionable became M3-W-1/M3-W-2 above, and the durable design notes were
already captured in `docs/processes.md`, ISSUES (I-80, I-81), migration 024's
comment block, and the module docstrings they describe.

- **Re-run vs. a leftover container (M3-W-1 discovery).** A re-run reuses the
  run id, so `launch` asks for the container name `stac-run-{run_id}` — which
  collides (409) while that run's orphaned container still exists. The failure
  is self-healing (a 409 surfaces as `ExecutorUnavailable` → infrastructure
  requeue, no attempt spent → the next tick after the reaper's 15-minute pass
  succeeds), so it is a latency wart, not a correctness one. Fix candidates if
  it ever bites: reap-by-run-id before launch, or an attempt suffix on the
  container name. Not worth a slice on its own.

- **`use_queue` is an M3 implementation slice, not a scoping change
  (M3-S-A).** The measurement set `pgstac_settings.use_queue = true`,
  confirmed a 10x end-to-end gain, then REVERTED it and drained
  `pgstac.query_queue`. Turning it on for real needs a periodic
  `CALL pgstac.run_queued_queries()` (it is a PROCEDURE) and an owner for the
  settings row — the app owns migrations, the `pgstac` schema is pypgstac's.
- **The harness has no process leg.** The M3 gate wants a synthetic process at
  ~50% source share; `pipeline.loadgen` drives ingest, outbox and delivery
  only. Gate-rehearsal work, sized when M3-S-G writes the gate.

(append here during iterations)
