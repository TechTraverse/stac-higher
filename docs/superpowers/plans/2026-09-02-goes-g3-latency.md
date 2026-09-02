# G-3 · Latency Posture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A triggered process run starts within seconds of its item landing instead of waiting for the next one-minute run tick; back-to-back arrivals for the same source coalesce into one run; S3 ingest sources stop paying a second poll to "settle" objects that S3 already guarantees are complete.

**Architecture:** The 2026-09-02 live check on `ai/main` established that the item-write → dispatcher hop is ALREADY event-driven (the outbox trigger's `pg_notify('item_events')` wakes `dispatcher/listener.py`; two items inserted at 06:19:12 had run rows by 06:19:12.8). What remains on the clock: (1) `process_run_tick` is a one-minute cron, so a queued run waits up to 60 s — fixed by an immediate `process_run_now(run_id)` job enqueued by `trigger_run`, with the tick kept as the recovery sweep; (2) the two 06:19:12 events became TWO runs 30 ms apart because coalescing only applies to rate-deferred rows — fixed by widening the partial unique index to every queued run per `(process_id, source_id)`; (3) DISCOVER's two-poll settle check — fixed by a `settle` ingest option defaulting to `auto` (immediate for `s3`, two polls for ssh/ftp).

**Tech Stack:** Python 3.12 (psycopg, Procrastinate), pytest; app migration (TypeScript, `app/src/lib/db/migrate.ts`); Zod v4; contract fixtures.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §5 (amended by Task 6 of this plan to record the NOTIFY finding).

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g3 -b ai/goes-g3 ai/main`; `npm install` at the worktree root; `cd services/pipeline && uv sync --extra dev`.
- Gates: `npm run verify` (root) and `uv run pytest` + `uv run ruff check .` (from `services/pipeline/`). No e2e, no dev server, no Docker for the implementer; the lead runs the loadgen check (Task 6).
- The app owns DDL (ADR 0001): the index change is an app migration, numbered `025`, appended to `MIGRATIONS` in `app/src/lib/db/migrate.ts`; the pipeline never issues DDL.
- Every enqueue path through the rate ceiling stays in `trigger_run` (the module docstring's one-function rule).
- Periodic jobs stay registered; immediate jobs are additive. Overlap must be safe by construction (atomic claims), never by timing.
- The ingest `config` shape is a cross-runtime contract: `associations/schemas.ts` ↔ `pipeline/ingest/config.py` ↔ `tests/contract-fixtures/ingest-config.json` move together.
- Commit messages end with the session's attribution trailer.

---

### Task 1: Migration 025 — one queued run per `(process_id, source_id)`

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append after `024_alerts_process_anchors`, before the closing `];` ~line 1332)
- Test: `app/src/__tests__/` — find the migrations test (`grep -l "MIGRATIONS\|runMigrations" app/src/__tests__/*.test.ts`) and extend it

**Interfaces:**
- Produces: index `process_runs_queued_source_idx` — `UNIQUE (process_id, source_id) WHERE status = 'queued' AND source_id IS NOT NULL`; the old `process_runs_deferred_source_idx` is dropped. Pre-existing duplicate queued rows are merged (later rows' `input_items` appended to the earliest, then deleted) so the unique index can be created.

- [ ] **Step 1: Write the failing test**

In the migrations test file, alongside whatever asserts migration names/order, add:

```ts
it("025 widens run coalescing to every queued run per source", () => {
  const m = MIGRATIONS.find((x) => x.name === "025_process_runs_queued_source_idx");
  expect(m).toBeDefined();
  expect(m!.sql).toContain("DROP INDEX IF EXISTS stac_higher.process_runs_deferred_source_idx");
  expect(m!.sql).toContain("CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_source_idx");
  expect(m!.sql).toContain("WHERE status = 'queued' AND source_id IS NOT NULL");
  // duplicates are merged BEFORE the unique index is created
  expect(m!.sql.indexOf("DELETE FROM stac_higher.process_runs")).toBeLessThan(
    m!.sql.indexOf("CREATE UNIQUE INDEX"),
  );
});
```

(If `MIGRATIONS` is not exported, export it `for tests` the way sibling internals are, or assert through whatever accessor the existing test uses.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/<migrations test file>`
Expected: FAIL — no migration 025.

- [ ] **Step 3: Implement**

Append to `MIGRATIONS`:

```ts
  {
    // GOES spec §5 / plan G-3: coalescing used to apply only to RATE-DEFERRED
    // queued runs (024's partial index on source_id). The 2026-09-02 live
    // check showed two events 30 ms apart becoming two runs; with immediate
    // dispatch that is the common case, so every queued run per
    // (process, source) is now the coalescing key. Existing duplicates are
    // merged first (input_items appended to the earliest row, later rows
    // deleted) or the unique index could not be created.
    name: "025_process_runs_queued_source_idx",
    sql: `
      WITH ranked AS (
        SELECT id, process_id, source_id, input_items,
               row_number() OVER (PARTITION BY process_id, source_id ORDER BY created_at, id) AS rn,
               first_value(id) OVER (PARTITION BY process_id, source_id ORDER BY created_at, id) AS keep_id
          FROM stac_higher.process_runs
         WHERE status = 'queued' AND source_id IS NOT NULL
      ),
      merged AS (
        SELECT keep_id, jsonb_agg(elem ORDER BY rn) AS items
          FROM ranked, jsonb_array_elements(ranked.input_items) AS elem
         WHERE rn > 1
         GROUP BY keep_id
      )
      UPDATE stac_higher.process_runs r
         SET input_items = r.input_items || m.items
        FROM merged m
       WHERE r.id = m.keep_id;

      DELETE FROM stac_higher.process_runs
       WHERE id IN (
         SELECT id FROM (
           SELECT id, row_number() OVER (PARTITION BY process_id, source_id ORDER BY created_at, id) AS rn
             FROM stac_higher.process_runs
            WHERE status = 'queued' AND source_id IS NOT NULL
         ) d WHERE d.rn > 1
       );

      DROP INDEX IF EXISTS stac_higher.process_runs_deferred_source_idx;
      CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_source_idx
        ON stac_higher.process_runs (process_id, source_id)
        WHERE status = 'queued' AND source_id IS NOT NULL;
    `,
  },
```

- [ ] **Step 4: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/<migrations test file> && npm run check`

```bash
git add app/src/lib/db/migrate.ts app/src/__tests__/
git commit -m "feat(db): migration 025 — one queued process run per (process, source) (G-3)"
```

---

### Task 2: `enqueue_run` coalesces into any queued run for the source

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py` (`ProcessRepo.enqueue_run` docstring ~line 112; `PgProcessRepo.enqueue_run` ~lines 301–358)
- Modify: `services/pipeline/tests/_process_fake.py` (`FakeProcessRepo.enqueue_run` and its `_deferred` mirror)
- Test: `services/pipeline/tests/test_process_triggers.py`

**Interfaces:**
- `enqueue_run(...) -> str | None` semantics: when `source_id` is not None, upsert on `(process_id, source_id) WHERE status = 'queued' AND source_id IS NOT NULL`, appending `input_items`; the existing row's `rate_deferred_until` is kept as-is (a deferral stands; an undeferred queued row stays undeferred). Returns the surviving row's id. `is_test` runs and `source_id = None` runs always insert.
- New `EnqueueResult`? No — keep the return type; add a second method `enqueue_run_detailed(...) -> tuple[str, bool]` returning `(run_id, merged)` and make `enqueue_run` call it. `trigger_run` (Task 3) uses the detailed form.

- [ ] **Step 1: Write the failing tests** (in `test_process_triggers.py`, using `FakeProcessRepo` and `trigger_run` like the existing coalescing test)

```python
async def test_undeferred_queued_run_absorbs_a_second_trigger_for_the_same_source():
    repo = FakeProcessRepo()
    now = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    first = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                              input_items=[{"item_id": "a", "collection_id": "c", "op": "insert"}], now=now)
    second = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                               input_items=[{"item_id": "b", "collection_id": "c", "op": "insert"}], now=now)
    assert first.run_id == second.run_id
    assert second.merged is True and first.merged is False
    assert repo.enqueued[0]["input_items"] == [
        {"item_id": "a", "collection_id": "c", "op": "insert"},
        {"item_id": "b", "collection_id": "c", "op": "insert"},
    ]


async def test_a_claimed_run_is_not_a_coalescing_target():
    repo = FakeProcessRepo()
    now = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    first = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                              input_items=[{"item_id": "a"}], now=now)
    repo.mark_claimed(first.run_id)          # status -> running: leaves the partial index
    second = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                               input_items=[{"item_id": "b"}], now=now)
    assert second.run_id != first.run_id and second.merged is False


async def test_sourceless_and_test_runs_never_coalesce():
    repo = FakeProcessRepo()
    now = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    a = await trigger_run(repo, process_id="p", revision_id="r", source_id=None, input_items=[], now=now, is_test=True)
    b = await trigger_run(repo, process_id="p", revision_id="r", source_id=None, input_items=[], now=now, is_test=True)
    assert a.run_id != b.run_id
```

`TriggerResult` gains `merged: bool = False` (Task 3 adds it; add the field now so these tests compile).

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py -k "absorbs or claimed_run or never_coalesce" -v`

- [ ] **Step 3: Implement the Pg repo**

Replace the body of `PgProcessRepo.enqueue_run` with a call to the new detailed method:

```python
    async def enqueue_run(self, **kwargs) -> str | None:  # pragma: no cover
        run_id, _merged = await self.enqueue_run_detailed(**kwargs)
        return run_id

    async def enqueue_run_detailed(  # pragma: no cover
        self, *, process_id, revision_id, source_id, input_items, deferred_until, is_test=False
    ) -> tuple[str, bool]:
        items = json.dumps(list(input_items))
        async with await self._connect() as conn:
            if source_id is not None and not is_test:
                # G-3 coalescing: ONE queued run per (process, source) — the
                # partial unique index (migration 025) is the arbiter, so two
                # dispatch wakes racing for the same source cannot both insert;
                # the loser APPENDS its items. `xmax = 0` tells insert from
                # update on the returned row.
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s, %s)"
                    " ON CONFLICT (process_id, source_id)"
                    "   WHERE status = 'queued' AND source_id IS NOT NULL"
                    " DO UPDATE SET input_items ="
                    "   stac_higher.process_runs.input_items || EXCLUDED.input_items"
                    " RETURNING id, (xmax = 0) AS inserted",
                    (process_id, revision_id, source_id, items, deferred_until, is_test),
                )
                row = await cur.fetchone()
                await conn.commit()
                return str(row[0]), not bool(row[1])
            cur = await conn.execute(
                "INSERT INTO stac_higher.process_runs"
                " (process_id, revision_id, source_id, input_items, rate_deferred_until, is_test)"
                " VALUES (%s, %s, %s, %s::jsonb, %s, %s) RETURNING id",
                (process_id, revision_id, source_id, items, deferred_until, is_test),
            )
            row = await cur.fetchone()
            await conn.commit()
            return str(row[0]), False
```

Read the current method first: keep whatever it does for `record_source_run`/metrics that the sketch above omits. Add `enqueue_run_detailed` to the abstract `ProcessRepo` with a default implementation `return (await self.enqueue_run(**kwargs)), False` so other subclasses keep working, and override it in `PgProcessRepo` and the fake.

- [ ] **Step 4: Update the fake**

In `_process_fake.py`, replace the `_deferred` mirror with `_queued: dict[tuple[str, str], str]` keyed by `(process_id, source_id)` for any queued, non-test, sourced run; `enqueue_run_detailed` appends and returns `(id, True)` on a hit; add `mark_claimed(run_id)` that sets the recorded row's `status = "running"` and removes it from `_queued`; make sure `claim_due_runs`/existing tests that relied on `_deferred` still pass (grep `_deferred` in tests).

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/repo.py services/pipeline/tests/_process_fake.py services/pipeline/tests/test_process_triggers.py
git commit -m "feat(process): coalesce triggers into any queued run for the source (G-3)"
```

---

### Task 3: Immediate `process_run_now` after an undeferred trigger

**Files:**
- Modify: `services/pipeline/src/pipeline/process/trigger.py` (`TriggerResult`, `trigger_run`)
- Modify: `services/pipeline/src/pipeline/process/repo.py` (`claim_run(run_id, now) -> QueuedRun | None`, abstract + Pg + fake)
- Modify: `services/pipeline/src/pipeline/jobs/process.py` (`JOB_RUN_NOW`, `process_run_now`, shared `_execute_claimed`, wire `enqueue_now` into `process_trigger` and `process_cron`)
- Test: `services/pipeline/tests/test_process_triggers.py`, `services/pipeline/tests/test_process_executor.py` (or wherever `process_run_tick` is exercised — `grep -l "process_run_tick\|RUN_BATCH" services/pipeline/tests/*.py`)

**Interfaces:**
- `TriggerResult(run_id, deferred, recent_runs, ceiling, merged: bool = False, enqueued_now: bool = False)`.
- `trigger_run(..., enqueue_now: Callable[[str], Awaitable[None]] | None = None)`: after `enqueue_run_detailed`, if `not deferred and enqueue_now is not None` → `await enqueue_now(run_id)` and `enqueued_now=True`. Called for merged rows too (a second enqueue for an already-queued id is harmless: the claim-by-id is atomic and a second job finds nothing to claim).
- `ProcessRepo.claim_run(run_id: str, now: dt.datetime) -> QueuedRun | None`: the same `UPDATE … SET status='running', started_at, attempts+1, rate_deferred_until=NULL … RETURNING` as `claim_due_runs`, but `WHERE r.id = %s AND r.status = 'queued' AND (r.rate_deferred_until IS NULL OR r.rate_deferred_until <= %s)`; `None` when nothing matched (already claimed by the tick, deferred, or gone).
- Job `pipeline.process_run_now(run_id: str)` registered with `RetrySpec(max_attempts=3, wait_seconds=15)` (executor unavailability); claims by id and runs the same per-run body as the tick.

- [ ] **Step 1: Write the failing tests**

```python
async def test_trigger_enqueues_an_immediate_run_when_not_deferred():
    repo = FakeProcessRepo()
    now = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    seen: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        seen.append(run_id)

    result = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                               input_items=[{"item_id": "a"}], now=now, enqueue_now=enqueue_now)
    assert result.enqueued_now is True and seen == [result.run_id]


async def test_trigger_does_not_enqueue_now_when_rate_deferred():
    repo = FakeProcessRepo(windows={"p": RateWindow(recent_runs=60, oldest_in_window=dt.datetime(2026, 9, 2, tzinfo=dt.UTC), max_runs_per_hour=60)})
    seen: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        seen.append(run_id)

    result = await trigger_run(repo, process_id="p", revision_id="r", source_id="s",
                               input_items=[{"item_id": "a"}], now=dt.datetime(2026, 9, 2, 0, 30, tzinfo=dt.UTC), enqueue_now=enqueue_now)
    assert result.deferred is True and result.enqueued_now is False and seen == []


async def test_claim_run_by_id_is_atomic_against_the_tick():
    repo = FakeProcessRepo()
    now = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    r = await trigger_run(repo, process_id="p", revision_id="r", source_id="s", input_items=[{"item_id": "a"}], now=now)
    first = await repo.claim_run(r.run_id, now)
    second = await repo.claim_run(r.run_id, now)
    assert first is not None and first.id == r.run_id
    assert second is None
```

Check the exact `RateWindow` field names in `process/rate.py` / `_process_fake.py` before using them.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py -k "immediate or deferred or claim_run" -v`

- [ ] **Step 3: Implement `trigger.py`**

```python
from collections.abc import Awaitable, Callable, Sequence

@dataclass(frozen=True)
class TriggerResult:
    run_id: str | None
    deferred: bool
    recent_runs: int = 0
    ceiling: int = 0
    merged: bool = False
    enqueued_now: bool = False


async def trigger_run(repo, *, process_id, revision_id, source_id, input_items, now, is_test=False,
                      enqueue_now: Callable[[str], Awaitable[None]] | None = None) -> TriggerResult:
    ...  # rate window + verdict unchanged
    run_id, merged = await repo.enqueue_run_detailed(
        process_id=process_id, revision_id=revision_id, source_id=source_id,
        input_items=input_items, deferred_until=verdict.deferred_until, is_test=is_test,
    )
    enqueued_now = False
    if not verdict.deferred and enqueue_now is not None and run_id is not None:
        # G-3: do not wait for the minute tick. The tick stays registered as the
        # recovery sweep; a run claimed by one path is invisible to the other.
        await enqueue_now(run_id)
        enqueued_now = True
    ...  # deferred logging unchanged
    return TriggerResult(run_id=run_id, deferred=verdict.deferred, recent_runs=verdict.recent_runs,
                         ceiling=verdict.ceiling, merged=merged, enqueued_now=enqueued_now)
```

- [ ] **Step 4: Implement `claim_run`** in `repo.py` (abstract, Pg — copy the `claim_due_runs` statement and swap the `due` CTE for the id predicate — and fake).

- [ ] **Step 5: Implement the job** in `jobs/process.py`

Refactor `process_run_tick`'s per-run loop into

```python
    async def _execute_claimed(runs: list[QueuedRun], repo: PgProcessRepo) -> None:
        # the existing body: executor, storage client, resolver, fetch_remote,
        # per-run try/except around run_one, collect finalize payloads,
        # enqueue_batch(JOB_FINALIZE, ...)
```

then

```python
    async def process_run_tick(timestamp: int) -> None:
        repo = _repo()
        runs = await repo.claim_due_runs(dt.datetime.now(dt.UTC), RUN_BATCH)
        if runs:
            await _execute_claimed(runs, repo)

    async def process_run_now(run_id: str) -> None:
        """G-3: run one just-queued row immediately. Claiming by id is the same
        atomic UPDATE the tick uses, so whichever path claims first wins and the
        other finds nothing — never a double execution."""
        repo = _repo()
        run = await repo.claim_run(run_id, dt.datetime.now(dt.UTC))
        if run is None:
            logger.info("process_run_now: nothing to claim", extra={"run_id": run_id})
            return
        await _execute_claimed([run], repo)

    async def _enqueue_now(run_id: str) -> None:
        await queue.enqueue(JOB_RUN_NOW, {"run_id": run_id})
```

Pass `enqueue_now=_enqueue_now` in both `trigger_run` calls (`process_trigger` and `process_cron`) and register `queue.register_task(process_run_now, name=JOB_RUN_NOW, retry=RetrySpec(max_attempts=3, wait_seconds=15))` with `JOB_RUN_NOW = "pipeline.process_run_now"`. Update the module docstring's job list.

- [ ] **Step 6: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/process/trigger.py services/pipeline/src/pipeline/process/repo.py services/pipeline/src/pipeline/jobs/process.py services/pipeline/tests/
git commit -m "feat(process): immediate process_run_now after an undeferred trigger; claim by id (G-3)"
```

---

### Task 4: `settle` option — S3 objects need no second poll

**Files:**
- Modify: `app/src/lib/associations/schemas.ts` (`ingestConfigSchema`, ~line 77)
- Modify: `tests/contract-fixtures/ingest-config.json` (`defaults` + cases)
- Modify: `services/pipeline/src/pipeline/ingest/config.py` (`IngestConfig.settle`, `SETTLE_MODES`, parser)
- Modify: `services/pipeline/src/pipeline/ingest/discover.py` (`discover_stage` → `_reconcile` gets `immediate: bool`)
- Test: `services/pipeline/tests/test_ingest_discover.py`, `app/src/__tests__/contract-fixtures.test.ts` (picks up the fixture), `services/pipeline/tests/test_contract_fixtures.py` (defaults assertion)

**Interfaces:**
- `settle: "auto" | "two_polls" | "immediate"`, default `"auto"`. Effective mode: explicit value, else `immediate` when the association's connection protocol is `s3`, else `two_polls`. `IngestConfig.settle: str = "auto"`; helper `effective_settle(config: IngestConfig, protocol: str) -> str` in `ingest/config.py`.
- DISCOVER with `immediate`: a newly seen file is inserted directly as `settled` (counted in `settled`/`settled_bytes`); an already-`seen` row (from before the switch) follows the existing path.

- [ ] **Step 1: Fixture** — add `"settle": "auto"` to `defaults`; cases: `{"settle":"immediate"}` accept/accept, `{"settle":"two_polls"}` accept/accept, `{"settle":"sometimes"}` reject/reject.

- [ ] **Step 2: Failing pipeline tests**

```python
def test_effective_settle_defaults_by_protocol():
    from pipeline.ingest.config import effective_settle
    cfg = parse_ingest_config({"source_path": "/out"})
    assert effective_settle(cfg, "s3") == "immediate"
    assert effective_settle(cfg, "sftp") == "two_polls"
    assert effective_settle(parse_ingest_config({"source_path": "/out", "settle": "two_polls"}), "s3") == "two_polls"


async def test_s3_source_settles_a_new_file_on_first_sight():
    repo = FakeIngestRepo()
    adapter = FakeAdapter(entries=[_entry("products/a.tif", size=10, etag="e1")])
    cfg = parse_ingest_config({"source_path": "products/"})
    result = await discover_stage(repo, _assoc({"source_path": "products/"}), cfg, adapter)
    assert result.settled == 1 and result.new_seen == 0
    latest = await repo.get_latest_ledger("assoc1", "a.tif")
    assert latest.status == "settled"


async def test_two_polls_still_required_when_forced_on_s3():
    repo = FakeIngestRepo()
    adapter = FakeAdapter(entries=[_entry("products/a.tif", size=10, etag="e1")])
    cfg = parse_ingest_config({"source_path": "products/", "settle": "two_polls"})
    result = await discover_stage(repo, _assoc({"source_path": "products/", "settle": "two_polls"}), cfg, adapter)
    assert result.new_seen == 1 and result.settled == 0
```

Read `_ingest_fake.py`'s `FakeAdapter`/`FakeIngestRepo` constructors and the existing "settles on second poll" test in this file to match their exact idiom; the `_assoc` helper already declares `protocol="s3"`. Expect the existing two-poll tests to need `"settle": "two_polls"` in their config now that s3 defaults to immediate — update them and say so in the commit.

- [ ] **Step 3: Implement**

`config.py`: `SETTLE_MODES = ("auto", "two_polls", "immediate")`; field `settle: str = "auto"`; parse with `_enum(raw.get("settle"), SETTLE_MODES, "auto", "settle")`; add

```python
def effective_settle(config: IngestConfig, protocol: str) -> str:
    """S3 objects are atomically visible (a key exists only once its PUT
    completed), so the second poll only adds latency; FTP/SFTP uploads are
    visible mid-write and need it (ROADMAP §6.1)."""
    if config.settle != "auto":
        return config.settle
    return "immediate" if protocol == "s3" else "two_polls"
```

`discover.py`: in `discover_stage`, compute `immediate = effective_settle(config, adapter.protocol) == "immediate"` and pass it to `_reconcile`; in `_reconcile`'s `latest is None` branch insert with `status=STATUS_SETTLED if immediate else STATUS_SEEN` and bump `result.settled`/`settled_bytes` instead of `new_seen` when immediate.

`schemas.ts`: `settle: z.enum(["auto", "two_polls", "immediate"]).default("auto")` inside `ingestConfigSchema` with a two-line comment.

- [ ] **Step 4: Run all gates, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`; `cd app && npm test && npm run check`.

```bash
git add app/src/lib/associations/schemas.ts tests/contract-fixtures/ingest-config.json services/pipeline/src/pipeline/ingest/config.py services/pipeline/src/pipeline/ingest/discover.py services/pipeline/tests/
git commit -m "feat(ingest): settle option; s3 sources settle on first sight (G-3)"
```

---

### Task 5: Loadgen note and docs

**Files:**
- Modify: `services/pipeline/src/pipeline/loadgen/README.md` (the `sleep 60 # … needs TWO polls` line)
- Modify: `docs/processes.md` ("Rate ceiling" / a new "When does a run start" paragraph)
- Modify: `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §5 table
- Modify: `docs/FEATURES.md` (G queue section, G-3 row), `docs/connections.md` or the ingest docs where `poll_frequency_seconds` is described (add `settle`)

- [ ] **Step 1: Spec §5 amendment** — replace the "Item write → dispatcher" row's "Change" cell with: "**Already event-driven** (found in the 2026-09-02 live check): the outbox trigger's `pg_notify('item_events')` wakes `dispatcher/listener.py`; the minute poll is the fallback. No change." Replace the "Dispatcher → run claim" row's cell with "`trigger_run` enqueues `process_run_now(run_id)`; claim-by-id shares the tick's atomic UPDATE; the tick stays as the sweep." Add to the "Extractor run" row: "coalescing now covers every queued run per (process, source) — migration 025."

- [ ] **Step 2: `docs/processes.md`** — add under "Rate ceiling":

> **When does a run start?** Within seconds of the triggering item landing: the catalog write wakes the dispatcher (Postgres NOTIFY), the dispatcher queues the run and enqueues it for immediate execution, and the executor launches it. A one-minute sweep still exists, but only to recover a run whose immediate job was lost. Items that arrive for the same source while a run is still queued join that run's batch rather than starting another.

- [ ] **Step 3: loadgen README** — change the `sleep 60` comment to "DISCOVER polls every 60 s; s3 sources settle on first sight since G-3 (`settle: auto`), so one poll is enough".

- [ ] **Step 4: Verify, commit**

Run `npm run verify` from the root (docs-only for the app but the gate is the gate).

```bash
git add docs/ services/pipeline/src/pipeline/loadgen/README.md
git commit -m "docs: latency posture — NOTIFY finding, immediate runs, settle option (G-3)"
```

---

### Task 6 (LEAD ONLY, Docker): concurrency check

With the stack rebuilt on the merged branch (`docker compose up -d --build pipeline` from the repo root, plus running the app once so migration 025 applies), run the loadgen at the M3-D concurrency the pipeline is currently configured with and confirm: (1) no duplicate `process_run_now` executions (`select id, attempts from stac_higher.process_runs where attempts > 1` stays empty for succeeded runs), (2) Procrastinate's `procrastinate_jobs` table shows one `pipeline.process_run_now` per run id, (3) a burst of N items for one source yields ≤ N runs with the later ones coalesced. Record the numbers in `TODO.md`'s "Discovered follow-ups" if anything is off; otherwise tick G-3.
