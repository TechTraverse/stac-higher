# K-4 · Submit-then-Reconcile Executor with Run Cancel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub issue #12 (K-4) · epic #2 · **migration 031 reserved for this slice** (`031_process_runs_cancelled`; 029 = K-3, 030 = C-1, 032/033 = Z-2).

**Goal:** A process run no longer lives inside a worker slot: the claim path mints, submits and returns; a run watcher (Docker Engine events) and the one-minute run tick reconcile every submitted run through `Executor.status()` and finalize it exactly once, even across a worker restart; operators can cancel a queued, retrying or running run from the UI (`cancelled`, audited `cancel`), closing I-81 and ADR 0016's `dismiss` gap.

**Architecture:** The `Executor` ABC becomes `submit / status / cancel / logs / reap / list_launched` plus an optional `watch()`; `wait` is gone. **Events are wake-ups; `status()` is the truth.** Every ledger move out of `running` is a compare-and-set on the row — fenced by the claim stamp (`started_at`) before submission and by `executor_handle` after it — and a submitted run's verdict is written only inside `collecting(run_id, handle)`, a transaction holding the row `FOR UPDATE SKIP LOCKED` across logs → log object → verdict, with the container reaped only after commit. One `Reconciler` (in `process/reconcile.py`) is called by three wake paths: the watcher (Docker `/events`, filtered on the `stac-higher.run-id` label), a `LISTEN process_run_cancel` loop (the app's cancel fires `pg_notify` in the same statement), and the run tick (the backstop). Docker enforces the run's timeout on read (a `stac-higher.timeout-seconds` label checked by `status()`), the analogue of a Kubernetes Job's `activeDeadlineSeconds`, so the reconciler stays backend-agnostic for K-5. Credentials are minted for `max_queue_wait + timeout + grace` and a submitted run still `pending_capacity` with less than `timeout + grace` left is cancelled and requeued with its attempt restored.

**Tech Stack:** Python 3.12 (psycopg 3 async, asyncio streams for the Engine events API, urllib for the rest of the Engine API v1.43), pytest; Astro 7 + React 19, vitest; migration in `app/src/lib/db/migrate.ts`; a new contract fixture consumed by both suites. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §5 (all), §6.1 (what K-5 needs from the ABC — read, not built), §8 (Docker parity), §9 (Cancel button, `cancelled` badge, test-run phases), §13 decisions 6–8; ADR 0019, ADR 0013 (isolation invariants, "AutoRemove not trusted"), ADR 0016 §3 (`dismiss`); ISSUES I-81, I-122, I-124, I-149, I-164, I-165. Predecessor plan: `docs/superpowers/plans/2026-09-15-k3-docker-profile-capacity.md`.

## Global Constraints

- **Worktree:** `.claude/worktrees/k4-submit-reconcile`, branch `feat/k4-submit-reconcile`, branched from K-3 (#11). Task 0 rebases it onto `origin/main` once K-3 has merged. `npm install` at the worktree root.
- **Gates:** app tasks `npm run verify` from the worktree root; pipeline tasks `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/` (plus `DATABASE_URL=… uv run pytest tests/test_integration_process_reconcile.py` for the DB-gated file when a Postgres with migration 031 is available — lead or a throwaway PG, never the shared stack's while the lead owns it). Teammates never run e2e, the dev server, Docker or image builds, and never push.
- **Migration 031 (`031_process_runs_cancelled`)**, appended between `030_container_images` and `032_cube_sinks` in `MIGRATIONS` (applied by name; a database at 033 applies just it): replace the `status` CHECK with `process_runs_status_check CHECK (status IN ('queued','running','succeeded','failed','dead','cancelled'))` and `ADD COLUMN IF NOT EXISTS credentials_expire_at timestamptz`. Nothing else. ADR 0001: the pipeline runs no DDL.
- **Status vocabulary, both sides, pinned by `tests/contract-fixtures/process-run-status.json`:** statuses `queued, running, succeeded, failed, dead, cancelled`; terminal `succeeded, dead, cancelled`; cancellable `queued, failed, running`; cancel channel `process_run_cancel`. The phase fixture (`process-run-phase.json`) is unchanged: `pending_capacity, starting, running`.
- **Executor phases** (pipeline-internal, `RunState.phase`): `pending_capacity, starting, running, exited, lost`. `exited` and `lost` are never written to `process_runs.phase`.
- **Docker labels:** existing `stac-higher.run-id`, `stac-higher.process-id`, `stac-higher.run-kind`, `stac-higher.image-id`; new `stac-higher.timeout-seconds` (decimal string of `RunSpec.timeout_seconds`, every container).
- **Docker cancel:** `POST /containers/{id}/stop?t=10` (SIGTERM, 10 s, SIGKILL); 304 and 404 are success. **Docker deadline:** `status()` of a `running` container whose `StartedAt + timeout` has passed sends `POST /containers/{id}/kill` and reports `exited` with `ExitStatus(137, timed_out=True)`; an `exited` container is `timed_out` when `ExitCode != 0` and `FinishedAt − StartedAt ≥ timeout`. Overrun is bounded by the reconcile cadence (≤ one tick, 60 s).
- **Docker events:** `GET /v1.43/events?filters={"type":["container"],"label":["stac-higher.run-id"],"event":["start","die","oom","destroy"]}` over an asyncio stream to the socket proxy; every (re)connect yields one RESYNC event first; no `since` bookkeeping.
- **Socket proxy:** `EVENTS=1` written explicitly (it is already on by the image default, `tecnativa/docker-socket-proxy:0.3.0` sets `EVENTS=1 PING=1 VERSION=1`); `PING=0` and `VERSION=0` written explicitly (nothing uses them — a narrowing). The compose test pins the explicit enabled set `{CONTAINERS, POST, IMAGES, INFO, EVENTS}` and that every image-default-on key is listed. The proxy's `docker-events` backend has `timeout server 0`; the frontend's `timeout client 10m` can still close an idle stream, which the watcher's reconnect absorbs.
- **Credentials:** process runs mint `max(900, max_queue_wait_seconds + timeout_seconds + PROCESS_CREDENTIAL_GRACE_SECONDS)` seconds (`max_queue_wait_seconds` from the resolved profile); image scans mint with a queue wait of 0 (unchanged duration). Above `PROCESS_MAX_CREDENTIAL_SECONDS` → `CredentialBudgetExceeded` (dead), never clamped. `credentials_expire_at` = the clock read *before* the STS call + the duration (a lower bound on the real expiry). Requeue rule: a submitted run whose backend reports `pending_capacity` and whose `credentials_expire_at − now < timeout_seconds + grace` is cancelled and requeued, attempt restored, `phase = pending_capacity`, `phase_detail = "requeued: its storage credentials would expire before the run could finish; resubmitting with fresh ones"`, `next_attempt_at = now`, plus a `process_run_now` enqueue.
- **Constants:** `RECONCILE_BATCH = 50` (rows per tick sweep), `WATCHER_RECONNECT_SECONDS = 5.0`, `DOCKER_STOP_GRACE_SECONDS = 10`, `INFRA_RETRY_SECONDS = 60` (the existing `DEFAULT_RETRY_WAIT_SECONDS`, now written to `next_attempt_at`).
- **Cancel route:** `POST /api/processes/[id]/runs/[runId]/cancel`, operator+ of the owning group, audited `cancel` on resource type `process` (the run id stays in the audited path, the `rerun` precedent). 202 `{ run, outcome: "cancelled" | "cancel_requested" }`; 409 `{ error, code: "run_not_cancellable" }` naming the status; 404 for an unknown run or one outside the caller's groups.
- **Do not touch:** the finalize path (`finalize/*`, `ProcessRunRecorder` keeps its unconditional `finish_run`), `delivery/`, the `procrastinate` schema, the pgstac writer, `RunSpec`'s existing fields, `components/ui/` (shadcn — the `cancelled` badge uses an existing variant). No new dependency. No `build_executor` factory and no `/health` executor key (K-5). Structured logging: data in `extra={...}`, constant messages; never `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  ```

## Invariants — who may move a run, and under what compare-and-set

These are the design this plan settles before any code; Task 2 pins them against Postgres and every later task cites them. "Submitted" means `executor_handle IS NOT NULL`. The **claim stamp** is the `started_at` a claim wrote (returned to the worker as `QueuedRun.claimed_at`); it fences the claiming worker's writes against a stale worker whose row was requeued and re-claimed under it.

| # | Move | Writer | Compare-and-set predicate | Notes |
|---|---|---|---|---|
| I1 | `queued → running` (`phase=starting`) | `claim_run`, `claim_due_runs` | `status='queued'` AND rate deferral elapsed AND `next_attempt_at` elapsed | stamps `started_at=now`, `attempts+1`; clears `executor_handle, executor_backend, submitted_at, credentials_expire_at, next_attempt_at` |
| I2 | `failed → running` | `claim_due_runs` | `status='failed' AND finished_at <= now` | as I1 |
| I3 | unsubmitted `running →` `dead`/`failed` | `run_one` → `finish_run(claimed_at=)` | `status='running' AND started_at=claimed_at` | cancel requested ⇒ lands `cancelled` (I10) |
| I4 | unsubmitted `running → queued` (infra fault; capacity release) | `requeue_without_attempt(claimed_at=)`, `release_for_capacity(claimed_at=)` | as I3 | attempt restored; retry time in `next_attempt_at`; may FOLD (#67); cancel requested ⇒ `cancelled` |
| I5 | unsubmitted → submitted | `record_submission` | `status='running' AND started_at=claimed_at AND executor_handle IS NULL` | writes handle, backend, `submitted_at`, `phase=executor.submitted_phase`, `credentials_expire_at`; returns whether cancel was already requested. A failed CAS ⇒ the worker cancels and reaps what it just submitted |
| I6 | submitted phase change | `set_phase` | `status='running' AND executor_handle=H` AND (phase, detail) differ | never terminal |
| I7 | submitted `running →` `succeeded`/`failed`/`dead`/`cancelled` | the Reconciler, inside `collecting(id, H)` | row locked `FOR UPDATE SKIP LOCKED` with `status='running' AND executor_handle=H` | logs + log object BEFORE the verdict (I-62 order); `reap` AFTER commit |
| I8 | submitted `running → queued` (lost; credential expiry; K-5 eviction) | the Reconciler, inside `collecting(id, H)` | as I7 | attempt restored; may FOLD; cancel requested ⇒ `cancelled` |
| I9 | stale unsubmitted `running → queued` | stall sweep (`reset_stalled_runs`) | `status='running' AND executor_handle IS NULL AND started_at < now − PROCESS_RUN_STALL_SECONDS` | a SUBMITTED row is never stall-swept: it is reconciled through `status()` |
| I10 | any pipeline move out of `running` while `cancel_requested_at IS NOT NULL` | the writers of I3, I4, I7, I8, I9 | their own predicate | lands `cancelled` (`finished_at=now`, phase NULL, the error keeps what happened) — **cancel wins** |
| I11 | `queued`/`failed → cancelled` | app `cancelRun` | `status IN ('queued','failed')` (row lock serialises it against I1/I2) | `cancel_requested_at=now`, `finished_at=now`, phase/next_attempt/rate deferral cleared |
| I12 | `running`: request cancel | app `cancelRun` | `status='running'` | `cancel_requested_at = coalesce(cancel_requested_at, now())` + `pg_notify('process_run_cancel','')` in the same statement |
| I13 | `dead`/`cancelled → queued` | app `rerunRun` | `status IN ('dead','cancelled')` | clears executor columns, `cancel_requested_at`, `credentials_expire_at` too |
| I14 | `succeeded → succeeded`/`dead` | finalize `ProcessRunRecorder` / extract finalize | unconditional (unchanged) | only ever on its own `succeeded` row; cancel cannot race it (I11/I12 exclude `succeeded`) |

**Exactly-once finalisation** follows from I7: the verdict UPDATE runs only in a transaction holding the lock on a row that was `running` with handle H when locked; it moves the row out of `running`, so every other observer (watcher, tick, cancel listener, a second worker) either skipped the locked row or re-reads a non-`running` row and does nothing. A crash before commit rolls back — the row is still `running`/H and the container is not yet reaped (reap is after commit), so the next observer collects again from the same container and overwrites the log object at its deterministic key with the same bytes. A crash after commit and before `reap` leaves a terminal row and a live/exited container: the M3-W-1 reaper's ledger rule removes it. **Not** exactly-once (pre-existing, unchanged, recorded in Task 10): the `process_finalize` enqueue and the extractor `on_dead` call happen after commit; a crash in that window loses the publish (or leaves extractor rows to the ingest stall sweep). `record_source_run` moves inside the collecting transaction, so `flow_stats` is exactly-once.

## Rulings

Each is a place the spec is silent, ambiguous or contradicts the code; decided here, with the cost if wrong.

- **R1 · Events are wake-ups, `status()` is the truth.** `RunEvent` carries a run id (or RESYNC) and the handle, never a state the applier trusts. Every transition re-reads `status()` first. *Why:* the item_events NOTIFY precedent (payload-less, ADR 0007); a missed, duplicated or reordered event cannot produce a wrong transition. *Cost if wrong:* one extra backend read per event (Docker inspect; for K-5 a Job + Workload read) — cheap at run volumes.
- **R2 · Verdicts are written under a row lock held across log capture (`collecting`).** *Why:* a plain CAS UPDATE after an unlocked log read lets a losing observer overwrite the log object with an empty read after the winner reaped. *Cost:* a pooled connection is held for the log read + object PUT (seconds, ≤ 10 MB); a blocked row lock delays a concurrent `set_phase` by the same.
- **R3 · Cancel wins (I10).** Once `cancel_requested_at` is set, the run ends `cancelled`, even if it exited 0 between the click and the stop; the error records the exit (`"cancelled by an operator; the run had already exited 0"`). *Why:* the only crash-safe rule — after a stop, a 143/137 exit is indistinguishable from a natural failure on restart. *Cost:* a run that succeeded seconds before a cancel is not published (its outputs age out of staging).
- **R4 · `failed` (retry pending) is cancellable** and flips to `cancelled` immediately, like `queued`. The spec names only `queued` and `running`. *Why:* a retry-pending run is owed work exactly like a queued one, and "stop this flapping run" is the main use. *Cost:* none known; a deviation from the spec text recorded in the PR.
- **R5 · `started_at` keeps meaning "claimed"** and doubles as the claim fence; the spec's "`started_at` now means the backend reported running" is not adopted. *Why:* `count_running_on_profile` orders by it, the stall sweep and reaper read it, and a new fencing column would be a second migration change. The backend's "running" moment is the phase change. *Cost:* the UI duration includes the submit time (seconds on Docker; the queue wait on K-5 — revisit there with a `running_at` if it matters).
- **R6 · `credentials_expire_at` is a new column (migration 031)**, written at submission. The spec names no column. *Why:* deriving expiry at reconcile time from the profile file would change the answer when the file changes. *Cost:* one nullable column.
- **R7 · Mint refuses, never clamps** (`mint_allowed: false` for "one over", as the R-2 note on #12 asks). The spec's "capped by the role's maximum session duration" reads as the deployment cap being the ceiling. *Why:* the app's deploy gate already counts the queue wait, so both runtimes agree; I-149 already names the mint refusal the backstop. *Cost:* raising a profile's `max_queue_wait_seconds` after deploy kills that profile's over-budget revisions dead at launch with `credential_budget_exceeded` rather than shortening their wait promise.
- **R8 · The queue wait is minted on Docker too.** Docker never holds a submitted run for capacity (K-3 releases before submit), so the extra lifetime is unused there. *Why:* one rule in both runtimes and the fixture. *Cost:* local run credentials live up to `max_queue_wait_seconds` (local profiles: 30–60 min) longer than needed — a wider leak window for a credential scoped to one run prefix.
- **R9 · The requeue rule is dormant on Docker** and is built and unit-tested against a scripted executor; it fires live only under K-5/K-6. *Cost:* the live check cannot exercise it before K-6's kind leg.
- **R10 · Docker enforces the timeout on read** (the timeout label + `status()`), not a per-run timer in the watcher. *Why:* it keeps the reconciler backend-agnostic (Kubernetes does this server-side) and is crash-safe (the kill and its `timed_out` verdict are recomputed from `StartedAt`/`FinishedAt`). *Cost:* a run may overrun its timeout by up to one tick (60 s) when no event arrives; credentials carry 300 s of grace.
- **R11 · Image scans move to `submit` + a polling helper (`run_to_exit`) and keep blocking in their thread.** They do not join the watcher (it ignores `run-kind=image_scan`). *Why:* the scan verdict path (`images/drain.py`) is built around one blocking call; moving it to reconcile is its own slice. *Cost:* **I-124 stays open** (a scan still holds one worker slot, measured 82–109 s); Task 10 rewords it and opens a follow-up issue. The lead may overrule and pull it in (≈ one more task).
- **R12 · The cancel wake is `pg_notify('process_run_cancel', '')` fired by the app's cancel statement**, consumed by the existing `run_dispatch_listener` loop with a different channel (payload-less; its connect wake drains pending cancels). *Why:* event-driven (the G-3 latency posture) without a trigger in DDL. *Cost:* the listener's log line says "dispatch listener connected" with `channel` in `extra` — reworded generically in Task 6.
- **R13 · Every "no attempt spent" requeue restores the attempt** (`attempts = greatest(attempts − 1, 0)`) and writes its retry time to `next_attempt_at`, not `finished_at`. Today the infrastructure requeue does neither (the claim never reads `finished_at` for a queued row, and the claim's `attempts+1` is never undone), as K-3's implementer flagged. *Cost:* a permanent infrastructure fault now requeues forever at 60 s spacing instead of eventually exhausting the budget — the documented intent of `infrastructure_transition` ("a persistent outage surfaces as a stalled flow").
- **R14 · The stall sweep covers unsubmitted rows only (I9).** *Why:* today a run longer than `PROCESS_RUN_STALL_SECONDS` (3600) is requeued while still executing and the reaper then kills its container — with submit-then-reconcile every long run would hit it. *Cost:* a submitted run whose backend wedges in `running` forever is bounded only by the timeout (Docker: R10) and the reaper's age rule.
- **R15 · `lost` requeues without spending an attempt** (I8), like K-5's `Evicted`. *Cost:* something that keeps deleting run containers loops the run at 60 s spacing; logged at WARNING each time.
- **R16 · No `build_executor` factory, no `/health` executor key** — K-5 owns both (spec §6). The watcher constructs its own `DockerExecutor`, like the tick. `executor_backend` is written at submission and the Reconciler skips rows whose backend is not its executor's `name`.
- **R17 · Extractor runs are cancellable** (re-run stays refused for them). Their `extracting` ingest rows are released by the ingest stall sweep, which already treats any non-open run status as closed (`ingest/repo.py`), not by a new cancel hook. *Cost:* those rows wait for the ingest stall window.
- **R18 · Test-run phases (spec §9 `TestRunCard`) are out of scope**: UI test runs are never drained today (I-122, #55). `priority: interactive` is already passed. When #55 lands, `finish_check` belongs in the Reconciler's post-commit step, not in `run_one` — noted on #55 by Task 10.
- **R19 · No new ADR.** These are ADR 0019's submit-then-reconcile semantics; they are recorded in `process/executor.py`'s and `process/reconcile.py`'s module docstrings and the pipeline README. The lead may promote R1–R3 to an ADR if K-5 review wants a citable contract.
- **R20 · The `cancelled` badge reuses the `outline` variant** with muted, struck-through text; spec §9 asks for "its own badge variant", but `components/ui/` is not hand-edited.

## Review Focus

- **A worker restart while a run is mid-collection** (logs read, object written, verdict not committed): the row must still finalize exactly once with its log — Task 2's `collecting` rollback test and Task 5's crash-replay test.
- **A cancel that races the run's own exit** (die event and cancel within a second): the row ends `cancelled` once, the container is gone, finalize is not enqueued — Task 5 `test_cancel_wins_over_a_concurrent_exit`.
- **A stale worker writing after its row was re-claimed** (slow staging past the stall window): its `finish_run`/`record_submission` must match nothing and the container it submitted must be cancelled and reaped — Task 2 fence tests, Task 5 `test_a_lost_claim_cancels_what_it_submitted`.
- **A long run (timeout above 3600 s)**: never stall-swept, never reaped while running — Task 2 `test_stall_sweep_skips_submitted_rows`.
- **The events stream going quiet or dropping** (haproxy's 10-minute client timeout, a daemon restart): the watcher reconnects, RESYNCs, and no exit is missed — Task 6 reconnect test plus the tick backstop.

---

## Tasks

### Task 0: Pre-flight

- [ ] `gh pr list --state merged --search "K-3 in:title"` shows K-3 (#11) merged. If not merged: STOP implementation beyond Task 1 (Task 1 is the only one that does not depend on K-3 surviving review unchanged) and report.
- [ ] `git fetch origin && git rebase origin/main` in the worktree (K-3's commits drop out once merged). Then confirm main's credential work is present: `grep -n "class CredentialDurationRejected" services/pipeline/src/pipeline/process/credentials.py` and `grep -n '"one over"' tests/contract-fixtures/process-credential-budget.json` (expect `"mint_allowed": true` — Task 4 flips it).
- [ ] Confirm the K-3 behaviour this plan depends on is still in the tree: both claim statements set `phase = 'starting'` and honour `next_attempt_at`; `release_for_capacity` goes through `_requeue`; `count_running_on_profile(profile_id, *, ahead_of_run_id)`; `process_runs_phase_check`; `RunsCard` exported from `ProcessDetailPage.tsx`. A review change to any of these: adjust the matching task before starting it.
- [ ] Migration numbers: `grep -n 'name: "03' app/src/lib/db/migrate.ts` shows 030, 032, 033 and no 031. Reserve 031 on the issue: `gh issue edit 12 --add-label migration` and `gh issue comment 12 --body "K-4 takes migration 031 (031_process_runs_cancelled): status CHECK gains cancelled; credentials_expire_at."`. Assign yourself: `gh issue edit 12 --add-assignee @me`.
- [ ] `npm install` at the worktree root; `uv sync` in `services/pipeline/`. Gates green before any change.

### Task 1: The status fixture, migration 031, the ledger vocabulary

**Files:**
- Create: `tests/contract-fixtures/process-run-status.json`
- Modify: `tests/contract-fixtures/README.md` (one table row)
- Modify: `app/src/lib/db/migrate.ts` (031 between 030 and 032)
- Modify: `app/src/lib/processes/storage.ts` (`RunRow.status`, export `ProcessRunStatus`, `CANCELLABLE_RUN_STATUSES`, `PROCESS_RUN_CANCEL_CHANNEL`)
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` (`RUN_STATUS_VARIANT` gains a `cancelled` key — typecheck only)
- Modify: `services/pipeline/src/pipeline/process/ledger.py`
- Test: `app/src/__tests__/processes-migration.test.ts` (append), `app/src/__tests__/contract-fixtures.test.ts` (append), `services/pipeline/tests/test_contract_fixtures.py` (append)

**Interfaces:**
- Produces:
  ```json
  { "statuses": ["queued","running","succeeded","failed","dead","cancelled"],
    "terminal": ["succeeded","dead","cancelled"],
    "cancellable": ["queued","failed","running"],
    "cancel_channel": "process_run_cancel" }
  ```
  ```ts
  // storage.ts
  export type ProcessRunStatus = "queued" | "running" | "succeeded" | "failed" | "dead" | "cancelled";
  export const CANCELLABLE_RUN_STATUSES: readonly ProcessRunStatus[]; // fixture order
  export const PROCESS_RUN_CANCEL_CHANNEL = "process_run_cancel";
  ```
  ```python
  # ledger.py
  STATUSES = ("queued", "running", "succeeded", "failed", "dead", "cancelled")
  TERMINAL_STATUSES = ("succeeded", "dead", "cancelled")
  CANCELLABLE_STATUSES = ("queued", "failed", "running")
  CANCEL_CHANNEL = "process_run_cancel"
  EXECUTOR_PHASES = (*PHASES, "exited", "lost")   # RunState vocabulary; never stored
  ```
  Migration name: `031_process_runs_cancelled`.

- [ ] **Step 1: The fixture.** Write the JSON above with a `$comment` naming K-4, spec §5.2/§5.3, the CHECK and both readers; add the README row (match the table's real columns).

- [ ] **Step 2: Failing tests.**
  - `processes-migration.test.ts`, `describe("migration 031 (cancelled — K-4)")`: `it("sits between 030 and 032")` (index of `"031_process_runs_cancelled"` > index of `"030_container_images"` and < `"032_cube_sinks"`); `it("constrains status to exactly the fixture's statuses")` (parse the `status IN (...)` list from the 031 SQL, `toEqual(fixture.statuses)`); `it("adds credentials_expire_at, nullable")` (`ADD COLUMN IF NOT EXISTS credentials_expire_at timestamptz`, no `NOT NULL`); `it("names the new constraint process_runs_status_check")`.
  - `contract-fixtures.test.ts`, `describe("process run status contract")`: `CANCELLABLE_RUN_STATUSES` equals `fixture.cancellable`; `PROCESS_RUN_CANCEL_CHANNEL` equals `fixture.cancel_channel`; every fixture status is assignable — assert a `Record<ProcessRunStatus, true>` literal's keys equal `fixture.statuses` (sorted).
  - `test_contract_fixtures.py`: `test_process_run_status_vocabulary_matches_golden` — `ledger.STATUSES`, `TERMINAL_STATUSES`, `CANCELLABLE_STATUSES`, `CANCEL_CHANNEL` equal the fixture's; `set(OPEN_STATUSES) | set(TERMINAL_STATUSES) == set(STATUSES)` and disjoint.

- [ ] **Step 3: Run to verify they fail** — `npx vitest run src/__tests__/processes-migration.test.ts src/__tests__/contract-fixtures.test.ts` (from `app/`), `uv run pytest tests/test_contract_fixtures.py -q`.

- [ ] **Step 4: Migration 031.** The `status` CHECK was declared inline in the 024-era `CREATE TABLE`, so its name is Postgres's default; drop it by lookup, not by guessed name:
  ```sql
  DO $do$
  DECLARE c record;
  BEGIN
    FOR c IN
      SELECT conname FROM pg_constraint
       WHERE conrelid = 'stac_higher.process_runs'::regclass AND contype = 'c'
         AND conname <> 'process_runs_phase_check'
         AND pg_get_constraintdef(oid) LIKE '%status%'
    LOOP
      EXECUTE format('ALTER TABLE stac_higher.process_runs DROP CONSTRAINT %I', c.conname);
    END LOOP;
    ALTER TABLE stac_higher.process_runs ADD CONSTRAINT process_runs_status_check
      CHECK (status IN ('queued','running','succeeded','failed','dead','cancelled'));
  END
  $do$;
  ALTER TABLE stac_higher.process_runs
    ADD COLUMN IF NOT EXISTS credentials_expire_at timestamptz;
  ```
  The comment above the entry names K-4, the fixture, R6, and that `process_runs_terminal_age_idx` deliberately stays `('succeeded','dead')` (no reader yet — the retention leg that will read it must include `cancelled`; Task 10 records it).

- [ ] **Step 5: The vocabularies.** `storage.ts`: `RunRow.status: ProcessRunStatus`; the two exports. `RUN_STATUS_VARIANT` in `ProcessDetailPage.tsx` is a `Record` over the status union, so it gains `cancelled: "outline"` here to keep the typecheck green (Task 8 styles it). `ledger.py`: the constants above, with `OPEN_STATUSES` unchanged and a comment that `cancelled` is terminal and never claimable.

- [ ] **Step 6: Gates and commit.** `npm run verify`; `uv run pytest -q`; `uv run ruff check .`.
  ```bash
  git commit -m "K-4: migration 031 — cancelled status + credentials_expire_at; process-run-status fixture binds both runtimes"
  ```

### Task 2: The compare-and-set ledger (Invariants I1–I10)

The riskiest piece, built and pinned against Postgres first. No executor change yet: `run_one` adopts the claim fence and the attempt-restoring requeue; nothing submits until Task 5.

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py` (`QueuedRun`, both claims, `finish_run`, `release_for_capacity`, `reset_stalled_runs`, `_requeue`/`_requeue_once`; new `SubmittedRun`, `SubmissionRecord`, `CollectingRun`, `record_submission`, `set_phase`, `list_submitted_runs`, `get_submitted_run`, `collecting`, `requeue_without_attempt`)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (`_finish`, `_requeue_infrastructure`, the executor-outage branch, the capacity release — pass `claimed_at`; use the returned status)
- Modify: `services/pipeline/tests/_process_fake.py` (the same contracts in memory)
- Create: `services/pipeline/tests/test_integration_process_reconcile.py` (DB-gated)
- Test: `services/pipeline/tests/test_process_ledger_cas.py` (new, fake-backed), existing `test_process_triggers.py` / `test_process_capacity.py` adjusted for `claimed_at`

**Interfaces:**
- Produces (all on `ProcessRepo`, abstract, with Pg + fake implementations):
  ```python
  @dataclass(frozen=True)
  class QueuedRun:            # existing, gains:
      claimed_at: dt.datetime | None = None   # the started_at the claim wrote (I1/I2)

  @dataclass(frozen=True)
  class SubmittedRun:
      id: str; process_id: str; revision_id: str
      source_id: str | None; association_id: str | None
      attempts: int; is_test: bool
      handle: str; backend: str
      phase: str | None
      submitted_at: dt.datetime | None
      cancel_requested_at: dt.datetime | None
      credentials_expire_at: dt.datetime | None
      runtime: dict[str, Any]
      input_items: list[dict[str, Any]]

  @dataclass(frozen=True)
  class SubmissionRecord:
      cancel_requested: bool

  class CollectingRun(abc.ABC):        # yielded by collecting(); valid only inside the block
      run: SubmittedRun
      async def finish(self, *, status: str, error: str | None, log_ref: str | None,
                       next_attempt_at: dt.datetime | None, at: dt.datetime) -> str: ...
      async def requeue(self, *, error: str | None, phase: str | None, phase_detail: str | None,
                        next_attempt_at: dt.datetime) -> str: ...

  async def finish_run(self, run_id, *, status, error, log_ref, next_attempt_at,
                       output_items=None, claimed_at: dt.datetime | None = None) -> str | None
  async def requeue_without_attempt(self, run_id, *, claimed_at: dt.datetime, error: str,
                                    next_attempt_at: dt.datetime) -> str
  async def release_for_capacity(self, run_id, *, claimed_at: dt.datetime,
                                 next_attempt_at: dt.datetime, detail: str) -> str
  async def record_submission(self, run_id, *, claimed_at: dt.datetime, handle: str,
                              backend: str, phase: str, submitted_at: dt.datetime,
                              credentials_expire_at: dt.datetime | None) -> SubmissionRecord | None
  async def set_phase(self, run_id, *, handle: str, phase: str, detail: str | None) -> bool
  async def list_submitted_runs(self, *, backend: str, limit: int,
                                cancel_requested_only: bool = False) -> list[SubmittedRun]
  async def get_submitted_run(self, run_id: str, *, backend: str) -> SubmittedRun | None
  def collecting(self, run_id: str, *, handle: str) -> AbstractAsyncContextManager[CollectingRun | None]
  ```
  Return vocabularies: `finish_run` → the status written (`cancelled` when I10 applied) or `None` when the CAS matched nothing (only possible with `claimed_at`); `finish`/`requeue`/`requeue_without_attempt`/`release_for_capacity` → `"succeeded" | "failed" | "dead" | "cancelled" | "requeued" | "merged" | "skipped"` as applicable. `finish_run` without `claimed_at` keeps today's unconditional UPDATE (I14's caller).

- [ ] **Step 1: Failing fake-backed tests** (`tests/test_process_ledger_cas.py`) — they pin the fake, which every later unit test leans on:
  - `test_a_claim_returns_its_stamp_and_clears_the_executor_columns` — a row carrying a stale `executor_handle`/`submitted_at`/`credentials_expire_at` from an earlier attempt comes back from `claim_run` with them cleared and `claimed_at == now`.
  - `test_finish_with_a_stale_claim_stamp_matches_nothing` — `finish_run(..., claimed_at=other)` returns `None`, row untouched.
  - `test_finish_lands_cancelled_when_a_cancel_was_requested` — `cancel_requested_at` set, `finish_run(status="dead", claimed_at=c)` returns `"cancelled"`.
  - `test_requeue_without_attempt_restores_the_attempt_and_uses_next_attempt_at` — attempts 2 → 1, `next_attempt_at` set, `finished_at` untouched, status `queued`, phase `None`.
  - `test_record_submission_is_fenced_and_one_shot` — first call returns `SubmissionRecord(False)`, second returns `None`; with `cancel_requested_at` set the first returns `SubmissionRecord(True)`.
  - `test_set_phase_needs_the_current_handle` — wrong handle → `False`.
  - `test_collecting_yields_none_for_a_row_already_collected` and `test_collecting_finish_moves_the_row_out_of_running_once`.

- [ ] **Step 2: Failing DB-gated tests** (`tests/test_integration_process_reconcile.py`, header in the `test_integration_process_capacity.py` style: skips without `DATABASE_URL`, needs migration 031, disabled process, far-future `rate_deferred_until` on seeded rows so the live tick never claims them; insert rows directly as `running` with a chosen `started_at`/handle where a claim is not under test):
  - `test_two_concurrent_collectors_write_one_verdict` — two tasks enter `collecting(id, handle=H)` concurrently (each on its own connection); exactly one yields a `CollectingRun`, the other `None`; after both, the row is `succeeded` once and `record_source_run` bumped `flow_stats.runs` by exactly 1.
  - `test_a_collector_that_raises_rolls_back_and_the_next_one_collects` — first block raises after `finish(...)`; the row is still `running`/H; a second `collecting` succeeds.
  - `test_set_phase_after_the_verdict_is_a_noop` — `set_phase` returns `False` once the row left `running`.
  - `test_stall_sweep_skips_submitted_rows` — two `running` rows with `started_at` 2 h old, one with a handle: `reset_stalled_runs` requeues only the unsubmitted one.
  - `test_a_stale_worker_cannot_finish_a_reclaimed_row` — row requeued and re-claimed (new `started_at`); `finish_run(claimed_at=old)` returns `None`; `record_submission(claimed_at=old)` returns `None`.
  - `test_requeue_with_cancel_requested_lands_cancelled` — via `requeue_without_attempt` and via `CollectingRun.requeue`.
  - `test_a_collector_requeue_folds_into_an_occupied_slot` — the #67 shape: a queued run holds the source's slot; `CollectingRun.requeue` returns `"merged"`, items appended, own row deleted.
  - `test_claim_and_app_cancel_serialise` — run the app's cancel statement (copied verbatim from Task 7 Step 4 as a constant in the test, with a comment pointing at `storage.ts`) concurrently with `claim_run`: the row ends either `cancelled` (cancel first) or `running` with `cancel_requested_at` set (claim first) — never `queued`.

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_process_ledger_cas.py -q`; with a Postgres at migration 031: `DATABASE_URL=… uv run pytest tests/test_integration_process_reconcile.py -q`.

- [ ] **Step 4: The SQL.** One rule per statement, every one a single round trip or a single transaction:
  - **Claims (I1/I2):** both statements add to `SET`: `executor_handle = NULL, executor_backend = NULL, submitted_at = NULL, credentials_expire_at = NULL`, and `RETURNING ... r.started_at` → `QueuedRun.claimed_at`.
  - **`finish_run` with `claimed_at` (I3, I10):**
    ```sql
    UPDATE stac_higher.process_runs
       SET status = CASE WHEN cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE %(status)s END,
           error = CASE WHEN cancel_requested_at IS NOT NULL
                        THEN 'cancelled by an operator' || coalesce('; ' || %(error)s, '')
                        ELSE %(error)s END,
           log_ref = coalesce(%(log_ref)s, log_ref),
           phase = NULL, phase_detail = NULL,
           finished_at = CASE WHEN cancel_requested_at IS NOT NULL THEN now()
                              ELSE coalesce(%(next_attempt_at)s, now()) END
     WHERE id = %(id)s AND status = 'running' AND started_at = %(claimed_at)s
       AND executor_handle IS NULL
    RETURNING status
    ```
    `status="queued"` is no longer accepted with `claimed_at` (raise `ValueError`) — callers use `requeue_without_attempt`.
  - **`_requeue_once`** gains keyword fences `claimed_at`, `handle`, `stalled_before` (each adds its predicate: `started_at = %(claimed_at)s AND executor_handle IS NULL` / `executor_handle = %(handle)s` / `executor_handle IS NULL AND started_at < %(before)s`) and selects `cancel_requested_at`; when it is set, it UPDATEs the row to `cancelled` (`finished_at = now()`, phase/detail/next_attempt NULL, error prefixed as above) and returns `"cancelled"` instead of requeueing or folding. Its requeue `set_sql` for every caller now also clears `executor_handle, executor_backend, submitted_at, credentials_expire_at`.
  - **`requeue_without_attempt` (I4, R13):** `_requeue(conn, run_id, claimed_at=…, set_sql="started_at = NULL, attempts = greatest(attempts - 1, 0), error = %(error)s, phase = NULL, phase_detail = NULL, next_attempt_at = %(at)s")`. **`release_for_capacity`** gains the `claimed_at` fence, otherwise unchanged.
  - **`reset_stalled_runs` (I9):** the candidate SELECT adds `AND executor_handle IS NULL`; the `_requeue` call passes `stalled_before` (which now carries the handle predicate too). The partial index `process_runs_running_idx (started_at) WHERE status='running'` still serves it.
  - **`record_submission` (I5):** `UPDATE ... SET executor_handle, executor_backend, submitted_at, phase, phase_detail = NULL, credentials_expire_at WHERE id AND status='running' AND started_at = %(claimed_at)s AND executor_handle IS NULL RETURNING cancel_requested_at IS NOT NULL`.
  - **`set_phase` (I6):** `UPDATE ... SET phase = %(p)s, phase_detail = %(d)s WHERE id AND status='running' AND executor_handle = %(h)s AND (phase IS DISTINCT FROM %(p)s OR phase_detail IS DISTINCT FROM %(d)s)`; returns `rowcount == 1`.
  - **`list_submitted_runs` / `get_submitted_run`:** `SELECT r.*, rev.runtime FROM process_runs r JOIN process_revisions rev ON rev.id = r.revision_id WHERE r.status = 'running' AND r.executor_handle IS NOT NULL AND r.executor_backend = %(backend)s [AND r.cancel_requested_at IS NOT NULL] ORDER BY r.cancel_requested_at NULLS LAST, r.submitted_at LIMIT %(limit)s` (cancel requests first: they are what an operator is watching).
  - **`collecting` (I7/I8):** an `@asynccontextmanager` on `PgProcessRepo` that opens one connection, `BEGIN`s, runs the `get_submitted_run` SELECT with `AND r.id = %(id)s AND r.executor_handle = %(h)s FOR UPDATE OF r SKIP LOCKED`, yields `None` when nothing came back, otherwise a `_PgCollectingRun(conn, run)`; commits on a clean exit and rolls back on any exception (including `CancelledError`). `finish(...)` runs the `finish_run` UPDATE with `executor_handle = %(h)s` in place of the claim fence and, in the same transaction, `record_source_run`'s UPDATE when `source_id` is set and the written status is `succeeded` or `dead`. `requeue(...)` calls `_requeue_once(conn, ..., handle=H)` inside a savepoint (`async with conn.transaction()`), retrying up to `_REQUEUE_ATTEMPTS` on `UniqueViolation` — the outer transaction must survive a fold race. A second `finish`/`requeue` on the same `CollectingRun` raises `RuntimeError`.

- [ ] **Step 5: The fake.** `FakeProcessRepo` grows rows that carry `started_at`, `executor_handle`, `executor_backend`, `submitted_at`, `cancel_requested_at`, `credentials_expire_at` and implements every predicate above literally (its `collecting` uses a per-run `asyncio.Lock` acquired with `locked()`-check-then-acquire so a second concurrent collector gets `None`, mirroring SKIP LOCKED). Keep its recording lists (`finished`, `released`) so existing tests stay readable.

- [ ] **Step 6: `run_one` adopts the fence.** Every `_finish` / `repo.finish_run` / `release_for_capacity` call in `runner.py` passes `claimed_at=run.claimed_at`; the executor-outage branch and `_requeue_infrastructure` call `requeue_without_attempt(run.id, claimed_at=run.claimed_at, error=…, next_attempt_at=at + 60 s)` instead of `finish_run(status="queued")`. `_finish` uses the status `finish_run` returns for the metric label, `record_source_run` and `on_dead` (a `cancelled` or `None` result triggers neither). `infrastructure_transition` stays in `ledger.py` (it still names the retry time).

- [ ] **Step 7: Gates and commit.** `uv run pytest -q`, `uv run ruff check .`, the DB-gated file where a Postgres is available (otherwise say so in the commit body).
  ```bash
  git commit -m "K-4: compare-and-set run ledger — claim fence, attempt-restoring requeue, record_submission, set_phase, collecting under a row lock; stall sweep spares submitted runs"
  ```

### Task 3: The executor contract — `submit / status / cancel`, `wait` removed

The ABC changes shape; the Docker and memory backends follow; image scans and (temporarily) `execute_run` are rebuilt on a polling helper so behaviour is unchanged and the tree stays green. Task 5 removes `execute_run`.

**Files:**
- Modify: `services/pipeline/src/pipeline/process/executor.py` (ABC, `RunState`, `RunEvent`, `RESYNC`, `run_to_exit`; module docstring rewritten around R1/R10)
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py` (`launch` → `submit`, `status`, `cancel`, `TIMEOUT_LABEL`, `submitted_phase`; `wait` deleted)
- Modify: `services/pipeline/src/pipeline/process/memory_executor.py` (scripted states)
- Modify: `services/pipeline/src/pipeline/process/launch.py` (`execute_run` on `submit` + `run_to_exit`, interim)
- Modify: `services/pipeline/src/pipeline/images/scan_launch.py` (`execute_scan` on `submit` + `run_to_exit`)
- Test: `tests/test_process_executor.py` (the `wait` tests replaced), `tests/test_docker_executor_user_images.py`, `tests/test_image_scan_launch.py`, `tests/test_process_reaper.py` (MemoryExecutor construction), new `tests/test_executor_contract.py`

**Interfaces:**
- Produces:
  ```python
  # executor.py
  @dataclass(frozen=True)
  class RunState:
      phase: str                      # one of ledger.EXECUTOR_PHASES
      detail: str | None = None       # the backend's own words (UI tooltip); never trusted as content
      exit: ExitStatus | None = None  # set iff phase == "exited"

  @dataclass(frozen=True)
  class RunEvent:
      run_id: str | None              # None = RESYNC: reconcile everything submitted
      handle_id: str | None = None
      kind: str = "process"           # one of RUN_KINDS, from the backend's label

  RESYNC = RunEvent(run_id=None)

  class Executor(abc.ABC):
      name: str
      #: the phase record_submission writes: what a just-submitted run is on this backend
      submitted_phase: ClassVar[str] = "pending_capacity"
      @abc.abstractmethod
      def submit(self, spec: RunSpec) -> RunHandle: ...      # create + start, returns immediately
      @abc.abstractmethod
      def status(self, handle: RunHandle) -> RunState: ...   # one read; may enforce the backend's deadline
      @abc.abstractmethod
      def cancel(self, handle: RunHandle) -> None: ...       # idempotent stop; resources kept for logs/reap
      @abc.abstractmethod
      def logs(self, handle: RunHandle, max_bytes: int) -> bytes: ...   # unchanged
      @abc.abstractmethod
      def reap(self, handle: RunHandle) -> None: ...                    # unchanged
      @abc.abstractmethod
      def list_launched(self) -> list[LaunchedRun]: ...                 # unchanged
      def watch(self) -> AsyncIterator[RunEvent]: ...        # optional: default raises NotImplementedError
      @property
      def supports_watch(self) -> bool: ...                  # True iff the subclass overrides watch

  def run_to_exit(executor: Executor, handle: RunHandle, timeout_seconds: int, *,
                  poll_seconds: float = 2.0,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep) -> ExitStatus
  ```
  `status()` raises `ExecutorUnavailable` when the backend cannot be asked and reports `lost` **only** when the backend affirms the run is gone (Docker: inspect 404) — the reaper's "an outage is not an absence" rule. `run_to_exit` polls `status()` until `exited` (returns its `ExitStatus`) or `lost` (returns `ExitStatus(-1, error="run lost by the executor")`); past `timeout_seconds` (its own clock — the backend may not enforce one) it calls `cancel` and returns `ExitStatus(137, timed_out=True)`; an `ExecutorUnavailable` from `status()` is retried until the deadline, like `wait`'s "timed out" slice. It is NOT on the ABC: only blocking callers (scans; the interim `execute_run`) use it.
  ```python
  # docker_executor.py
  TIMEOUT_LABEL = "stac-higher.timeout-seconds"
  STOP_GRACE_SECONDS = 10
  class DockerExecutor(Executor):
      submitted_phase = "starting"
      def submit(self, spec: RunSpec) -> RunHandle: ...   # = today's launch + the timeout label
      def status(self, handle: RunHandle) -> RunState: ...
      def cancel(self, handle: RunHandle) -> None: ...
      _clock: Callable[[], dt.datetime]                   # field(default=utcnow, repr=False) — test seam
  ```
  ```python
  # memory_executor.py
  @dataclass
  class MemoryExecutor(Executor):
      name: str = "memory"
      submitted_phase: str = "starting"
      #: per run id, the states status() returns in order (the last repeats); default [exited 0]
      states: dict[str, list[RunState]] = field(default_factory=dict)
      default_states: list[RunState] = field(default_factory=lambda: [RunState("exited", exit=ExitStatus(0))])
      submit_error: Exception | None = None
      status_error: Exception | None = None
      log_output: bytes = b""
      submitted: list[RunSpec]; cancelled: list[str]; reaped: list[str]; status_calls: list[str]
  ```
  `results=` and `launch_error=` are renamed `default_states=`/`submit_error=`; every test constructing a `MemoryExecutor` is updated (grep `MemoryExecutor(`).

- [ ] **Step 1: Failing tests.**
  - `test_executor_contract.py`: `test_wait_is_gone_from_the_interface` (`not hasattr(Executor, "wait")`; the abstract set equals `{submit, status, cancel, logs, reap, list_launched}`); `test_watch_is_optional` (a minimal subclass has `supports_watch is False` and its `watch()` raises `NotImplementedError`); `test_run_to_exit_returns_the_exit` (states starting → running → exited 3 ⇒ `ExitStatus(3)`); `test_run_to_exit_cancels_on_its_own_deadline` (fake clock, a run that stays `running` ⇒ `cancel` called once, `timed_out=True`); `test_run_to_exit_reports_lost`; `test_run_to_exit_rides_out_an_unavailable_backend`.
  - `test_process_executor.py` (Docker, against the file's existing stubbed `_request`): `test_submit_labels_the_timeout`; `test_status_maps_created_running_exited` (`created` → `starting`; `running` → `running`; `exited` + `ExitCode 2` → `exited`, `ExitStatus(2)`); `test_status_of_a_gone_container_is_lost` (inspect 404); `test_status_of_an_unreachable_daemon_raises` (`ExecutorUnavailable`, not `lost`); `test_status_kills_a_run_past_its_deadline` (StartedAt = now − timeout − 1 s ⇒ one `POST …/kill`, `ExitStatus(137, timed_out=True)`); `test_an_exited_run_past_its_deadline_is_timed_out_after_a_restart` (exited 137, `FinishedAt − StartedAt ≥ timeout` ⇒ `timed_out=True`; exited 1 inside the budget ⇒ `False`); `test_status_names_an_oom_kill` (`OOMKilled: true` ⇒ `error` mentions the memory limit); `test_cancel_stops_with_a_grace_and_is_idempotent` (`POST …/stop?t=10`; a 304 and a 404 both return quietly). The two `wait` tests are deleted.
  - `test_image_scan_launch.py`: the existing scan tests pass with `MemoryExecutor(default_states=…)`; add `test_a_scan_past_the_policy_timeout_is_cancelled_and_reaped`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_executor_contract.py tests/test_process_executor.py tests/test_image_scan_launch.py -q`.

- [ ] **Step 3: Implement.** `executor.py` per the Interfaces (the docstring keeps every ADR 0013 bullet and replaces the `wait` bullet with "the backend enforces the timeout: server-side (Kubernetes `activeDeadlineSeconds`) or on read (Docker, `status()`)"). `DockerExecutor.status`: `GET /containers/{id}/json`; map `State.Status` (`created` → `starting`; `running`/`paused`/`restarting` → `running`; `removing`/`exited`/`dead` → `exited`); parse `StartedAt`/`FinishedAt` (RFC 3339 with nanoseconds — truncate the fraction to 6 digits before `datetime.fromisoformat`; Docker's zero time `0001-01-01T00:00:00Z` means unset); read the timeout from `Config.Labels[TIMEOUT_LABEL]` (absent — a container submitted before K-4 — means no deadline); `State.Error` non-empty on an exited container → `ExitStatus.error`. `cancel`: `POST /containers/{id}/stop?t=10`, treating `EngineHTTPError` with status 304 or 404 as done. `submit` is `launch` renamed with `labels[TIMEOUT_LABEL] = str(spec.timeout_seconds)`. Delete `wait`, `poll_slice_seconds` and `SIGKILL_EXIT_CODE`'s `wait` reference (keep the constant for the deadline kill).

- [ ] **Step 4: Interim callers.** `launch.execute_run` and `scan_launch.execute_scan` call `executor.submit(spec)` and `run_to_exit(executor, handle, timeout)`, then `logs` and the `finally: reap` exactly as today. Comment on `execute_run`: "interim (K-4 Task 3) — Task 5 replaces it with submit-and-return".

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-4: executor contract — submit/status/cancel, optional watch, wait removed; Docker enforces the timeout on read via a label; scans poll status"
  ```

### Task 4: Credentials outlive the queue wait (spec §5.4, R7, R8)

**Files:**
- Modify: `services/pipeline/src/pipeline/process/credentials.py` (`mint_prefix_credentials`, `mint_run_credentials`, `RunCredentials`, both refusal messages)
- Modify: `services/pipeline/src/pipeline/process/launch.py` (`execute_run` passes the profile's wait; Task 5 moves this into `submit_run`)
- Modify: `tests/contract-fixtures/process-credential-budget.json` ("one over" → `"mint_allowed": false`; `$comment` updated)
- Test: `services/pipeline/tests/test_contract_fixtures.py`, `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_process_executor.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class RunCredentials:            # existing, gains:
      expires_at: dt.datetime | None = None   # lower bound: clock before the STS call + duration

  def mint_prefix_credentials(settings, *, session_name, prefix, timeout_seconds,
                              sts_client=None, read_prefixes=(), queue_wait_seconds: int = 0,
                              now: dt.datetime | None = None) -> RunCredentials
  def mint_run_credentials(settings, run_id, timeout_seconds, *, queue_wait_seconds: int = 0,
                           sts_client=None, read_prefixes=(), now: dt.datetime | None = None) -> RunCredentials
  ```
  `CredentialBudgetExceeded` and `CredentialDurationRejected` gain `queue_wait_seconds` (keyword, default 0) and their messages read `… would need {n} s (max_queue_wait {w} s + timeout {t} s + grace {g} s, STS minimum 900 s) …` — still starting with `credential_budget_exceeded`. `as_env()` is unchanged (the run never sees its expiry as a variable — it is not part of the ADR 0013 env contract).

- [ ] **Step 1: Failing tests.**
  - `test_contract_fixtures.py`: `test_process_credential_budget_mint_cases` passes `queue_wait_seconds=case["max_queue_wait_seconds"]` (delete the "until K-4" comment); new `test_process_credential_budget_deploy_and_mint_agree` — `case["deploy_allowed"] == case["mint_allowed"]` for every case.
  - `contract-fixtures.test.ts`: the same agreement, reading `mint_allowed` (extend the fixture's local type).
  - `test_process_executor.py`: `test_mint_adds_the_queue_wait_to_the_duration` (wait 600, timeout 900, grace 300 ⇒ the stubbed STS sees `DurationSeconds=1800`); `test_mint_reports_a_lower_bound_expiry` (`now=T` ⇒ `expires_at == T + 1800 s`); `test_a_scan_mints_without_a_queue_wait` (`execute_scan` through a recording STS stub ⇒ `DurationSeconds == max(900, scan_timeout + grace)`); `test_the_refusal_names_the_queue_wait`.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** `duration = max(MIN_DURATION_SECONDS, queue_wait_seconds + timeout_seconds + grace)`; `minted_at = now or dt.datetime.now(dt.UTC)` read before `assume_role`; `expires_at = minted_at + timedelta(seconds=duration)`. Negative `queue_wait_seconds` raises `ValueError` (the profile parser already refuses one). `execute_run` passes `queue_wait_seconds=profile.max_queue_wait_seconds if profile else 0`. Flip the fixture case and update its `$comment` ("K-4: the mint counts the queue wait, so deploy and mint agree on every case").

- [ ] **Step 4: Gates and commit.** `npm run verify`; pytest; ruff.
  ```bash
  git commit -m "K-4: run credentials cover max_queue_wait + timeout + grace; expiry recorded; budget fixture's mint and deploy now agree"
  ```

### Task 5: The Reconciler; the claim path submits and returns; the tick reconciles

**Files:**
- Create: `services/pipeline/src/pipeline/process/reconcile.py`
- Modify: `services/pipeline/src/pipeline/process/launch.py` (`execute_run` and `RunOutcome` deleted; `submit_run`, `SubmittedHandle`, `collect_run_log` added)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (submit → `record_submission` → return; the post-launch outcome block deleted; docstrings)
- Modify: `services/pipeline/src/pipeline/jobs/process.py` (`_execute_claimed` no longer enqueues finalize; `build_reconciler`; the tick sweeps; `fail_extract_batch` typed on a protocol)
- Modify: `services/pipeline/src/pipeline/metrics.py` (one counter)
- Test: `services/pipeline/tests/test_process_reconcile.py` (new), `tests/test_process_triggers.py`, `tests/test_process_capacity.py`, `tests/test_process_executor.py` (the `execute_run` tests become `submit_run`/`collect_run_log` tests), `tests/test_main_jobs.py` (registration unchanged — assert it)

**Interfaces:**
- Consumes: Task 2's repo methods and `SubmittedRun`/`CollectingRun`; Task 3's `Executor`, `RunState`, `RunEvent`, `RESYNC`; Task 4's `RunCredentials.expires_at`.
- Produces:
  ```python
  # launch.py
  @dataclass(frozen=True)
  class SubmittedHandle:
      handle: RunHandle
      credentials_expire_at: dt.datetime | None
      credentials_prefix: str

  def submit_run(executor, settings, *, run_id, process_id, runtime, code, env_entries,
                 resolve_secret, sts_client=None, read_prefixes=(), extra_env=None,
                 profile=None, priority="triggered", image=None) -> SubmittedHandle
  def collect_run_log(executor, settings, storage_client, *, handle: RunHandle,
                      process_id: str, run_id: str) -> str | None     # logs → store_run_log; never raises for a lost log

  # reconcile.py
  class RunBatch(Protocol):            # what on_dead / fail_extract_batch read
      id: str
      input_items: list[dict[str, Any]]

  OUTCOMES = ("unchanged", "phase", "succeeded", "failed", "dead", "cancelled",
              "requeued", "merged", "skipped", "unavailable")

  @dataclass
  class Reconciler:
      repo: ProcessRepo
      executor: Executor
      settings: Settings
      storage_client: Any
      enqueue_finalize: Callable[[str, str], Awaitable[None]]     # (run_id, process_id)
      enqueue_run_now: Callable[[str], Awaitable[None]]           # (run_id)
      on_dead: Callable[[RunBatch, str], Awaitable[None]] | None = None
      clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)

      async def reconcile(self, run: SubmittedRun) -> str            # one of OUTCOMES
      async def reconcile_id(self, run_id: str) -> str | None        # None: not a submitted run of this backend
      async def sweep(self, *, limit: int, cancel_requested_only: bool = False) -> int   # rows reconciled
      async def on_event(self, event: RunEvent) -> None              # RESYNC → sweep; image_scan → ignored
  ```
  ```python
  # jobs/process.py
  RECONCILE_BATCH = 50
  def build_reconciler(queue: QueueBackend, settings: Settings, *,
                       executor: Executor | None = None) -> Reconciler   # DockerExecutor when None
  ```
  `runner.RunResult` gains `cancel_requested: bool = False`; a submitted run returns `status="running"`.
  Metric: `PROCESS_RECONCILES = Counter("pipeline_process_reconciles_total", "…", ["outcome"])`.

- [ ] **Step 1: Failing tests** (`tests/test_process_reconcile.py`, `FakeProcessRepo` + `MemoryExecutor`, a fixed clock, recording `enqueue_finalize`/`enqueue_run_now`/`on_dead`). One test per row of the table in Step 3, named after it, plus:
  - `test_a_restart_mid_collection_still_finalizes_once` — a first `reconcile` whose `collect_run_log` raises after the log object is written: row still `running`, nothing reaped, no finalize; a second `reconcile` writes `succeeded`, stores the log once more at the same key, reaps once, enqueues finalize once.
  - `test_cancel_wins_over_a_concurrent_exit` — `cancel_requested_at` set and `status()` already `exited 0`: one `cancel` call, row `cancelled`, error `"cancelled by an operator; the run had already exited 0"`, no finalize.
  - `test_the_watcher_and_the_tick_racing_collect_once` — `asyncio.gather(rec.reconcile(run), rec.reconcile(run))`: outcomes `{"succeeded", "skipped"}`, one finalize enqueue, one reap.
  - `test_a_row_of_another_backend_is_skipped` — `backend="kubernetes"` row: `reconcile_id` returns `None`, no executor call.
  - `test_an_image_scan_event_is_ignored` and `test_resync_sweeps_every_submitted_run`.
  - `test_run_one_submits_and_returns` (in `test_process_triggers.py`) — `run_one` returns `RunResult(status="running")`, `record_submission` was called with `phase=executor.submitted_phase`, the handle, `backend="memory"` and the credentials' `expires_at`; nothing reaped; no log stored.
  - `test_a_lost_claim_cancels_what_it_submitted` — `record_submission` returns `None` ⇒ `cancel` then `reap` on that handle, `RunResult(status="skipped")`.
  - `test_a_cancel_requested_before_submission_is_reported` — `SubmissionRecord(cancel_requested=True)` ⇒ `RunResult.cancel_requested is True`; `_execute_claimed` then calls `reconcile_id` (assert through a stub reconciler).

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_process_reconcile.py tests/test_process_triggers.py -q`.

- [ ] **Step 3: `Reconciler.reconcile` — the decision table.** Every executor call is `await asyncio.to_thread(...)`. `ExecutorUnavailable` anywhere before the collecting block ⇒ return `"unavailable"` (the tick retries). Order:

  | # | Row / backend state | Action | Outcome |
  |---|---|---|---|
  | 1 | `cancel_requested_at` set | `before = status()` (an `ExecutorUnavailable` here is ignored); `cancel(H)`; then `collecting(H)`: `collect_run_log` → `finish(status="cancelled", error=f"the run had already exited {code}" if before was exited else None)` — the repo's I10 rewrite prefixes `cancelled by an operator`; after commit `reap(H)` | `cancelled` |
  | 2 | `status()` = `pending_capacity`/`starting`/`running`, credentials fine | `set_phase(H, phase, state.detail)` | `phase` or `unchanged` |
  | 3 | `status()` = `pending_capacity` and `credentials_expire_at − now < timeout + grace` (timeout from `parse_process_runtime(run.runtime).timeout_seconds`, grace from settings) | `cancel(H)`; `collecting(H)` → `requeue(error=None, phase="pending_capacity", phase_detail=CREDENTIAL_REQUEUE_DETAIL, next_attempt_at=now)` (the constant is the Global Constraints text, defined in `reconcile.py`); after commit `reap(H)` and `enqueue_run_now(id)` | `requeued`/`merged`/`cancelled` |
  | 4 | `status()` = `exited` | `collecting(H)`: `collect_run_log`; `outcome_transition(exit_code, timed_out, attempts=run.attempts, max_attempts=runtime.max_attempts, now, retry_wait_seconds=60, error=exit.error)`; `finish(...)`; after commit `reap(H)`; if written `succeeded` → `enqueue_finalize(id, process_id)`; if written `dead` and `association_id` → `on_dead(run, error)`; observe `PROCESS_RUN_SECONDS` = now − `submitted_at` | the written status |
  | 5 | `status()` = `lost` | `collecting(H)` → `requeue(error="the executor lost the run (its container is gone)", phase=None, phase_detail=None, next_attempt_at=now + 60 s)`; after commit `reap(H)` (idempotent); WARNING log | `requeued`/`merged`/`cancelled` |
  | 6 | `collecting` yields `None` | nothing (another observer owns it, or it is already terminal) | `skipped` |

  Row 1 is checked before rows 2–5 so a cancel is never mistaken for an ordinary exit; if `cancel` raises `ExecutorUnavailable`, return `"unavailable"` without collecting (the container may still be running). An unparseable stored runtime (cannot happen after a successful claim, but) ⇒ treat `max_attempts=1`, `timeout_seconds=900` and log a WARNING rather than strand the row. `sweep` = `list_submitted_runs(backend=executor.name, limit=…, cancel_requested_only=…)` then `reconcile` each, isolating per-row exceptions (logged with `run_id`, the `_execute_claimed` precedent). `on_event(RESYNC)` = `sweep(limit=RECONCILE_BATCH)`; a `kind != "process"` event is dropped; otherwise `reconcile_id(event.run_id)`. Every outcome increments `PROCESS_RECONCILES`.

- [ ] **Step 4: The claim path.** `run_one` after staging: `submitted = await asyncio.to_thread(submit_run, …)` (the same exception mapping as today's `execute_run` block — `ImagePullFailed`/`ImageMissing` spend an attempt, `HardwareUnavailable`/`CredentialBudgetExceeded`/`SecretResolutionError` dead, `ExecutorUnavailable`/`RunCredentialsError` → `requeue_without_attempt`); then `record = await repo.record_submission(run.id, claimed_at=run.claimed_at, handle=submitted.handle.id, backend=executor.name, phase=executor.submitted_phase, submitted_at=at, credentials_expire_at=submitted.credentials_expire_at)`; `None` ⇒ `cancel` + `reap` in a thread, WARNING `"submitted run lost its claim; container removed"`, `RunResult(run.id, "skipped")`; else `RunResult(run.id, "running", cancel_requested=record.cancel_requested)`. This also stops `run_one` blocking the event loop (today `execute_run` runs on it). `_execute_claimed` builds one `Reconciler` per batch (via `build_reconciler`), drops its finalize collection, and calls `reconciler.reconcile_id(result.run_id)` for any `cancel_requested` result. `process_run_tick` keeps claiming, then `await reconciler.sweep(limit=RECONCILE_BATCH)`. `fail_extract_batch(ingest_repo, run: RunBatch, error)`.

- [ ] **Step 5: Gates and commit.** pytest (all of it — `run_one`'s callers moved); ruff.
  ```bash
  git commit -m "K-4: the Reconciler — status() decides, verdicts collected once under the row lock; run_one submits and returns; the run tick reconciles submitted runs; credential-expiry requeue"
  ```

### Task 6: The run watcher (Docker events) and the cancel listener

**Files:**
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py` (`watch()` and two pure helpers)
- Create: `services/pipeline/src/pipeline/process/watcher.py` (`run_watcher`)
- Modify: `services/pipeline/src/pipeline/dispatcher/listener.py` (log line wording only: `"notify listener connected"` / `"notify listener failed; reconnecting"`, `channel` stays in `extra`)
- Modify: `services/pipeline/src/pipeline/jobs/process.py` (`build_run_watcher`, `build_cancel_listener`)
- Modify: `services/pipeline/src/pipeline/main.py` (two more coroutines in `run_until_first_exit`)
- Modify: `docker-compose.yml` (`docker-socket-proxy`: `EVENTS=1`, `PING=0`, `VERSION=0`, comments)
- Test: `tests/test_process_executor.py` (events parsing), new `tests/test_process_watcher.py`, `tests/test_compose_scanner.py`, `tests/test_main_jobs.py`, `tests/test_dispatch_listener.py` (log text, if asserted)

**Interfaces:**
- Consumes: Task 3's `RunEvent`/`RESYNC`; Task 5's `Reconciler.on_event` and `Reconciler.sweep`; `run_dispatch_listener(database_url, on_wake, *, channel=…)`; ledger `CANCEL_CHANNEL`.
- Produces:
  ```python
  # docker_executor.py
  EVENT_FILTERS = {"type": ["container"], "label": [RUN_ID_LABEL],
                   "event": ["start", "die", "oom", "destroy"]}
  async def watch(self) -> AsyncIterator[RunEvent]          # DockerExecutor; supports_watch is True
  def _parse_event(obj: object) -> RunEvent | None          # module-level, pure
  async def _iter_body_lines(reader: asyncio.StreamReader, *, chunked: bool) -> AsyncIterator[bytes]

  # watcher.py
  WATCHER_RECONNECT_SECONDS = 5.0
  async def run_watcher(watch: Callable[[], AsyncIterator[RunEvent]],
                        on_event: Callable[[RunEvent], Awaitable[None]], *,
                        reconnect_delay: float = WATCHER_RECONNECT_SECONDS,
                        max_connects: int | None = None) -> None

  # jobs/process.py
  def build_run_watcher(queue: QueueBackend, settings: Settings) -> Coroutine[Any, Any, None]
  def build_cancel_listener(queue: QueueBackend, settings: Settings) -> Coroutine[Any, Any, None]
  ```

- [ ] **Step 1: Failing tests.**
  - Events (`test_process_executor.py`): `test_parse_event_reads_the_run_id_handle_and_kind` (a `die` object with `Actor.ID` and `Actor.Attributes` carrying `stac-higher.run-id`/`stac-higher.run-kind` ⇒ `RunEvent(run_id, handle_id, kind)`; a missing kind label ⇒ `"process"`); `test_parse_event_ignores_strangers_and_garbage` (no run-id attribute, a non-container `Type`, a non-dict ⇒ `None`); `test_body_lines_decode_chunked_framing` (feed a `StreamReader` `b"5a\r\n{…}\n\r\n0\r\n\r\n"`-style chunks, including one JSON object split across two chunks and two objects in one chunk ⇒ the right lines); `test_body_lines_read_an_identity_body_to_eof`; `test_watch_yields_resync_first_then_events` (a local `asyncio.start_server` on 127.0.0.1 answering `HTTP/1.1 200 OK` + `Transfer-Encoding: chunked` with two events, with the executor's `docker_host` pointed at it ⇒ `[RESYNC, RunEvent(...), RunEvent(...)]`, the request line names `/v1.43/events` and URL-encodes `EVENT_FILTERS`); `test_watch_raises_unavailable_on_a_refused_status` (403 from the proxy ⇒ `ExecutorUnavailable` naming the status).
  - Watcher (`test_process_watcher.py`, the `test_dispatch_listener.py` shape): `test_each_connect_delivers_resync_then_events`; `test_a_failing_event_handler_does_not_drop_the_stream` (the handler raises on event 1; event 2 is still delivered; the error is logged with `run_id`); `test_a_dropped_stream_reconnects_after_the_delay` (two factory calls with `max_connects=2`, `reconnect_delay=0`); `test_cancellation_propagates`.
  - Compose (`test_compose_scanner.py`, replacing K-3's set test): `test_the_socket_proxy_enables_exactly_the_known_sections` — the explicit `=1` set is `{"CONTAINERS", "POST", "IMAGES", "INFO", "EVENTS"}`; new `test_every_image_default_on_section_is_listed` — each of `EVENTS`, `PING`, `VERSION` (the `0.3.0` image's defaults) appears explicitly, `PING=0` and `VERSION=0`.
  - Main (`test_main_jobs.py`): `test_run_passes_the_watcher_and_the_cancel_listener` — monkeypatch `run_until_first_exit` to capture its coroutines (close them unawaited) and assert their `__qualname__`s include the two new ones; the existing `run_until_first_exit` stop tests already cover cancellation of any extra sibling.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: `DockerExecutor.watch`.** `asyncio.open_connection(host, port)` from the parsed `docker_host`; send `GET /v1.43/events?filters=<quote(json.dumps(EVENT_FILTERS))> HTTP/1.1\r\nHost: docker\r\nAccept: application/json\r\nConnection: close\r\n\r\n`; read the status line (`!= 200` ⇒ `ExecutorUnavailable(f"docker GET /events failed: {status}")`) and headers (`Transfer-Encoding: chunked` ⇒ chunked); `yield RESYNC`; then for each non-empty line `json.loads` → `_parse_event` → yield non-`None`. Connection errors ⇒ `ExecutorUnavailable`. `finally`: close the writer. No `since` (Global Constraints). No thread, no new dependency.

- [ ] **Step 4: `run_watcher`** — the `run_dispatch_listener` loop with two differences: `on_event` exceptions are caught and logged **per event** (`"run watcher could not apply an event"`, `extra={"run_id": …}`) so one bad row never tears down the stream, and the stream itself failing is logged (`"run watcher stream ended; reconnecting"`) and retried after the delay. `CancelledError` always propagates.

- [ ] **Step 5: Wiring.** `build_run_watcher`: one long-lived `DockerExecutor(docker_host=settings.docker_host)` and `Reconciler` (`build_reconciler` from Task 5, with `enqueue_finalize = queue.enqueue(JOB_FINALIZE, …)`, `enqueue_run_now = queue.enqueue(JOB_RUN_NOW, …)`); returns `run_watcher(executor.watch, reconciler.on_event)` when `executor.supports_watch`, else a coroutine that waits forever (the tick is the only path — K-5's watch may land later than its executor). `build_cancel_listener`: `run_dispatch_listener(settings.database_url, lambda: reconciler.sweep(limit=RECONCILE_BATCH, cancel_requested_only=True), channel=CANCEL_CHANNEL)` — its connect wake drains cancels requested while the worker was down. `main.run` passes both to `run_until_first_exit` after the dispatch listener. The issue's uvicorn note needs nothing more: #39 made `run_until_first_exit` stop every sibling on the first exit; the two new coroutines run until cancelled exactly like the dispatch listener, and a collection cancelled mid-flight rolls back (Task 2).

- [ ] **Step 6: Compose.** In `docker-socket-proxy`'s environment: `- EVENTS=1` with a comment ("K-4: the run watcher's `GET /events` stream, filtered to our run-id label; read-only. Already on by the image default — written here so the boundary is visible"), and `- PING=0`, `- VERSION=0` ("on by the image default, used by nothing here"). Update the header comment's "Least privilege:" line to list events.

- [ ] **Step 7: Gates and commit.**
  ```bash
  git commit -m "K-4: the run watcher — Docker /events (run-id label) wakes the Reconciler, RESYNC on every connect; LISTEN process_run_cancel; socket proxy EVENTS explicit, PING/VERSION off"
  ```

### Task 7: The cancel verb — storage, route, gate, re-run of a cancelled run

**Files:**
- Modify: `app/src/lib/processes/storage.ts` (`RUN_COLUMNS` + `cancel_requested_at`; `ApiProcessRun.cancel_requested_at`; `cancelRun`; `rerunRun` accepts `cancelled` and clears the executor columns)
- Create: `app/src/pages/api/processes/[id]/runs/[runId]/cancel.ts`
- Modify: `app/src/pages/api/processes/[id]/runs/[runId]/rerun.ts` (409 text: "Only dead or cancelled runs can be re-run")
- Modify: `app/src/lib/authz/permissions.ts` (`GatedAction` + `"cancel"`; one `SUB_ACTION_ROUTES` entry)
- Modify: `app/src/lib/processes/api.ts`, `app/src/lib/processes/queries.ts` (`cancelRun`, `useCancelRun`)
- Test: `app/src/__tests__/api-processes.test.ts`, `app/src/__tests__/authz-processes-gate.test.ts`, `app/src/__tests__/processes-storage.test.ts`, `app/src/__tests__/processes-run-phase.test.ts` (the new field in `toApiRun`)

**Interfaces:**
- Consumes: Task 1's `ProcessRunStatus`, `CANCELLABLE_RUN_STATUSES`, `PROCESS_RUN_CANCEL_CHANNEL`.
- Produces:
  ```ts
  export interface ApiProcessRun { …; status: ProcessRunStatus; cancel_requested_at: string | null }
  export type CancelOutcome = "cancelled" | "cancel_requested";
  export async function cancelRun(processId: string, runId: string):
    Promise<{ run: ApiProcessRun; outcome: CancelOutcome } | null>;   // null: unknown or not cancellable
  // api.ts
  export async function cancelRun(id: string, runId: string): Promise<{ run: ProcessRun; outcome: CancelOutcome }>;
  // queries.ts
  export function useCancelRun(); // useProcessMutation(({ id, runId }) => cancelRun(id, runId))
  ```

- [ ] **Step 1: Failing tests.**
  - `authz-processes-gate.test.ts`: `matchGatedRoute("POST", \`/api/processes/${P}/runs/${R}/cancel\`)` equals `{ action: "cancel", resourceType: "process", resourceId: P }`; a member identity is refused by the guard (the file's existing gate pattern).
  - `api-processes.test.ts` (mock `cancelRun` beside `rerunRun`): member → 403 and `cancelRun` not called; operator → 202 with `{ run, outcome: "cancel_requested" }` and `cancelRun(PROCESS_ID, RUN_ID)`; `cancelRun` → `null` with `getRun` → `succeeded` ⇒ 409 `{ code: "run_not_cancellable" }` and an error naming `'succeeded'`; `getRun` → `null` ⇒ 404; a non-UUID run id ⇒ 404; a database error ⇒ 500 `{ error: "internal error" }` with no raw text (#74); an extractor process is cancellable (202, R17).
  - `processes-storage.test.ts`: `cancelRun` sends ONE statement whose SQL contains `status IN ('queued','failed','running')`, `FOR UPDATE`, `coalesce(r.cancel_requested_at, now())` and `pg_notify('process_run_cancel', '')`, with params `[runId, processId]`; maps a returned row whose `prior_status` is `running` to `outcome: "cancel_requested"` and `queued` to `"cancelled"`; returns `null` on no row. `rerunRun`'s SQL now has `status IN ('dead','cancelled')` and sets `executor_handle = NULL, executor_backend = NULL, submitted_at = NULL, cancel_requested_at = NULL, credentials_expire_at = NULL`.
  - `processes-run-phase.test.ts`: `toApiRun` passes `cancel_requested_at` through as ISO (or `null`).

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/__tests__/api-processes.test.ts src/__tests__/authz-processes-gate.test.ts src/__tests__/processes-storage.test.ts src/__tests__/processes-run-phase.test.ts`.

- [ ] **Step 3: The gate.** `GatedAction` gains `"cancel"`; after the `rerun` entry:
  ```ts
  // K-4 (spec §5.3, closes I-81): cancel a queued, retrying or running run.
  // Audited against the PROCESS; the run id stays in the audited path.
  { pattern: /^\/api\/processes\/([^/]+)\/runs\/[^/]+\/cancel$/, action: "cancel", resourceType: "process" },
  ```

- [ ] **Step 4: `cancelRun` — one statement (I11, I12).** `prior_status` comes from the locked row, so a claim that commits first is seen as `running` (READ COMMITTED re-checks a `FOR UPDATE` row against its newest version):
  ```sql
  WITH target AS (
    SELECT id, status AS prior_status FROM stac_higher.process_runs
     WHERE id = $1 AND process_id = $2 AND status IN ('queued','failed','running')
     FOR UPDATE
  ), upd AS (
    UPDATE stac_higher.process_runs r
       SET cancel_requested_at = coalesce(r.cancel_requested_at, now()),
           status      = CASE WHEN t.prior_status = 'running' THEN r.status ELSE 'cancelled' END,
           finished_at = CASE WHEN t.prior_status = 'running' THEN r.finished_at ELSE now() END,
           phase       = CASE WHEN t.prior_status = 'running' THEN r.phase ELSE NULL END,
           phase_detail = CASE WHEN t.prior_status = 'running' THEN r.phase_detail ELSE NULL END,
           next_attempt_at = CASE WHEN t.prior_status = 'running' THEN r.next_attempt_at ELSE NULL END,
           rate_deferred_until = CASE WHEN t.prior_status = 'running' THEN r.rate_deferred_until ELSE NULL END,
           error = CASE WHEN t.prior_status = 'running' THEN r.error ELSE 'cancelled by an operator' END
      FROM target t WHERE r.id = t.id
    RETURNING r.*, t.prior_status
  )
  SELECT ${RUN_COLUMNS}, prior_status, pg_notify('process_run_cancel', '') FROM upd
  ```
  (Write the column list against `upd` explicitly; `RUN_COLUMNS` is a bare list.) The notify is delivered at commit, payload-less (the row is the truth, ADR 0007). The SQL is copied into Task 2's integration test as a constant — keep the two in sync (a comment on each names the other).

- [ ] **Step 5: The route.** `cancel.ts`, the `rerun.ts` shape: `loadVisibleProcess(locals.auth, params.id, true)`; `isUuid(params.runId)` else 404; `cancelRun` → 202 `{ run, outcome }`; `null` ⇒ `getRun` → 404 when absent, else 409 `{ error: \`Only queued, retrying or running runs can be cancelled; this one is '${status}'\`, code: "run_not_cancellable" }`; errors → `console.error(message)` + 500 `{ error: "internal error" }`. File header documents the two outcomes and that the app never touches the executor (ADR 0004): a running run is stopped by the pipeline's watcher within seconds of the NOTIFY, or by the next run tick.

- [ ] **Step 6: Client + docs row.** `api.ts` `cancelRun` (POST, the `rerunRun` shape); `queries.ts` `useCancelRun`. `docs/backend.md` route table: a row for `/api/processes/[id]/runs/[runId]/cancel` (operator+, audited `cancel`, the outcomes, 409/404) and the `rerun` row's "Only dead rows" → "dead or cancelled rows".

- [ ] **Step 7: Gates and commit.** `npm run verify`.
  ```bash
  git commit -m "K-4: POST /api/processes/[id]/runs/[runId]/cancel — operator+, audited cancel; queued/retrying runs cancel at once, running runs are flagged and NOTIFY the pipeline; cancelled runs can be re-run"
  ```

### Task 8: The run row — Cancel button, `cancelled` badge, *Cancelling…* and the submitted-wait chip

**Files:**
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` (`RUN_STATUS_VARIANT`, `RunRow`, a `CancelRunButton` local to the file)
- Test: `app/src/__tests__/process-run-row-hardware.test.tsx` (mock gains `useCancelRun`; `baseRun` gains `cancel_requested_at: null`), new `app/src/__tests__/process-run-cancel.test.tsx`, `app/src/__tests__/processes-health.test.ts`

**Interfaces:**
- Consumes: Task 7's `useCancelRun`, `ApiProcessRun.cancel_requested_at`, `CANCELLABLE_RUN_STATUSES`.
- Produces (selectors the e2e and the lead's check rely on): `data-testid="run-cancel"` (the row button), `data-testid="run-cancel-confirm"` (the dialog's confirm button), `data-testid="run-status-cancelled"` (the badge), `data-testid="run-phase-cancelling"`, `data-testid="run-phase-pending_capacity"` (existing, now also on running rows).

- [ ] **Step 1: Failing tests** (`process-run-cancel.test.tsx`, the hardware test's mocking pattern: `vi.hoisted` state, `vi.mock("@/lib/processes/queries", …)` with `useRuns`, `useRerunRun`, `useCancelRun`, `useHardwareProfiles`):
  - `shows Cancel on queued, retrying and running rows for an operator only` — `canMutate` true: three `run-cancel` buttons for `queued`/`failed`/`running` runs, none for `succeeded`/`dead`/`cancelled`; `canMutate` false: none.
  - `asks before cancelling, then calls the verb once` — click `run-cancel` → a dialog titled `Cancel this run?` whose description says a running run is stopped and its log kept, and that a cancelled run can be re-run; click `run-cancel-confirm` → `mutateAsync({ id: "p1", runId })` once; toast `"Run cancelled"` for outcome `cancelled`, `"Cancel requested — stopping the run"` for `cancel_requested`.
  - `marks a running run whose cancel is pending` — `status: "running", cancel_requested_at: "…"` ⇒ chip `Cancelling…` (`run-phase-cancelling`), no Cancel button (already requested), no `Starting` chip.
  - `shows the cancelled badge and offers Re-run` — `status: "cancelled"` ⇒ `run-status-cancelled` with text `cancelled`; a non-extractor shows `Re-run`; an extractor does not.
  - `shows Waiting for capacity on a submitted run too` — `status: "running", phase: "pending_capacity", phase_detail: "…"` ⇒ the chip with that tooltip (K-5's Kueue wait).
  - `processes-health.test.ts`: `a cancelled run is neutral` — a ledger of `succeeded` + `cancelled` ⇒ verdict `Healthy`, `successRateOverRuns` counts 1 (cancelled is not terminal-for-rate).

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/__tests__/process-run-cancel.test.tsx src/__tests__/process-run-row-hardware.test.tsx src/__tests__/processes-health.test.ts`.

- [ ] **Step 3: Implement.**
  - Status badge: `cancelled` keeps `variant="outline"` (R20) with `className="text-muted-foreground line-through"` and the testid.
  - Chips: the pending-capacity condition drops its `status === "queued"` clause (`run.phase === "pending_capacity"`); new `running` + `cancel_requested_at` ⇒ `Cancelling…`, which suppresses `Starting`.
  - `CancelRunButton({ processId, run })`: `Button variant="outline" size="sm"` with lucide `CircleStop`, label `Cancel`; a controlled `Dialog` from `@/components/ui/dialog` (the `ProcessesPage.tsx` import precedent) with `Keep running` / `Cancel run` (destructive, `run-cancel-confirm`, spinner while pending); toast on error with the server's message. Rendered when `canMutate && CANCELLABLE_RUN_STATUSES.includes(run.status) && !run.cancel_requested_at` — extractors included (R17).
  - Re-run shows for `dead` **or** `cancelled` (still not for extractors).
  - `health.ts` needs no code change (it counts only `succeeded`/`dead`/`failed`); add a one-line comment that `cancelled` is deliberately neutral (spec §9).

- [ ] **Step 4: Gates and commit.** `npm run verify`.
  ```bash
  git commit -m "K-4: run rows — Cancel (confirm dialog, audited verb), cancelled badge + Re-run, Cancelling… chip, Waiting for capacity on submitted runs"
  ```

### Task 9: A freed slot wakes the runs waiting for it (I-164's latency half)

Small and separable: if review pushes back, drop it and I-164 stays as K-3 left it.

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py` (`wake_capacity_waiters`, Pg + ABC), `tests/_process_fake.py`
- Modify: `services/pipeline/src/pipeline/process/reconcile.py` (after a commit that moved a run out of `running`)
- Test: `tests/test_process_reconcile.py`, `tests/test_integration_process_reconcile.py`

**Interfaces:**
- Produces:
  ```python
  async def wake_capacity_waiters(self, profile_id: str, *, limit: int) -> list[str]
  ```
  Clears `next_attempt_at` on up to `limit` oldest `queued` runs with `phase = 'pending_capacity'` whose revision's profile (`coalesce(rev.runtime->'hardware'->>'profile', 'standard')`, the `count_running_on_profile` expression) is `profile_id`, and returns their ids. `limit` = 1 (one slot freed per collection).

- [ ] **Step 1: Failing tests** — `test_a_finished_run_wakes_one_capacity_waiter_on_its_profile` (fake: a waiting run on the same profile gets `enqueue_run_now`; one on another profile does not); DB-gated `test_wake_capacity_waiters_clears_the_oldest_on_the_profile`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** In `Reconciler`, after any commit whose outcome left `running` (rows 1, 3, 4, 5), read the run's profile from `parse_process_runtime(run.runtime).hardware_profile`, call `wake_capacity_waiters(profile, limit=1)` and `enqueue_run_now` each id. The woken run's own claim still runs K-3's capacity count, so a wake never oversubscribes a profile.
- [ ] **Step 4: Gates and commit.**
  ```bash
  git commit -m "K-4: a run leaving a profile wakes the oldest run waiting for its capacity (I-164 latency)"
  ```

### Task 10: Docs

**Files:** `docs/FEATURES.md`, `docs/ISSUES.md`, `docs/decisions/0016-ogc-processes-conformance-posture.md`, `docs/processes.md`, `services/pipeline/README.md`, `docs/backend.md` (if Task 7 did not finish it), `tests/contract-fixtures/README.md` (Task 1 did).

- [ ] **FEATURES.md:** a `K-4 · Submit-then-reconcile + cancel` row after K-3: the ABC (`submit/status/cancel/logs/reap/list_launched`, optional `watch`, `wait` gone); verdicts collected once under the row lock; the three wake paths (Docker `/events`, `LISTEN process_run_cancel`, the tick); Docker's timeout on read; migration **031**; the claim fence; the attempt-restoring requeue; the credential-expiry requeue (dormant on Docker); the cancel verb + UI; the `process-run-status.json` fixture; what was live-checked (Task 11).
- [ ] **ISSUES.md:**
  - **I-81** → 🟢 resolved (K-4, migration 031; cancel wins — R3).
  - **I-124** reworded: scans no longer call `wait` but still block one worker slot in a thread via `run_to_exit` (R11); "closes with K-4" → a new follow-up issue (open it: Slice template, `queue: k8s compute (K)`, title "Image scans on the reconcile path (I-124)") and link it.
  - **I-149**: the mint now counts the queue wait; deploy and mint agree on every fixture case; the remaining gap is only a cap or profile change after deploy (R7).
  - **I-164**: latency half resolved by Task 9 (drop this line if Task 9 was dropped); the database-wide count stays.
  - **New entries:** (a) the `process_finalize` enqueue and extractor `on_dead` run after the verdict commits — a crash in that window loses a publish (pre-existing, now stated; fix candidates: a `finalize_enqueued_at` column swept by the tick, or the outbox pattern); (b) a Docker run can overrun its timeout by up to one tick when no event arrives (R10); (c) `process_runs_terminal_age_idx` excludes `cancelled` — the retention leg that will use it must include it; (d) a `lost` run requeues forever if something keeps deleting run containers (R15); (e) the reaper's age ceiling (`MAX_TIMEOUT_SECONDS + 3600`) does not count `max_queue_wait_seconds`, which matters once K-5 holds submitted Jobs for capacity — K-5 must widen it.
- [ ] **ADR 0016:** item 3's status sentence: `dismiss` is now implementable (K-4, `POST …/cancel`); the facade claims it when it ships. Do not rewrite the decision.
- [ ] **processes.md:** in "When does a run start?", replace "A claimed run shows *Starting* until it finishes." with the real phase sequence (*Starting* → running → a verdict) and add a short **"Cancelling a run"** section: who can, what each status does, that a running run is stopped with 10 s to exit (SIGTERM, then SIGKILL) and its log kept, that cancel wins even over a run that just finished, that a cancelled run can be re-run and an extractor's files are retried by the ingest sweep. In "Failure and retry": a run longer than an hour is no longer mistaken for a stranded one; a worker restart no longer touches a running run; the timeout may overrun by up to a minute.
- [ ] **pipeline README:** the executor section: the ABC, R1/R2 in two sentences, the watcher and cancel listener as `main.py` siblings, the socket proxy's `EVENTS`.
- [ ] **Issue #55** (I-122): comment that with K-4 a test run's `finish_check` belongs in the Reconciler's post-commit step (R18).
- [ ] Gates (`npm run verify`) and commit: `git commit -m "K-4: docs — FEATURES row, I-81 resolved, I-124/I-149/I-164 updated, new residuals, ADR 0016 dismiss, cancelling a run"`.

### Task 11: Verify, rebase, PR, merge, live check (lead only)

- [ ] Worktree: `npm run verify`; `uv run pytest -q`; `uv run ruff check .`; the DB-gated `test_integration_process_reconcile.py`, `test_integration_process_capacity.py`, `test_integration_process_requeue.py` against a Postgres at migration 031. Rebase onto `main`; gates again. Push; `gh pr create --base main` titled `K-4: submit-then-reconcile executor, run watcher, cancel verb`, body starting `Closes #12`, listing the gates, migration 031, Rulings R3/R4/R7/R8/R11/R13/R14 as deviations or choices, and the lead-only steps below. Squash-merge when CI is green.
- [ ] Deploy: `docker compose up -d --build pipeline docker-socket-proxy`; migration 031 applies on the app's first API request; `psql`: `\d+ stac_higher.process_runs` shows `process_runs_status_check` with `cancelled` and `credentials_expire_at`. Pipeline log: `notify listener connected` with `channel=process_run_cancel`, and no `run watcher stream ended` loop.
- [ ] **Run lifecycle:** a `standard` test run (or a cron-triggered process) moves `queued → running/Starting → running → succeeded`, `submitted_at` and `executor_handle` set, `phase` `running` while it runs, finalize publishes; `SELECT status, phase, executor_handle, submitted_at, credentials_expire_at - submitted_at FROM stac_higher.process_runs ORDER BY created_at DESC LIMIT 3` (the last ≈ `max_queue_wait + timeout + 300` s).
- [ ] **Worker restart mid-run (spec §2 gate):** deploy a revision that sleeps 120 s then publishes; trigger; while `running`, `docker compose restart pipeline`; the run container keeps running (`docker ps --filter label=stac-higher.run-id`); after it exits the row goes `succeeded` exactly once (one log object, one finalize — `output_items` once, `flow_stats.runs` +1), the container is reaped. Repeat with `docker compose stop pipeline` across the run's exit, then `start`: the RESYNC collects it within seconds.
- [ ] **Cancel a running run:** the same sleeping revision; click Cancel → confirm; the row shows *Cancelling…*, then `cancelled` within ~15 s; the container is gone; `audit_log` has one `cancel` row on resource `process` naming the run path; the Log link serves the partial log. Cancel a **queued** run (a rate-deferred one, or one behind `cpu-large` capacity 1): `cancelled` at once. Re-run the cancelled one: it executes.
- [ ] **Timeout on read:** a revision with `timeout_seconds: 60` that sleeps 300 s ends `failed`/`dead` with "run exceeded its timeout" within ~2 min.
- [ ] **Capacity (with Task 9):** two runs on `cpu-large`: the second shows *Waiting for capacity* and starts within seconds of the first finishing, not at the next tick.
- [ ] **Image scan:** add an image in the UI; the admission scan finishes as before (`approved`/`flagged`), its container reaped, no watcher errors for `run-kind=image_scan`.
- [ ] **Standing GOES demo:** `uv run python -m pipeline.demo status` from `services/pipeline` shows the loop healthy; the extractor and GeoColor runs keep succeeding across the deploy and the restart test; no new `process_failed` alerts.
- [ ] **Silo STS duration:** a run's credentials now last `max_queue_wait` longer; confirm a `standard` run mints (no `CredentialDurationRejected`) — if Silo caps `DurationSeconds` below `1800 + timeout + 300` for the demo's revisions, set `PROCESS_MAX_CREDENTIAL_SECONDS` accordingly and record it.
- [ ] e2e: `processes` spec, then the whole suite (`npm run test:e2e:ci` from `app/`); no existing spec asserts a run row's status chips or `Starting`, so no selector edits are expected — if `goes-loop` (gated, `E2E_LIVE_NODD=1`) is run, it exercises the whole reconcile path end to end. Optionally add `app/e2e/process-cancel.spec.ts` (an operator cancels a queued run via the API-created fixture and sees `run-status-cancelled`) if the suite has a cheap way to create a queued run without the pipeline claiming it (a rate-deferred run) — otherwise record the UI check above as the evidence.
- [ ] `git worktree remove .claude/worktrees/k4-submit-reconcile`.

## Self-review

- **Spec coverage.** §5.1 ABC → T3 (`watch` T6). §5.2 `cancelled` + migration → T1 (as 031, not 029, per the numbering on #12); `TERMINAL_STATUSES` → T1; `submitted_at`/`executor_handle`/`cancel_requested_at` written → T2/T5. §5.3 flow: submit-and-return → T5; watcher → T6; run tick reconcile → T5; credential-expiry requeue → T5 (rule), T4 (mint); cancel verb → T7, UI → T8, `dismiss` → T10. §5.4 → T4/T5 (R7–R9). §8 Docker `submit/status/cancel/watch` → T3/T6. §9 Cancel button, `cancelled` badge, health neutral → T8; test-run phases deferred (R18). §13 decisions 6–8 honoured. Issue extras: socket proxy + compose test → T6; uvicorn note → T6 Step 5 (already resolved by #39); image scans → T3 (R11); docs → T10; worker-restart verification → T11.
- **Type consistency.** `QueuedRun.claimed_at` (T2) used by T5's `record_submission`; `SubmittedRun`/`CollectingRun.finish/requeue` (T2) used by T5's table and T9; `Executor.submitted_phase`, `RunState`, `RunEvent`, `RESYNC` (T3) used by T5/T6; `RunCredentials.expires_at` (T4) → `SubmittedHandle.credentials_expire_at` (T5) → `record_submission(credentials_expire_at=)` (T2); `CANCEL_CHANNEL` (T1) = `PROCESS_RUN_CANCEL_CHANNEL` (T1) = the `pg_notify` literal (T7) — the fixture binds all three; `RECONCILE_BATCH` defined in T5, used in T6.
- **Proportion.** Code appears only where the signature and tests do not determine it: the migration's constraint lookup, the CAS predicates, the cancel CTE and the reconcile decision table.
