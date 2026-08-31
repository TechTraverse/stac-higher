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

- [x] **M5-0 · contracts + migrations** (spec §3). Migrations **022–023**;
      the four new contract fixtures (process-trigger / process-runtime /
      process-env / process-expectation) + **append the three process kinds
      to the existing `alert-kinds.json`** (P7-I amendment — do not recreate
      it); Zod schemas ↔ pipeline lenient readers, both suites consuming.
      Remember the slice-1 asymmetry: `container` runtime = app reject,
      pipeline accept.
- [x] **M5-A · app CRUD + editor UI** (spec §4, §7). `/api/processes*`
      (+revisions, sources, outputs, audited verbs), `/processes` +
      `/processes/[id]` islands with the **plain-textarea** editor (P9-C:
      no editor dep in slice 1), test-run request rows (ADR 0004 bridge).
      After M5-0.
- [x] **M5-B · executor** (spec §5, ADR 0013). docker-socket-proxy compose
      service (least-privilege config as verified in P9-A), platform
      executor image, `DockerExecutor` (launch/limits/timeout/logs/reap),
      STS run-scoped creds, log capture + capped storage (I-62: `logs/runs/`
      prefix, 10MB cap, log object before row). Startup self-check refuses a
      raw-socket `DOCKER_HOST`. After M5-0; parallel-safe with M5-A
      (pipeline+infra vs app files).
- [x] **M5-C · triggers & runs** (spec §6). Dispatcher leg (item_event
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

- **`env` secret-refs point at connection credentials.** M5-0 had to pin what
  a `secret_ref` actually references — the spec says "refs into the §5.2
  envelope" without naming a target. The contract now says
  `{connection_id, key}`: a named key inside a connection's write-only
  credentials blob, which reuses the existing envelope with no new storage and
  matches the §10 "secret-ref picker" UI. M5-B's launch-time resolver has to
  decrypt through the connections credential path and must NOT widen it (the
  worker decrypts, but only into the run container's environment — ADR 0013).
  If a deployment ever needs process secrets that are not a connection's,
  that is a new store and a new ref kind, not a reinterpretation of this one.
- **`alerts` has no process anchor yet.** The three process kinds are declared
  in M5-0, but `alerts` anchors on connection/association/channel/collection
  (migrations 014/015/021). `process_stalled` and `process_rate_limited` are
  per-SOURCE (I-63) and `process_failed` is per-process — none of which is an
  existing anchor. **M5-E must add the anchor column in lockstep with the
  CHECK, the open-dedup index and BOTH pipeline `ON CONFLICT` targets**, the
  way migration 021 did; the migration-021 comment block is the checklist.
- **The three process kinds are declared but UNOWNED** (fixture
  `declared_kinds`). M5-E must claim each for the writer that actually raises
  it. This is not bookkeeping: `MONITOR_KINDS` membership grants the flow
  monitor auto-resolve authority, so a kind raised by the run path or the rate
  limiter while listed as monitor-owned would be silently resolved within a
  minute. `process_rate_limited` is an enqueue-time event — the same shape as
  `webhook_failed`, which is deliberately notify-owned for that reason.
- **`secret_ref`'s target namespace can't hold an arbitrary secret.** M5-0
  pinned `{connection_id, key}`, but `connections/schemas.ts` credentials are
  CLOSED per-protocol `.strict()` shapes (s3: access_key_id/secret_access_key/
  session_token; ssh: username/password/private_key/passphrase; ftp:
  username/password). So `key` can only ever name one of ~8 endpoint-credential
  fields, and the first process needing a third-party API token would force an
  operator to create a sham `ftp` connection with the token in `password` —
  which then drags in that entity's machinery (health sweep flips it to
  `error`, raises `connection_error`, TOFU/egress policy on an endpoint that
  does not exist). **Decide in M5-B before building the resolver**: either a
  per-group `secrets` table (name → envelope) reusing `connections/crypto.ts`'s
  already-generic `sealEnvelope`/`openEnvelope` with `secret_ref = {secret_id}`,
  or accept the connection-scoped limit and say so in the docs. Related and
  unassigned: the ref's safety constraint — the referenced connection must
  belong to the process's owning group — is not expressible in jsonb (no FK)
  and the pipeline resolves at launch with no requester identity, so it can
  only land as a per-route check.
- **`alerts-migration.test.ts` sliced to the end of `MIGRATIONS`**, so its
  "no FK" pin silently widened over every migration added after 021 (M5-0's
  022 tripped it). Fixed by slicing to the next entry. Worth remembering for
  any future text-pinned migration test.

- **M5-A left the run surfaces to M5-C, deliberately.** There is no
  `/api/processes/[id]/runs` route and the detail page has no runs panel: the
  ledger has no writer yet, so a list would only ever show "none" and read as
  "nothing ran" rather than "nothing runs yet". M5-C adds the route, the panel
  and the Re-run verb together.
- **The source/output routes are the M5-D cycle-check hook points.** Both
  `POST .../sources` and `POST .../outputs` (and re-enabling a source) need the
  our-edges-only DFS before insert, 409ing with the path. The route comments
  mark the spots; until then the §7 ceiling is the only backstop.
- **The editor collects no env vars yet.** The contract, the storage column and
  the deploy route all carry `env` (and it round-trips), but the UI deploys
  `env: []` — the secret-ref picker needs the `secret_ref` target question
  settled first (see the entry above).

- **The revision's code travels in the ENVIRONMENT, not a bind mount** —
  a deliberate departure from ADR 0013's sketch, and worth knowing before
  M5-C wires the ledger. Under docker-out-of-docker a bind path is resolved
  by the DAEMON on the host, so mounting a path from inside the pipeline
  container would be wrong or dangerous. Base64 in
  `STAC_HIGHER_PROCESS_CODE_B64`, scrubbed by the entrypoint before user code
  runs. The upside is that the socket proxy never needs to permit mounts, so
  ADR 0013's documented residual risk (arbitrary `HostConfig` on create) has
  one fewer way to bite. **Consequence for a future `container` runtime**:
  a user image will not have our entrypoint, so it needs its own code-delivery
  answer — do not assume this one generalises.
- **`process_runs.log_ref` has no writer until M5-C.** `store_run_log` returns
  the key and `execute_run` returns it in the outcome, but nothing persists it
  yet. M5-C must write the object BEFORE the row (I-62) — the retention leg
  deletes in the opposite order, so that ordering is what keeps a row from
  ever pointing at bytes that do not exist.
- **Nothing prunes orphaned run containers.** `reap` runs in a `finally` and
  a failed start self-reaps, but a worker killed between launch and reap
  leaves a container behind. The `stac-higher.run-id` label exists so a sweep
  can find them; M5-C's stall sweep is the natural home.

- **`process_rate_limited` is logged, not raised.** M5-C defers and coalesces
  correctly and logs the numbers, but the ALERT is M5-E's (the kind sits in the
  fixture's `declared_kinds` with no writer). Until then a ceiling breach is
  visible in the run list's "rate limited" badge and the worker log, not the
  bell.
- **`output_items` is still always empty.** The column, the API field and the
  UI counter all exist and round-trip; M5-D's `finalize(process_run)` is what
  will populate it.
- **Cron uses our own matcher, deliberately.** Sources are user data created
  and edited at runtime, while the queue backend's periodic registry is fixed
  at worker startup — so a per-source periodic job is not expressible. The
  minute tick asks the database "who is due". The last-run guard is what makes
  it idempotent; if the tick ever moves off a 1-minute cadence, that guard's
  window has to move with it.
- **A re-run does not re-resolve the revision.** It re-executes the pinned one,
  so an operator who deploys a fix must deploy AND then trigger, not re-run an
  old dead row expecting the new code. Worth a line in the UI copy if operators
  trip on it.

(append here during iterations)
