# Phase 9 — Processes (M5) design

**Status: DRAFT — pending lead review** (the P9-F stop point). Accepting this
spec also flips ADRs 0013/0014 from proposed to accepted, with the decisions
recorded here.

Sources: ROADMAP §5 `PROCESS_*` + §5.6 + §6.7 + §8 Phase 9 table + §9/§10;
ADR 0013 (+ the P9-A investigation appendix), ADR 0014 (+ the P9-B seam
sketch); the P9 scoping notes
(`2026-08-29-phase9-scoping-notes.md`); ISSUES I-60…I-66. Settled inputs,
not relitigated: **M5 precedes M3**; **Phase 7 precedes Phase 9** and ships
the producer-parameterized finalize; **slice 1 is `inline_python` on a
platform-built image**; **a per-process run-rate ceiling + alert is a
requirement**; local-first (no cloud executor before Phase 8).

## 1. Gate (done-when, firmed up from ROADMAP §9)

An operator can, in the UI: create a group-owned process, edit its
`inline_python` code, deploy a revision, attach a source collection
(`item_event` trigger) and an output collection — then drop a file into the
source collection's ingest flow and watch, without leaving the UI:

1. the landing item trigger a run (`process_runs` row, revision pinned);
2. the run execute in an **isolated container** (not the worker process),
   its logs captured and viewable on the run;
3. a validated output item appear in the output collection with
   `/api/assets/...` hrefs, via the Phase 7 finalize;
4. the output item **deliver onward** through an ordinary delivery
   association;
5. a failing revision go `failed → dead` on the retry budget, raise a
   `process_failed` alert (bell + channels), and recover via the audited
   **Re-run** verb;
6. a stopped flow breach the source's `run_within_seconds` expectation and
   raise `process_stalled`, auto-resolving on recovery.

Everything above on the auth-enforced stack, rehearsed M2-I-style as the
final slice.

## 2. What M5 inherits

The platform already has every pattern this phase needs: the outbox +
dispatcher (item_event triggers ride it like delivery), the scheduler (cron
triggers), the ledger + sweep recovery shape (`process_runs` mirrors
`delivery_log`), queue `RetrySpec`, the ADR 0004 request-table bridge (test
runs), group ownership + guard audit, per-direction expectations + the
`flow_monitor`, `MONITOR_KINDS` ownership boundaries, contract fixtures,
`/metrics` instrumentation-at-registration, and — from Phase 7 — the
producer-parameterized `finalize()` (ADR 0014 seam; P9-B's check criterion
binds Phase 7's design). M5 adds exactly two genuinely new mechanisms: the
**executor boundary** and **run-scoped storage credentials**.

## 3. Data model (ROADMAP §5 `PROCESS_*`, confirmed)

Migrations (app-owned DDL, ADR 0001; numbering continues from 019):

- **020**: `processes`, `process_revisions`, `process_sources`,
  `process_outputs`, `process_runs` exactly as §5's ER shapes, plus:
  - `processes.max_runs_per_hour int NOT NULL DEFAULT 60` — the run-rate
    ceiling (§7).
  - `process_sources.expectation jsonb` — `{run_within_seconds}` (decision
    §8: per-source).
  - `process_runs.rate_deferred_until timestamptz` (§7) and
    `process_runs.error text`.
  - `process_checks` — the ADR 0004 request table for UI test runs
    (`connection_checks` shape: requested → claimed by the pipeline →
    result columns).
- **Hygiene stance confirmed against ADR 0012's criteria**: `process_runs`
  is NOT partitioned — runs are verb targets (`re-run`), the
  `delivery_log` argument verbatim. The hourly `history_retention` sweep
  gains a leg: terminal runs older than `PROCESS_RUN_RETENTION_DAYS`
  (default 90) are pruned, **log object deleted before the row** (§9).

Cross-runtime contracts land WITH the DDL slice: the five P9-D fixtures
(`process-trigger`, `process-runtime`, `process-env`,
`process-expectation`, `alert-kinds`) in the established
minimal/defaults/cases format, including the slice-1 asymmetry (`container`
runtime: app **reject**, pipeline **accept**) and the full alert-kind enum
(§8). `alert-kinds.json` retires the deferred M2 hygiene follow-up.

## 4. Execution — ADR 0013 accepted as Option B

**Decision (accepts ADR 0013):** container-per-run behind an `Executor`
interface; slice 1 backend is **`DockerExecutor`** per the P9-A
investigation — Docker Engine API through a least-privilege
`docker-socket-proxy` (compose service; `CONTAINERS=1 POST=1`, everything
else off; the proxy reachable ONLY from the pipeline, never from run
networks), per-run `HostConfig` limits (`Memory`, `NanoCpus`) from the
§5.6 runtime shape, wall-clock timeout enforced by the executor, and runs
attached to a dedicated egress-restricted network (the `resolve_pinned`
analog at the network boundary). Cloud backends stay paper (P9-A: ECS/
Fargate RunTask primary, K8s-Job-on-EKS fallback — EKS-on-Fargate absent in
GovCloud) and are built in Phase 8.

The interface (pipeline-side seam, queue-interface pattern):

```
Executor.launch(spec: RunSpec) -> RunHandle     # image, cmd, env, limits, network
Executor.wait(handle, timeout) -> ExitStatus    # enforced timeout kills the run
Executor.logs(handle) -> byte stream            # capped, captured to log_ref
Executor.reap(handle)                           # always; AutoRemove not trusted
```

- **Image**: slice 1 runs a **platform-built executor image**
  (`services/process-runtime/Dockerfile`: pinned python + the blessed
  library set; built/pushed like the pipeline image). `runtime.kind =
  "container"` is carried in the contract and **refused at the app's write
  gate** — user-image supply-chain review stays out of the first
  accreditation surface.
- **Code injection**: the revision's `code` is mounted read-only into the
  run container (no image rebuild per revision).
- **Env**: `process_revisions.env` resolves secret-refs (§5.2 envelope)
  **at launch, pipeline-side, into the run's environment only** — never
  into the worker's own process. Plus the run-scoped storage credentials
  (§5) and nothing else: no DB URL, no master key, no platform S3 keys.
- **Test runs**: the UI writes a `process_checks` row (ADR 0004); the
  pipeline drains it into a normal (flagged) run. Local Docker launch is
  ~0.1–0.7s warm (P9-A) so interactive test runs are fine in dev; the
  Fargate cold start (~30–45s) is a Phase 8 UX problem — the spec's stance:
  set expectations in the UI (a "queued (cold start)" state), do NOT build
  a warm-pool bypass that weakens the boundary.

## 5. Output path — ADR 0014 accepted; credential mechanics settled

**Decision (accepts ADR 0014):** user code writes only to
`staging/runs/{run_id}/…`; the platform finalizes through Phase 7's
`finalize()` with `producer: "process_run"`, `output_collections` from
`process_outputs`, provenance = the run id. `FinalizeResult.upserted`
records `process_runs.output_items` authoritatively; rejects fail the run
with per-item reasons in the ledger. Outbox events fire on upsert —
delivery composes for free; finalize gating (§6.4) keeps dispatch off
staged items.

**Run-scoped credentials (the ADR's open question, settled):** the pipeline
mints **STS session credentials with an inline session policy** restricting
S3 access to the run's staging prefix (`s3:PutObject`/`GetObject`/
`ListBucket` on `staging/runs/{run_id}/*` only). Works against both MinIO
(AssumeRole STS, supported) and AWS; expiry = run timeout + grace. If a
deployment's store lacks STS, the executor falls back to failing process
creation loudly — no silent widening to platform keys. Abandoned staging
prefixes age out via the existing TTL sweep (ADR 0005).

## 6. Triggers & runs

- **`item_event`**: a consumer leg on the existing outbox dispatcher —
  matching extends from delivery associations to `process_sources`
  (enabled, collection match, optional CQL2 `item_filter` on the same
  evaluation path as delivery). Matches **batch** into one queued run per
  process-source per dispatch tick (`input_items` = the batch; one run = N
  trigger items, §6.7).
- **`cron`**: the ingest scheduler pattern — due sources enqueue a run with
  empty `input_items`.
- **Ledger**: `queued → running → succeeded | failed → dead` with queue
  `RetrySpec` (runtime.retry), attempts on the row, and a stall sweep
  (`PROCESS_RUN_STALL_SECONDS`, M2-0 pattern) for rows stranded
  `running` by a crashed worker/executor. Dead runs expose the audited
  operator+ **Re-run** verb (the `redeliver` analog, via the BFF-side
  route + audit action `rerun`).

## 7. The run-rate ceiling (requirement, backstopping I-64)

`processes.max_runs_per_hour` (default 60, operator-editable) is enforced
at **enqueue** time: a trigger that would exceed the rolling window defers
the run (`rate_deferred_until`; deferred runs coalesce — a deferred
item_event run absorbs new matches rather than queueing more) and the
monitor raises **`process_rate_limited`** (auto-resolving when the rate
drops). This caps the blast radius of any loop the cycle check cannot see:
a runaway loop degrades to ceiling-paced, alerting, visible — never silent
amplification.

## 8. Monitoring, alerting & telemetry decisions

- **I-63 decided: `run_within_seconds` lives per `process_sources` row** —
  it mirrors the per-association expectation model exactly (different cron
  cadences / arrival profiles per source), reuses the M2-A editable-
  expectation UI shape, and slots the source id into the ADR 0010 dedup
  key's association position. The `/processes` dashboard aggregates
  per-source states into the process-level "is my process running" view.
- **Alert kinds** (extends `MONITOR_KINDS`; the monitor stays the single
  writer for its kinds): `process_stalled` (expectation breach — no
  successful run within the window of a due trigger), `process_failed`
  (dead run — the `job_failure` analog, raised by the run path like
  `delivery_dead`), `process_rate_limited` (§7). All three join
  `alert-kinds.json`; the app's kind-branching (`EXPECTATION_BREACH_KIND`)
  gains `process_stalled`.
- **I-64 decided: cycle detection refuses at write time over OUR edges
  only** — creating a `process_sources`/`process_outputs` row (or
  re-enabling one) runs a DFS over the {ingest, deliver, process_source,
  process_output} edge model (shared with `/api/monitoring/graph`); a
  closable cycle through collections/processes is a 409 with the path in
  the error. Paths through connections/external systems are **not**
  statically decidable and are deliberately out of scope — the §7 ceiling
  is the backstop. Delivery→re-ingest self-loops via the same connection
  remain possible and documented.
- **Metrics** (M2-H pattern): per-process-kind run counters ride the
  central job instrumentation for free; add `pipeline_process_runs_total
  {outcome}`, run-duration histogram, `pipeline_process_output_items_total`,
  and rate-limit deferral counter at the run choke points.
- **`flow_stats` for processes**: per-source rollup on `process_sources`
  (runs, output items, last_run_at, last_error_at, last_duration) written
  at run-terminal transitions — the M2-A shape.

## 9. Run logs (I-62 decided)

Captured by the executor (§4) to object storage at
`logs/runs/{process_id}/{run_id}.log` — a `logs/` sibling of
`assets/`/`staging/` in the §5.3 layout — referenced from
`process_runs.log_ref`, never interleaved into pipeline logs as trusted
content (ADR 0013 invariant). **Size cap** enforced during capture
(`PROCESS_LOG_MAX_BYTES`, default 10MB; truncated with a marker line).
**Retention** rides the `history_retention` leg (§3): when a terminal run
row ages out, its log object is deleted FIRST, then the row — no orphaned
bytes, no `asset_gc` involvement (logs are platform bytes, not catalog
assets). The UI streams the log via an app route that presigns/302s like
the asset route (member+ of the owning group).

## 10. UI surface (ROADMAP §8 Phase 9 table, confirmed)

- **`/processes`** — dashboard: per-process health (aggregated source
  states), last run, success rate, run sparkline (from `flow_stats_daily`).
- **`/processes/[id]`** — sources+triggers+expectation, outputs, runtime
  knobs, env vars (secret-ref picker; plaintext never echoed), the code
  editor — **slice 1 is a plain textarea** (P9-C decided; CodeMirror 6 is
  the recorded upgrade path, Monaco never), deploy (new revision, audited),
  test run (ADR 0004 bridge), recent runs with logs + Re-run.
- **`/graph`** — the pipeline graph off `/api/monitoring/graph` (P9-E
  shapes: typed nodes/edges, per-edge flow_stats + open_alert derived from
  the alerts list exactly like the M2-D hint). Rendered as an island;
  health-colored edges.
- **Collection lineage panel** (Data-flow tab) + **overview rollup** — fed
  by the graph endpoint and **`flow_stats_daily`** (P9-E decided: a table,
  `(subject_kind, subject_id, day)` PK, daily pipeline upsert job, ~400-day
  prune; today's bucket derived live). Migration 021.
- RBAC: processes are group-owned; member views, operator+
  creates/deploys/re-runs; every create/update/deploy/rerun/delete audited
  through the existing guard (`process`, `process_revision` resource
  types). Soft-delete per ADR 0009.

## 11. OGC API — Processes facade (I-66 decided)

**Worth claiming, as a stretch slice — not slices 0–3.** The mapping is
clean (read-only + async execute): `/processes` → list/describe (inputs
schema derived from the env/trigger shape), `POST
/processes/{id}/execution` → an audited run (Part 1 async core;
`Prefer: respond-async` only), `/jobs/{jobId}` → run status, `…/results` →
`output_items` links. Conformance candidates: Core, JSON, OGC Process
Description, Job list. Bearer auth at the proxy fits the standard's
assumptions. It ships only after the native surface is rehearsed —
interop demo value (NOAA) without contorting the internal model, which
stays richer (revisions, group ownership, event triggers) and canonical.

## 12. Throughput arithmetic (§10 obligation — the number M3 consumes)

M3 targets ~30 items/s (~2.6M/day) of **ingest-origin** items. Processes
multiply: every output item is a full catalog item through finalize →
outbox → delivery matching. Planning envelope for M3, set here:
**process-generated volume is budgeted at ≤ 1× ingest volume — M3 must
size and rehearse against ~60 items/s total catalog write rate**, with a
synthetic process (≥1 output per input on a 50% source share, or
equivalent) in the load rehearsal. Mechanically: finalize is the correct
choke point (per-item validation ≈ ITEMIZE cost; pypgstac batch upserts
amortize), the §7 ceiling bounds any single process (60 runs/h × batch
size), and the M2-A follow-up about batching per-transition `flow_stats`
writes becomes mandatory at that rate. The §2 byte-volume arithmetic redo
stays an M3-scoping task; this spec contributes the 2× item-rate input.

## 13. Slices (worked through TODO.md, M2 style)

| Slice | Contents | Depends on |
|---|---|---|
| **M5-0** | Migrations 020–021, the five contract fixtures + `alert-kinds.json`, Zod schemas ↔ pipeline readers | — |
| **M5-A** | App CRUD: `/api/processes*` (+revisions, sources, outputs, audited verbs), `/processes` + `/processes/[id]` islands (textarea editor), test-run request rows | M5-0 |
| **M5-B** | Executor: socket-proxy compose service, platform executor image, `DockerExecutor` (launch/limits/timeout/logs/reap), STS run-scoped creds, log capture + capped storage | M5-0 |
| **M5-C** | Triggers & runs: dispatcher leg (item_event batching + CQL2 filter), cron leg, run ledger + RetrySpec + stall sweep, rate ceiling + deferral, Re-run verb | M5-A/B |
| **M5-D** | Output path: finalize(`process_run`) integration, output_items recording, outbox composition verified, cycle refusal (shared edge module) | M5-C, Phase 7 |
| **M5-E** | Monitoring: three alert kinds, per-source expectations, process flow_stats, `flow_stats_daily` job, `/api/monitoring/graph`, `/metrics` counters | M5-C |
| **M5-F** | UI completion: `/graph`, lineage panel, overview rollup, run log viewer, dashboard sparklines; e2e | M5-D/E |
| **M5-G** | **M5 gate rehearsal** (§1 done-when live on the auth-enforced stack, M2-I style: kill-mid-run, stall sweep, rate-ceiling breach, cycle refusal, `pragma: no cover` SQL) | all |
| *stretch* | OGC API — Processes facade (§11) | M5-G |

## 14. Testing & risk

- Unit/pytest per slice with the fixtures as the cross-runtime spine; the
  executor gets a fake (in-process) implementation for handler tests, the
  real `DockerExecutor` exercised in M5-G (and a DB-gated pytest where the
  socket proxy is present).
- **Risks carried consciously**: the socket proxy still allows arbitrary
  `HostConfig` on create (bind mounts, privileged) — mitigated by the
  executor being the only client, the proxy unreachable from run networks,
  and a startup self-check that refuses a raw-socket `DOCKER_HOST`;
  slice-1 blessed-image scope creep (operators will ask for packages —
  answered by image versioning, not per-process installs); expectation
  noise on cron sources (same recency-cutoff revisit as M2-B's
  delivery-SLO note); `process_runs` growth at M3 rates (bounded by the
  retention leg; revisit ADR 0012 criteria if re-run windows must extend).
- Singleton stance unchanged (I-40): the dispatcher/scheduler/monitor stay
  single-instance through M5; M3 owns the horizontal story.
