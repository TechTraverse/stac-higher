# TODO — M5 work queue (Phase 9: Processes)

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.

Scope source: **`docs/superpowers/specs/2026-08-29-phase9-processes-design.md`**
(approved by the lead 2026-08-29; §13 is this queue's origin), ADRs **0013**
(executor isolation) and **0014** (output path) — both accepted. Related:
ROADMAP §5 `PROCESS_*`/§5.6/§6.7/§8 Phase 9 table, ISSUES I-60…I-66 (settled).
Migrations are at **021** (Phase 7 took 020/021) — M5 takes **022+** per the
P7-I amendment already applied to the spec.

**M5 gate (§1 done-when):** an operator creates a process in the UI, deploys a
revision, an item landing in a source collection produces a validated item in
the output collection through an isolated run, the run + logs are visible in
`/processes`, the output item delivers onward through an ordinary delivery
association — and a failed run goes dead → alerts → manual re-run.

The Phase 7 queue is closed — it lives in git history at `05912f8` (gate
evidence: ROADMAP §9 Phase 7). Promoted to `main` 2026-08-30.

Settled — do not relitigate: slice 1 is `inline_python`-only on a
platform-built image behind `DockerExecutor` + docker-socket-proxy (ADR 0013);
outputs finalize through the Phase 7 `FinalizeRequest` seam with
`producer="process_run"` (ADR 0014 — the seam test already proves it routes);
per-process run-rate ceiling is a requirement; M5 precedes M3 (M3 budget
~60 items/s total, process output ≤ 1× ingest).

## Warm-up — small Phase 7 residuals (one worktree, one iteration)

- [x] **M5-W · gate-finding paper cuts.** (1) `docs/push-ingest.md`'s poll
      example shows a flat response; the route nests under `upload` — fix the
      doc (or flatten the route if that was the intent; doc fix is cheaper).
      (2) `.env.example`'s `CATALOG_BFF_SHARED_SECRET` block and the
      enforced-overlay header still overclaim app-side fail-fast — the app
      deliberately does NOT fail fast (can't distinguish dev from enforced;
      compose-side interpolation is the fail-fast) — align the comments with
      `app/src/lib/push/config.ts`. (3) `docs/FEATURES.md`'s baseline intro
      still says "Astro 6" (Astro 7 shipped 2026-08-29).

## M5 implementation (spec §13 verbatim; dependency spine 0 → A/B → C → D/E → F → G)

- [ ] **M5-0 · contracts + migrations** (spec §3). Migrations **022–023**;
      the four new contract fixtures (process-trigger / process-runtime /
      process-env / process-expectation) + **append the three process kinds
      to the existing `alert-kinds.json`** (P7-I amendment — do not recreate
      it); Zod schemas ↔ pipeline lenient readers, both suites consuming.
      Remember the slice-1 asymmetry: `container` runtime = app reject,
      pipeline accept.
- [ ] **M5-A · app CRUD + editor UI** (spec §4, §7). `/api/processes*`
      (+revisions, sources, outputs, audited verbs), `/processes` +
      `/processes/[id]` islands with the **plain-textarea** editor (P9-C:
      no editor dep in slice 1), test-run request rows (ADR 0004 bridge).
      After M5-0.
- [ ] **M5-B · executor** (spec §5, ADR 0013). docker-socket-proxy compose
      service (least-privilege config as verified in P9-A), platform
      executor image, `DockerExecutor` (launch/limits/timeout/logs/reap),
      STS run-scoped creds, log capture + capped storage (I-62: `logs/runs/`
      prefix, 10MB cap, log object before row). Startup self-check refuses a
      raw-socket `DOCKER_HOST`. After M5-0; parallel-safe with M5-A
      (pipeline+infra vs app files).
- [ ] **M5-C · triggers & runs** (spec §6). Dispatcher leg (item_event
      batching + CQL2 filter), cron leg via the scheduler, run ledger +
      RetrySpec + stall sweep (the repo's ledger+sweep shape), **rate
      ceiling + deferral** (`process_rate_limited` backstop), Re-run verb.
      After M5-A and M5-B.
- [ ] **M5-D · output path** (spec §2, ADR 0014). `finalize(process_run)`
      integration (the resolver/recorder pair the Phase 7 seam left as
      `NotImplementedError`), output_items recording, outbox composition
      verified (delivery fires from process output), **cycle refusal**
      (our-edges-only DFS, shared edge module with the graph API). Heed
      ISSUES **I-80**: the finalize tier logic is snapshot-first/
      provable-create — the process recorder must define its own rejection
      semantics against that, not copy push's. After M5-C.
- [ ] **M5-E · monitoring** (spec §8). Three alert kinds
      (`process_failed`/`process_stalled`/`process_rate_limited`,
      per-source `run_within_seconds` expectations — I-63), process
      flow_stats, the **`flow_stats_daily`** table + daily upsert job +
      ~400-day prune (P9-E), `/api/monitoring/graph` (nodes/edges, member+
      scoped), `/metrics` counters per the M2-H central pattern. After M5-C;
      parallel-safe with M5-D if footprints stay disjoint (check the shared
      edge module — coordinate if both touch it).
- [ ] **M5-F · UI completion** (spec §7, §9). `/graph` pipeline view,
      collection lineage panel (30-day strip off `flow_stats_daily`),
      overview rollup, run log viewer, dashboard sparklines; e2e coverage.
      After M5-D and M5-E.
- [ ] **M5-G · M5 gate rehearsal** (lead only: Docker + dev server + e2e,
      M2-I/P7-Z style). §1 done-when live on the auth-enforced stack:
      kill-mid-run recovery, stall sweep, rate-ceiling breach + alert,
      cycle refusal, every `pragma: no cover` SQL path. Record evidence in
      ROADMAP §9 M5; promotion PR is the human's. After everything.
- [ ] *(stretch, post-gate)* **OGC API — Processes facade** (spec §11,
      I-66) — only if the lead green-lights after M5-G.

## Discovered follow-ups

(append here during iterations)
