# Z-3 · Queue Lock Seam, Dispatcher Matching and `cube_kick` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get each new source item into the cube ledger and one single-writer `pipeline.cube_append` job queued per sink. The append itself is Z-4; Z-3's `cube_append` is a stub that marks its pending rows `failed: not_implemented`.

**Architecture:** The queue interface gains `lock` / `queueing_lock` on `enqueue` and returns an `Enqueued` result whose `coalesced` flag replaces Procrastinate's `AlreadyEnqueued` exception (caught in the backend, spec §14.2). A new `cubes/repo.py` (`CubeRepo` ABC + `PgCubeRepo`) holds every `cube_sinks` / `cube_appends` statement the pipeline runs in this slice. The queue also gains `retry_stalled(job_name)`, which hands a job a dead worker left `doing` back to the queue; otherwise that job holds its lock forever. `jobs/cubes.py` owns the lock name, the enqueue helper, the 5-minute `cube_kick` backstop (stalled-job recovery first, then stale sinks) and the stub. The dispatcher loop matches `insert` events only, buffers ledger rows for the claim, and writes them and enqueues one job per sink **before** draining the outbox.

**Tech Stack:** Python 3.12, Procrastinate 3.9.0, psycopg 3 (async pool), pytest (asyncio auto mode), ruff. No app change, no migration, no new dependency.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §5 (decision §14.2; §6 and §10 for what Z-4/Z-6 need); spike `docs/research/2026-10-03-virtual-cube-spike.md` §7; ADR 0022; epic #83; issue #89.

## Global Constraints

- **Worktree:** `.claude/worktrees/z3-cube-dispatch`, branch `feat/z3-cube-dispatch`, off a fresh `origin/main` (0709865). Never work in the main checkout.
- **Gates:** each task ends green on what it touches. The final task runs `npm run verify` (repo root) and, from `services/pipeline/`, `uv run pytest` and `uv run ruff check .`. No e2e, no dev server, no Docker, no load harness. The real-DB check uses a **throwaway** Postgres in the session scratchpad on TCP `localhost:5499` — never the compose DB (it carries the standing GOES demo).
- **No migration.** Migration 032 (`cube_sinks`, `cube_appends`) is on `main`. The pipeline runs no DDL (ADR 0001). If a task seems to need DDL, stop and ask.
- **The pipeline never writes `cube_sinks.updated_at`** (it is the app's optimistic-lock version, #98/#99). Z-3 writes **nothing** to `cube_sinks` at all; it reads `id`, `cube_collection_id`, `source_collection_id`, `enabled`. A DB-gated test pins this.
- **Lock names:** `lock` = `queueing_lock` = `cube:{cube_sink_id}` (spec §2, §5.1). Job names: `pipeline.cube_append` (payload `{cube_sink_id}`, default queue) and `pipeline.cube_kick` (cron `*/5 * * * *`).
- **Ledger vocabulary** (fixture `cube-append-status.json`, unchanged): statuses `pending | appended | skipped | failed`; the dispatcher writes `pending`, or `skipped` with reason `no_datetime`. The stub writes `failed` with reason `not_implemented` (free text; failed reasons are not a closed set).
- **Backstop staleness:** `pending` rows whose `created_at` is older than **120 s**, on **enabled** sinks.
- **Logging:** data in `extra={...}`, never interpolated (backend-invariants).
- **Commit trailer:** every commit message ends with
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Decisions this plan takes (flag in the PR)

1. **`Enqueued` is a frozen dataclass** `Enqueued(job_id: str | None, coalesced: bool = False)`, returned by `enqueue` for every call, not only locked ones. "Returned as `Enqueued.coalesced`" (spec §5.1) reads as "a result whose `coalesced` flag is set". No production caller uses `enqueue`'s return value today; two tests change from `job_id == "1"` to `.job_id == "1"`. `enqueue_batch` keeps returning `list[str]`.
2. **Only the options given reach `configure`.** `enqueue(..., lock=None, queueing_lock=None)` calls `task.defer_async` directly; passing `queueing_lock=None` to `configure` could clear a task-level default.
3. **The memory backend's `queueing_lock` covers waiting jobs only**, like Procrastinate (its unique index is on `status = 'todo'`). A job that is running can enqueue its own successor with the same locks, which Z-4's self re-enqueue (§6.2) depends on.
4. **No-datetime rows still need an `item_datetime`** (the column is `NOT NULL`). They take the event's `occurred_at`, else `now()`. The row is terminal (`skipped`) and never ordered for an append.
5. **A datetime that does not parse counts as missing** (`skipped: no_datetime`), parsed in Python inside the per-event isolation. Passing the raw string to Postgres would let one bad value fail the whole claim's insert. pgstac refuses such items anyway, so this is defensive.
6. **The cube step runs first among the post-loop writes** (ledger insert → cube enqueues → finalize → process runs → deliveries → drain). The cube step is fully idempotent (`ON CONFLICT DO NOTHING` + `queueing_lock`), so if it raises nothing non-idempotent has been queued for the claim yet. In-loop, the matching still happens after process and deliver matching, as §5.2 says.
7. **A sink lookup that raises drops that event's cube row, logged at ERROR, and the event still drains.** It does not defer the event: by then its delivery and process matches are already batched, and redriving the event would queue a duplicate process run. A missing table (pipeline newer than the DB) must not stall deliveries. The failure is cached as "no sinks" for that collection for the rest of the claim, so a missing table costs one query and one traceback per claim, not one per event. The consequence is a gap in the cube for that claim's items from that collection, which v1 already allows (late files are skipped).
8. **A ledger insert that raises leaves the claim unprocessed** (the existing enqueue-before-drain rule); the redrive is a no-op thanks to `ON CONFLICT`.
9. **The ledger insert joins `cube_sinks … AND enabled`**, so a sink deleted or disabled between the lookup and the insert is skipped instead of raising an FK error that would stall the outbox.
10. **One job per sink per claim, and only for sinks that got at least one `pending` row.** A claim of 500 inserts enqueues once per sink. A sink whose only rows were `skipped: no_datetime` is not woken.
11. **`main.py` registers `jobs/cubes.py`.** `main.py` is not in #89's file list, but every job module registers there; the alternative (registering from `dispatch.register`) hides it.
12. **The stub registers no retry.** §6 says "retry 3, exponential"; `RetrySpec` has only a fixed wait, and the stub cannot fail usefully. Z-4 adds the retry (and, if it wants exponential, the `RetrySpec` field).
13. **Staged inserts never match a sink.** The staged gate `continue`s before matching, and finalize's upsert emits an `update`, which §5.2 excludes. Cube sources are reference-mode ingest items, never staged; Z-4 would mark a staged item `no_source_connection` anyway. A test pins the behaviour so it is a choice, not an accident.
14. **`cube_kick` also recovers stalled `cube_append` jobs (beyond spec §5.3).** Procrastinate will not start a job while a same-`lock` job is `doing` (`procrastinate_fetch_job_v2`), and nothing in the pipeline recovers stalled jobs today. A worker killed mid-append (SIGKILL, OOM, host loss) would leave its row `doing` and wedge the sink. Every later wake would coalesce into one `todo` job that never starts, and the kick's own enqueue would coalesce into it too. A graceful stop is already safe: the 25 s abort ends the job `failed` and releases the lock. `QueueBackend.retry_stalled(job_name)` (Task 2) uses Procrastinate's heartbeat-based `get_stalled_jobs` + `retry_job`. If a waiting job already holds the stalled job's `queueing_lock`, the requeue would hit the unique index, so the stalled job is finished `failed` instead; the waiting job does the same work.
15. **The stall threshold is 300 s of missed heartbeats (`STALLED_WORKER_SECONDS`), not Procrastinate's 30 s default.** Workers heartbeat every 10 s from the event loop, so a job that blocks the loop also stops the heartbeat. Requeueing a job that is still running would run two appends under one lock. Recovery therefore takes up to 5 min of silence plus the next tick. One false positive remains: a worker starting up prunes any worker row whose heartbeat is older than 30 s, which nulls `worker_id` on that worker's jobs and makes them look stalled at once. Z-4 stays safe because a double run is detected: Icechunk commits are optimistic (`ConflictError` → reopen and redo, spec §6.2 step 7), and a redo finds the steps already present (`appended`, duplicate no-op). The Z-4 issue (#90) must keep that property.

## Review Focus

1. **A bulk ingest of hundreds of items into a source collection** must enqueue one `cube_append` per sink per claim, not one per item (Task 5, "one job per sink however many items").
2. **GOES item datetimes carry nanoseconds** (`2026-10-03T17:02:36.714359936Z`) and must land as microsecond timestamps, not as `no_datetime` (Task 5, "nanosecond datetime").
3. **The cube tables missing or the lookup erroring** must not stop that item's deliveries or process runs (Task 5, "a sink lookup failure keeps the item's deliveries").
4. **A sink deleted or disabled between the lookup and the insert** must not stall the outbox on an FK error (Task 3, DB-gated "skips a sink deleted or disabled after the lookup").
5. **A running `cube_append` that re-enqueues itself** must be accepted, not coalesced away, or Z-4's 50-row cap would strand the backlog until the next kick (Task 1, "a running job can enqueue its successor").
6. **A `cube_append` left `doing` by a killed worker** must not wedge its sink. The next `cube_kick` requeues it, or closes it as `failed` when a waiting job already covers the sink (Task 2, DB-gated "retry_stalled against real Procrastinate"; Task 4, "kick recovers a stranded append").

---

## File Structure

| File | Responsibility |
|---|---|
| `services/pipeline/src/pipeline/queue/interface.py` | `Enqueued`; `enqueue(..., *, lock=None, queueing_lock=None) -> Enqueued`; `retry_stalled(job_name) -> int` |
| `services/pipeline/src/pipeline/queue/procrastinate_backend.py` | `configure(lock, queueing_lock)`; `AlreadyEnqueued` → `Enqueued(coalesced=True)`; `retry_stalled` via `get_stalled_jobs` + `retry_job` / `finish_job` |
| `services/pipeline/src/pipeline/queue/memory.py` | test double: coalesce waiting same-`queueing_lock` jobs; run same-`lock` jobs one at a time; `strand(job_id)` + `retry_stalled` |
| `services/pipeline/src/pipeline/cubes/repo.py` (new) | `CubeSinkRef`, `LedgerEntry`, `CubeRepo` ABC, `PgCubeRepo` |
| `services/pipeline/src/pipeline/jobs/cubes.py` (new) | job names, `cube_lock`, `enqueue_cube_append`, `cube_append_enqueuer`, `kick_stale_sinks`, `fail_pending_stub`, `register` |
| `services/pipeline/src/pipeline/dispatcher/loop.py` | insert-only sink matching, ledger buffer, post-loop write + enqueue |
| `services/pipeline/src/pipeline/jobs/dispatch.py` | wire `PgCubeRepo` + `cube_append_enqueuer` into the drain |
| `services/pipeline/src/pipeline/main.py` | `cubes.register(queue, settings)` |
| `services/pipeline/tests/test_queue_interface.py` | `Enqueued`, memory lock semantics, stalled recovery |
| `services/pipeline/tests/test_procrastinate_backend.py` | `configure` options, coalescing, `retry_stalled` |
| `services/pipeline/tests/test_integration_db.py` | DB-gated: real Procrastinate lock + coalesce + stalled recovery |
| `services/pipeline/tests/_cube_fake.py` (new) | `FakeCubeRepo` |
| `services/pipeline/tests/test_cube_jobs.py` (new) | kick, stub, registration |
| `services/pipeline/tests/test_dispatch_cubes.py` (new) | dispatcher matching |
| `services/pipeline/tests/test_integration_cubes_repo.py` (new) | DB-gated `PgCubeRepo` SQL + `updated_at` untouched |
| `services/pipeline/tests/test_main_jobs.py` | cube jobs registered |
| `services/pipeline/README.md`, `docs/FEATURES.md` | docs |

---

### Task 1: Queue seam — `Enqueued`, locks on both backends

**Files:**
- Modify: `services/pipeline/src/pipeline/queue/interface.py`
- Modify: `services/pipeline/src/pipeline/queue/memory.py`
- Modify: `services/pipeline/src/pipeline/queue/procrastinate_backend.py`
- Test: `services/pipeline/tests/test_queue_interface.py`, `services/pipeline/tests/test_procrastinate_backend.py`, `services/pipeline/tests/test_integration_db.py`

**Interfaces:**
- Produces: `pipeline.queue.interface.Enqueued(job_id: str | None, coalesced: bool = False)` (frozen dataclass); `QueueBackend.enqueue(job_name: str, payload: JobPayload | None = None, *, lock: str | None = None, queueing_lock: str | None = None) -> Enqueued`. `InMemoryQueue.Job` gains `lock: str | None` and `queueing_lock: str | None`; job status adds `"running"`.

- [ ] **Step 1: Write the failing memory-backend tests** — append to `tests/test_queue_interface.py` and update `test_register_and_run_task`:

```python
from pipeline.queue.interface import Enqueued, QueueConnectionError, QueueError
```

In `test_register_and_run_task`, replace `assert job_id == "1"` with:

```python
    assert job_id == Enqueued(job_id="1")
```

New tests:

```python
async def test_enqueue_records_locks(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    result = await queue.enqueue("jobs.locked", {"n": 1}, lock="L", queueing_lock="Q")
    assert result == Enqueued(job_id="1", coalesced=False)
    assert (queue.jobs[0].lock, queue.jobs[0].queueing_lock) == ("L", "Q")


async def test_queueing_lock_coalesces_a_waiting_job(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    first = await queue.enqueue("jobs.locked", {"n": 1}, queueing_lock="Q")
    second = await queue.enqueue("jobs.locked", {"n": 2}, queueing_lock="Q")
    other = await queue.enqueue("jobs.locked", {"n": 3}, queueing_lock="R")
    assert first.coalesced is False
    assert second == Enqueued(job_id=None, coalesced=True)
    assert other.coalesced is False
    assert [j.payload for j in queue.jobs] == [{"n": 1}, {"n": 3}]


async def test_queueing_lock_frees_once_the_job_has_run(queue: InMemoryQueue):
    queue.register_task(lambda **kw: None, name="jobs.locked")
    await queue.enqueue("jobs.locked", {}, queueing_lock="Q")
    await queue.run_pending()
    again = await queue.enqueue("jobs.locked", {}, queueing_lock="Q")
    assert again.coalesced is False


async def test_a_running_job_can_enqueue_its_successor(queue: InMemoryQueue):
    # Z-4's self re-enqueue (spec §6.2): a running job does not hold its own
    # queueing_lock, exactly like Procrastinate's status='todo' unique index.
    results: list[Enqueued] = []

    async def handler(n: int) -> None:
        if n == 1:
            results.append(
                await queue.enqueue("jobs.self", {"n": 2}, lock="L", queueing_lock="L")
            )

    queue.register_task(handler, name="jobs.self")
    await queue.enqueue("jobs.self", {"n": 1}, lock="L", queueing_lock="L")
    await queue.run_pending()
    assert results == [Enqueued(job_id="2")]
    assert [j.status for j in queue.jobs] == ["done", "done"]


async def test_same_lock_jobs_run_one_at_a_time(queue: InMemoryQueue):
    order: list[str] = []

    async def handler(name: str) -> None:
        order.append(f"start {name}")
        if name == "a":
            # Re-entrant drive while "a" holds lock L: "b" (same lock) must
            # wait, "c" (another lock) may run.
            await queue.run_pending()
        order.append(f"end {name}")

    queue.register_task(handler, name="jobs.lock")
    await queue.enqueue("jobs.lock", {"name": "a"}, lock="L")
    await queue.enqueue("jobs.lock", {"name": "b"}, lock="L")
    await queue.enqueue("jobs.lock", {"name": "c"}, lock="M")
    await queue.run_pending()
    assert order == ["start a", "start c", "end c", "end a", "start b", "end b"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_queue_interface.py -q` (from `services/pipeline/`)
Expected: FAIL — `ImportError: cannot import name 'Enqueued'`.

- [ ] **Step 3: Implement `Enqueued` and the new `enqueue` signature** in `queue/interface.py`.

Add after `RetrySpec`:

```python
@dataclass(frozen=True)
class Enqueued:
    """What one ``enqueue`` did (virtual cube spec §5.1, decision §14.2).

    ``coalesced`` means a job with the same ``queueing_lock`` was already
    waiting, so nothing new was queued. That is success: the waiting job
    will see whatever the caller just wrote. Backends return it instead of
    raising, so no caller can turn coalescing into a retry loop.
    """

    #: the backend-scoped id of the new job; None when coalesced
    job_id: str | None
    coalesced: bool = False
```

Replace the abstract `enqueue`:

```python
    @abc.abstractmethod
    async def enqueue(
        self,
        job_name: str,
        payload: JobPayload | None = None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Enqueued:
        """Enqueue one job.

        ``lock``: jobs sharing it never run at the same time (one writer per
        cube repository). ``queueing_lock``: while a job holding it is still
        waiting, another enqueue with it is refused and comes back as
        ``Enqueued(job_id=None, coalesced=True)``. A job that is already
        running does not hold its ``queueing_lock``, so it can enqueue its own
        successor.
        """
```

- [ ] **Step 4: Implement the memory backend** (`queue/memory.py`).

Import `Enqueued` from the interface (after `QUEUE_DEFAULT`, ruff's isort order). `Job` gains:

```python
@dataclass
class Job:
    id: str
    name: str
    payload: dict[str, Any]
    status: str = "pending"  # pending | running | done | failed
    lock: str | None = None
    queueing_lock: str | None = None
```

A lock counts as held while any job carrying it is `running`, which is how Procrastinate decides (`status = 'doing'`). Task 2 relies on this: a job stranded `running` by a dead worker keeps its lock.

Replace `enqueue`, `enqueue_batch` and `run_pending`:

```python
    async def enqueue(
        self,
        job_name: str,
        payload: JobPayload | None = None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Enqueued:
        if job_name not in self.tasks:
            raise QueueError(f"unknown task: {job_name}")
        if queueing_lock is not None and any(
            job.queueing_lock == queueing_lock and job.status == "pending"
            for job in self.jobs
        ):
            # Procrastinate refuses only while the holder is waiting ('todo').
            return Enqueued(job_id=None, coalesced=True)
        job = self._add_job(job_name, payload, lock=lock, queueing_lock=queueing_lock)
        return Enqueued(job_id=job.id)

    async def enqueue_batch(self, job_name: str, payloads: Sequence[JobPayload]) -> list[str]:
        return [self._add_job(job_name, payload).id for payload in payloads]

    def _add_job(
        self,
        job_name: str,
        payload: JobPayload | None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Job:
        if job_name not in self.tasks:
            raise QueueError(f"unknown task: {job_name}")
        job = Job(
            id=str(self._next_id),
            name=job_name,
            payload=dict(payload or {}),
            lock=lock,
            queueing_lock=queueing_lock,
        )
        self._next_id += 1
        self.jobs.append(job)
        return job
```

```python
    async def run_pending(self) -> int:
        """Execute all pending jobs; returns how many ran.

        A job whose ``lock`` is held by a running job is left pending for a
        later pass (re-entrant drives see this; a sequential pass never
        overlaps anyway).
        """
        ran = 0
        for job in self.jobs:
            if job.status != "pending" or self._lock_held(job.lock):
                continue
            job.status = "running"
            try:
                await _call(self.tasks[job.name], **job.payload)
                job.status = "done"
            except Exception:
                job.status = "failed"
                raise
            ran += 1
        return ran

    def _lock_held(self, lock: str | None) -> bool:
        # Procrastinate: a 'doing' job holds its lock until it finishes, even
        # when its worker has died (retry_stalled releases those).
        return lock is not None and any(
            job.lock == lock and job.status == "running" for job in self.jobs
        )
```

Update the module docstring's last line to mention the lock semantics:

```python
"""In-memory queue backend for unit tests.

Executes nothing on its own: tests call :meth:`run_pending` (or
:meth:`run_periodic`) to drive handlers deterministically. ``queueing_lock``
and ``lock`` follow Procrastinate's semantics (virtual cube spec §5.1): a
second enqueue while a same-``queueing_lock`` job is waiting is coalesced,
and same-``lock`` jobs never run at once.
"""
```

- [ ] **Step 5: Run the memory tests**

Run: `uv run pytest tests/test_queue_interface.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing Procrastinate tests** — append to `tests/test_procrastinate_backend.py`:

```python
class _FakeDeferrer:
    def __init__(self, outcome):
        self.outcome = outcome
        self.payloads: list[dict] = []

    async def defer_async(self, **payload):
        self.payloads.append(payload)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _wire(queue: ProcrastinateQueue, monkeypatch, outcome):
    """Register jobs.locked and capture what enqueue hands Procrastinate."""

    async def handler(**kw):
        pass

    async def fake_open():
        pass

    queue.register_task(handler, name="jobs.locked")
    task = queue.app.tasks["jobs.locked"]
    deferrer = _FakeDeferrer(outcome)
    configured: list[dict] = []

    def fake_configure(**options):
        configured.append(options)
        return deferrer

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(task, "configure", fake_configure)
    monkeypatch.setattr(task, "defer_async", deferrer.defer_async)
    return deferrer, configured


async def test_enqueue_passes_both_locks_to_configure(queue, monkeypatch):
    deferrer, configured = _wire(queue, monkeypatch, 42)
    result = await queue.enqueue(
        "jobs.locked", {"cube_sink_id": "s"}, lock="cube:s", queueing_lock="cube:s"
    )
    assert result == Enqueued(job_id="42")
    assert configured == [{"lock": "cube:s", "queueing_lock": "cube:s"}]
    assert deferrer.payloads == [{"cube_sink_id": "s"}]


async def test_enqueue_passes_only_the_options_given(queue, monkeypatch):
    _deferrer, configured = _wire(queue, monkeypatch, 7)
    await queue.enqueue("jobs.locked", {}, lock="cube:s")
    assert configured == [{"lock": "cube:s"}]


async def test_enqueue_without_locks_skips_configure(queue, monkeypatch):
    deferrer, configured = _wire(queue, monkeypatch, 7)
    result = await queue.enqueue("jobs.locked", {"n": 1})
    assert result == Enqueued(job_id="7")
    assert configured == []
    assert deferrer.payloads == [{"n": 1}]


async def test_already_enqueued_comes_back_coalesced(queue, monkeypatch):
    from procrastinate.exceptions import AlreadyEnqueued

    _wire(queue, monkeypatch, AlreadyEnqueued("cube:s"))
    result = await queue.enqueue(
        "jobs.locked", {"cube_sink_id": "s"}, lock="cube:s", queueing_lock="cube:s"
    )
    assert result == Enqueued(job_id=None, coalesced=True)
```

and add `from pipeline.queue.interface import Enqueued, RetrySpec` to its imports.

- [ ] **Step 7: Run them to see them fail**

Run: `uv run pytest tests/test_procrastinate_backend.py -q`
Expected: FAIL — `enqueue() got an unexpected keyword argument 'lock'`.

- [ ] **Step 8: Implement the Procrastinate backend** (`queue/procrastinate_backend.py`).

Imports: add `from procrastinate.exceptions import AlreadyEnqueued` and `Enqueued` to the interface import. Replace `enqueue`:

```python
    async def enqueue(
        self,
        job_name: str,
        payload: JobPayload | None = None,
        *,
        lock: str | None = None,
        queueing_lock: str | None = None,
    ) -> Enqueued:
        await self._ensure_open()
        task = self.app.tasks[job_name]
        # Only the options given: configure(queueing_lock=None) could clear a
        # task-level default.
        options = {
            key: value
            for key, value in (("lock", lock), ("queueing_lock", queueing_lock))
            if value is not None
        }
        deferrer = task.configure(**options) if options else task
        try:
            job_id = await deferrer.defer_async(**dict(payload or {}))
        except AlreadyEnqueued:
            # Spec §14.2: coalescing is success. A job holding this
            # queueing_lock is already waiting and will see the new work.
            logger.debug(
                "enqueue coalesced",
                extra={"job_name": job_name, "queueing_lock": queueing_lock},
            )
            return Enqueued(job_id=None, coalesced=True)
        return Enqueued(job_id=str(job_id))
```

- [ ] **Step 9: Update the DB-gated queue test** (`tests/test_integration_db.py`).

In `test_setup_is_idempotent_and_enqueue_works`, rename `job_id` to `result` (it is an `Enqueued` now) and replace `assert job_id.isdigit()` with:

```python
    assert result.job_id is not None and result.job_id.isdigit()
    assert result.coalesced is False
```

Append:

```python
async def test_queueing_lock_coalesces_and_lock_reaches_the_row(queue):
    """Spec §5.1 against real Procrastinate: the second defer with the same
    queueing_lock is coalesced (not raised), another lock is accepted, and
    both locks are stored on the job row."""
    import psycopg

    from pipeline.queue.interface import Enqueued

    await queue.setup()

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.locked")
    first = await queue.enqueue("jobs.locked", {"n": 1}, lock="cube:a", queueing_lock="cube:a")
    second = await queue.enqueue("jobs.locked", {"n": 2}, lock="cube:a", queueing_lock="cube:a")
    other = await queue.enqueue("jobs.locked", {"n": 3}, lock="cube:b", queueing_lock="cube:b")
    await queue.aclose()

    assert first.coalesced is False and first.job_id is not None
    assert second == Enqueued(job_id=None, coalesced=True)
    assert other.coalesced is False

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(
            f'SELECT lock, queueing_lock FROM "{SCHEMA}".procrastinate_jobs WHERE id = %s',
            (int(first.job_id),),
        )
        assert await cur.fetchone() == ("cube:a", "cube:a")
```

- [ ] **Step 10: Run the queue tests and the whole suite**

Run: `uv run pytest tests/test_procrastinate_backend.py tests/test_queue_interface.py -q`, then `uv run pytest -q`
Expected: PASS (the DB-gated file skips without `DATABASE_URL`; it runs in Task 6).

- [ ] **Step 11: Commit**

```bash
git add services/pipeline/src/pipeline/queue services/pipeline/tests/test_queue_interface.py services/pipeline/tests/test_procrastinate_backend.py services/pipeline/tests/test_integration_db.py
git commit -m "Z-3: lock and queueing_lock on enqueue, Enqueued.coalesced

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Stalled-job recovery — `retry_stalled`

Decisions 14 and 15. A job a dead worker left `doing` holds its `lock` forever, and nothing in the pipeline recovers stalled jobs today. Before Z-3 no job had a lock, so such a row was harmless (the ledger sweeps re-drive the work); with `cube:{id}` it wedges the sink.

**Files:**
- Modify: `services/pipeline/src/pipeline/queue/interface.py`, `queue/memory.py`, `queue/procrastinate_backend.py`
- Test: `services/pipeline/tests/test_queue_interface.py`, `tests/test_procrastinate_backend.py`, `tests/test_integration_db.py`

**Interfaces:**
- Consumes: Task 1's `Job.lock` / `queueing_lock` and the status-derived `_lock_held`.
- Produces: `QueueBackend.retry_stalled(job_name: str) -> int` (abstract; returns the stalled jobs handled); `pipeline.queue.procrastinate_backend.STALLED_WORKER_SECONDS = 300`; `InMemoryQueue.strand(job_id: str) -> None` (test helper); `Job.stalled: bool = False`.

- [ ] **Step 1: Write the failing memory-backend tests** — append to `tests/test_queue_interface.py`:

```python
async def test_a_stranded_job_holds_its_lock(queue: InMemoryQueue):
    ran: list[int] = []
    queue.register_task(lambda n: ran.append(n), name="jobs.lock")
    first = await queue.enqueue("jobs.lock", {"n": 1}, lock="L", queueing_lock="L")
    queue.strand(first.job_id)  # its worker was SIGKILLed mid-job
    await queue.enqueue("jobs.lock", {"n": 2}, lock="L", queueing_lock="L")
    third = await queue.enqueue("jobs.lock", {"n": 3}, lock="L", queueing_lock="L")
    await queue.run_pending()
    assert third.coalesced is True
    assert ran == []  # the wedge retry_stalled exists to clear


async def test_retry_stalled_requeues_a_stranded_job(queue: InMemoryQueue):
    ran: list[int] = []
    queue.register_task(lambda n: ran.append(n), name="jobs.lock")
    first = await queue.enqueue("jobs.lock", {"n": 1}, lock="L", queueing_lock="L")
    queue.strand(first.job_id)
    assert await queue.retry_stalled("jobs.lock") == 1
    await queue.run_pending()
    assert ran == [1]


async def test_retry_stalled_fails_a_stranded_job_a_waiting_one_covers(queue: InMemoryQueue):
    # Requeueing it would break the waiting job's queueing_lock (Procrastinate's
    # unique index on status='todo'); the waiting job does the same work.
    ran: list[int] = []
    queue.register_task(lambda n: ran.append(n), name="jobs.lock")
    first = await queue.enqueue("jobs.lock", {"n": 1}, lock="L", queueing_lock="L")
    queue.strand(first.job_id)
    await queue.enqueue("jobs.lock", {"n": 2}, lock="L", queueing_lock="L")
    assert await queue.retry_stalled("jobs.lock") == 1
    await queue.run_pending()
    assert ran == [2]
    assert [j.status for j in queue.jobs] == ["failed", "done"]


async def test_retry_stalled_skips_live_jobs_and_other_tasks(queue: InMemoryQueue):
    seen: list[int] = []

    async def handler() -> None:
        seen.append(await queue.retry_stalled("jobs.live"))

    queue.register_task(handler, name="jobs.live")
    queue.register_task(lambda **kw: None, name="jobs.other")
    other = await queue.enqueue("jobs.other", {})
    queue.strand(other.job_id)
    await queue.enqueue("jobs.live", {}, lock="L")
    await queue.run_pending()
    assert seen == [0]  # neither itself (live) nor another task's stranded job
    assert [j.status for j in queue.jobs] == ["running", "done"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_queue_interface.py -q`
Expected: FAIL — `AttributeError: 'InMemoryQueue' object has no attribute 'strand'`.

- [ ] **Step 3: Add the abstract method** to `QueueBackend` in `queue/interface.py`, after `enqueue_batch`:

```python
    @abc.abstractmethod
    async def retry_stalled(self, job_name: str) -> int:
        """Hand ``job_name`` jobs that a dead worker left running back to the
        queue; returns how many were handled.

        A dead worker's job keeps its ``lock`` forever otherwise, so every
        later same-lock job waits behind it. If a waiting job already holds
        the stalled job's ``queueing_lock``, the stalled job is closed as
        failed instead: the waiting job does the same work, and requeueing
        would collide with it.
        """
```

- [ ] **Step 4: Implement the memory backend** (`queue/memory.py`). `Job` gains, after `queueing_lock`:

```python
    #: set by strand(): running on a worker that died (retry_stalled's target)
    stalled: bool = False
```

Add after `enqueue_batch`/`_add_job`:

```python
    async def retry_stalled(self, job_name: str) -> int:
        recovered = 0
        for job in self.jobs:
            if job.name != job_name or not job.stalled:
                continue
            job.stalled = False
            covered = job.queueing_lock is not None and any(
                other.queueing_lock == job.queueing_lock and other.status == "pending"
                for other in self.jobs
            )
            job.status = "failed" if covered else "pending"
            recovered += 1
        return recovered
```

and with the test drivers:

```python
    def strand(self, job_id: str) -> None:
        """Leave a job ``running`` on a worker that died (a SIGKILL mid-job).
        It keeps its lock until :meth:`retry_stalled` recovers it."""
        job = next(j for j in self.jobs if j.id == job_id)
        job.status, job.stalled = "running", True
```

- [ ] **Step 5: Run the memory tests**

Run: `uv run pytest tests/test_queue_interface.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing Procrastinate tests** — append to `tests/test_procrastinate_backend.py`:

```python
class _FakeJobManager:
    """Stands in for app.job_manager: records calls, collides on demand."""

    def __init__(self, stalled, collide=(), other_violation=False):
        self.stalled = stalled
        self.collide = set(collide)
        self.other_violation = other_violation
        self.calls: list[tuple] = []

    async def get_stalled_jobs(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self.stalled

    async def retry_job(self, job):
        from procrastinate.exceptions import UniqueViolation
        from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT

        self.calls.append(("retry", job.id))
        if self.other_violation:
            raise UniqueViolation(constraint_name="some_other_idx", queueing_lock=None)
        if job.id in self.collide:
            raise UniqueViolation(
                constraint_name=QUEUEING_LOCK_CONSTRAINT, queueing_lock=job.queueing_lock
            )

    async def finish_job(self, job, status, delete_job):
        self.calls.append(("finish", job.id, status, delete_job))


def _stalled_job(job_id: int):
    from procrastinate.jobs import Job

    return Job(
        id=job_id,
        queue="default",
        lock="cube:s",
        queueing_lock="cube:s",
        task_name="pipeline.cube_append",
    )


def _wire_manager(queue: ProcrastinateQueue, monkeypatch, manager: _FakeJobManager) -> None:
    async def fake_open():
        pass

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(queue.app, "job_manager", manager)


async def test_retry_stalled_requeues_and_closes_covered_jobs(queue, monkeypatch):
    from procrastinate.jobs import Status

    from pipeline.queue.procrastinate_backend import STALLED_WORKER_SECONDS

    manager = _FakeJobManager([_stalled_job(1), _stalled_job(2)], collide={2})
    _wire_manager(queue, monkeypatch, manager)
    assert await queue.retry_stalled("pipeline.cube_append") == 2
    get_kwargs = {
        "task_name": "pipeline.cube_append",
        "seconds_since_heartbeat": STALLED_WORKER_SECONDS,
    }
    assert manager.calls == [
        ("get", get_kwargs),
        ("retry", 1),
        ("retry", 2),
        ("finish", 2, Status.FAILED, False),
    ]


async def test_retry_stalled_reraises_any_other_unique_violation(queue, monkeypatch):
    from procrastinate.exceptions import UniqueViolation

    _wire_manager(queue, monkeypatch, _FakeJobManager([_stalled_job(1)], other_violation=True))
    with pytest.raises(UniqueViolation):
        await queue.retry_stalled("pipeline.cube_append")


async def test_retry_stalled_with_nothing_stalled_is_a_no_op(queue, monkeypatch):
    manager = _FakeJobManager([])
    _wire_manager(queue, monkeypatch, manager)
    assert await queue.retry_stalled("pipeline.cube_append") == 0
    assert [c[0] for c in manager.calls] == ["get"]
```

- [ ] **Step 7: Run them to see them fail**

Run: `uv run pytest tests/test_procrastinate_backend.py -q`
Expected: FAIL — `TypeError: Can't instantiate abstract class ProcrastinateQueue … retry_stalled` (the fixture), or `ImportError: STALLED_WORKER_SECONDS`.

- [ ] **Step 8: Implement the Procrastinate backend** (`queue/procrastinate_backend.py`).

Imports: extend to `from procrastinate.exceptions import AlreadyEnqueued, UniqueViolation`, and add `from procrastinate.jobs import Status` and `from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT`. After `SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS`:

```python
#: A worker whose heartbeat (every 10 s) is older than this is taken for dead,
#: and its running jobs for stalled. Generous on purpose: a job that blocks
#: the event loop also stops its worker's heartbeat, and requeueing a job that
#: is in fact still running would run it twice under one lock.
STALLED_WORKER_SECONDS = 300
```

After `enqueue_batch`:

```python
    async def retry_stalled(self, job_name: str) -> int:
        await self._ensure_open()
        manager = self.app.job_manager
        # Heartbeat-based: 'doing' jobs whose worker row is gone (pruned) or
        # silent for STALLED_WORKER_SECONDS.
        stalled = await manager.get_stalled_jobs(
            task_name=job_name, seconds_since_heartbeat=STALLED_WORKER_SECONDS
        )
        for job in stalled:
            try:
                await manager.retry_job(job)
                outcome = "requeued"
            except UniqueViolation as exc:
                if exc.constraint_name != QUEUEING_LOCK_CONSTRAINT:
                    raise
                # A job holding the same queueing_lock is already waiting and
                # does the same work; requeueing this one would collide with it.
                await manager.finish_job(job, status=Status.FAILED, delete_job=False)
                outcome = "failed"
            logger.warning(
                "stalled job recovered",
                extra={
                    "job_name": job_name,
                    "job_id": job.id,
                    "lock": job.lock,
                    "outcome": outcome,
                },
            )
        return len(stalled)
```

- [ ] **Step 9: Write the DB-gated test** — append to `tests/test_integration_db.py`:

```python
async def test_retry_stalled_against_real_procrastinate(queue):
    """Decision 14 against real Procrastinate. A 'doing' job with no worker row
    (a dead worker, pruned) is requeued. One whose queueing_lock a waiting job
    already holds is closed as failed. A job on a live worker is left alone."""
    import psycopg

    await queue.setup()

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.locked")
    lone = await queue.enqueue("jobs.locked", {"n": 1}, lock="cube:a", queueing_lock="cube:a")
    covered = await queue.enqueue("jobs.locked", {"n": 2}, lock="cube:b", queueing_lock="cube:b")
    live = await queue.enqueue("jobs.locked", {"n": 3}, lock="cube:c", queueing_lock="cube:c")
    jobs = f'"{SCHEMA}".procrastinate_jobs'
    # Procrastinate's status trigger names its enum type unqualified, so the
    # raw connection needs the queue's search_path, like the backend's pool.
    async with await psycopg.AsyncConnection.connect(
        DATABASE_URL, autocommit=True, options=f"-c search_path={SCHEMA},public"
    ) as conn:
        await conn.execute(
            f"UPDATE {jobs} SET status = 'doing', worker_id = NULL WHERE id = ANY(%s)",
            ([int(lone.job_id), int(covered.job_id)],),
        )
        cur = await conn.execute(
            f'INSERT INTO "{SCHEMA}".procrastinate_workers DEFAULT VALUES RETURNING id'
        )
        worker_id = (await cur.fetchone())[0]
        await conn.execute(
            f"UPDATE {jobs} SET status = 'doing', worker_id = %s WHERE id = %s",
            (worker_id, int(live.job_id)),
        )
    # 'covered' is doing, so it holds no queueing lock: a new wake is accepted.
    waiting = await queue.enqueue("jobs.locked", {"n": 4}, lock="cube:b", queueing_lock="cube:b")
    assert waiting.coalesced is False

    assert await queue.retry_stalled("jobs.locked") == 2
    await queue.aclose()

    by_name = {"lone": lone, "covered": covered, "live": live, "waiting": waiting}
    ids = {int(result.job_id): name for name, result in by_name.items()}
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(
            f"SELECT id, status::text FROM {jobs} WHERE id = ANY(%s)", (list(ids),)
        )
        statuses = {ids[row[0]]: row[1] for row in await cur.fetchall()}
    assert statuses == {"lone": "todo", "covered": "failed", "live": "doing", "waiting": "todo"}
```

- [ ] **Step 10: Run the queue tests and the whole suite**

Run: `uv run pytest tests/test_procrastinate_backend.py tests/test_queue_interface.py -q`, then `uv run pytest -q && uv run ruff check .`
Expected: PASS (the DB-gated test runs in Task 6).

- [ ] **Step 11: Commit**

```bash
git add services/pipeline/src/pipeline/queue services/pipeline/tests/test_queue_interface.py services/pipeline/tests/test_procrastinate_backend.py services/pipeline/tests/test_integration_db.py
git commit -m "Z-3: retry_stalled, so a dead worker's job cannot hold its lock forever

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `cubes/repo.py` — the cube SQL seam

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/repo.py`
- Create: `services/pipeline/tests/_cube_fake.py`
- Test: `services/pipeline/tests/test_integration_cubes_repo.py` (DB-gated), `services/pipeline/tests/test_cube_config.py` (one vocabulary assertion)

**Interfaces:**
- Produces:
  - `REASON_NO_DATETIME = "no_datetime"`
  - `CubeSinkRef(id: str, cube_collection_id: str)` (frozen dataclass)
  - `LedgerEntry(cube_sink_id: str, item_id: str, item_datetime: dt.datetime, status: str = "pending", reason: str | None = None)` (frozen dataclass)
  - `CubeRepo` ABC: `enabled_sinks_for_source(collection_id: str) -> list[CubeSinkRef]`; `record_appends(entries: Sequence[LedgerEntry]) -> int` (rows inserted); `sinks_with_stale_pending(older_than_seconds: int) -> list[str]` (sink ids, sorted); `fail_pending(cube_sink_id: str, reason: str) -> int` (rows failed)
  - `PgCubeRepo(database_url: str)`
  - `tests/_cube_fake.py::FakeCubeRepo` and `FakeSink`, `FakeLedgerRow`

- [ ] **Step 1: Write the vocabulary test** — append to `tests/test_cube_config.py`:

```python
def test_dispatcher_skip_reason_is_in_the_contract():
    from pipeline.cubes.config import SKIP_REASONS
    from pipeline.cubes.repo import REASON_NO_DATETIME

    assert REASON_NO_DATETIME in SKIP_REASONS
```

- [ ] **Step 2: Write the DB-gated repo test** — `tests/test_integration_cubes_repo.py`:

```python
"""PgCubeRepo against a migrated database -- auto-skips unless DATABASE_URL
is set AND migration 032 has been applied.

    DATABASE_URL=postgresql://... uv run pytest tests/test_integration_cubes_repo.py

Every test creates its own sinks (uuid-suffixed collection ids) and deletes
them in teardown (the ledger cascades). The one whole-table read,
``sinks_with_stale_pending``, is asserted only for the sinks the test owns.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


@pytest.fixture
async def db():
    """(raw connection, make_sink) with every sink it made deleted after."""
    import psycopg

    from pipeline.db.pool import close_pools

    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
    cur = await conn.execute("SELECT to_regclass('stac_higher.cube_appends')")
    if (await cur.fetchone())[0] is None:
        await conn.close()
        pytest.skip("migration 032 not applied")
    made: list[str] = []

    async def make_sink(source: str, *, enabled: bool = True) -> str:
        cur = await conn.execute(
            "INSERT INTO stac_higher.cube_sinks"
            " (source_collection_id, cube_collection_id, enabled, config, created_by)"
            " VALUES (%s, %s, %s, '{}'::jsonb, 'itest') RETURNING id::text",
            (source, f"z3-cube-{uuid.uuid4().hex[:8]}", enabled),
        )
        sink_id = (await cur.fetchone())[0]
        made.append(sink_id)
        return sink_id

    yield conn, make_sink
    await conn.execute(
        "DELETE FROM stac_higher.cube_sinks WHERE id = ANY(%s::uuid[])", (made,)
    )
    await conn.close()
    await close_pools()


def _source() -> str:
    return f"z3-src-{uuid.uuid4().hex[:8]}"


async def _rows(conn, sink_id: str) -> list[tuple]:
    cur = await conn.execute(
        "SELECT item_id, item_datetime, status, reason, attempts"
        " FROM stac_higher.cube_appends WHERE cube_sink_id = %s ORDER BY item_id",
        (sink_id,),
    )
    return await cur.fetchall()


async def test_enabled_sinks_for_source_lists_enabled_only(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    source = _source()
    on = await make_sink(source)
    await make_sink(source, enabled=False)
    await make_sink(_source())
    sinks = await PgCubeRepo(DATABASE_URL).enabled_sinks_for_source(source)
    assert [s.id for s in sinks] == [on]


async def test_record_appends_is_idempotent(db):
    from pipeline.cubes.repo import REASON_NO_DATETIME, LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    entries = [
        LedgerEntry(sink, "a", T0),
        LedgerEntry(sink, "a", T0),  # same key twice in one statement
        LedgerEntry(sink, "b", T0, status="skipped", reason=REASON_NO_DATETIME),
    ]
    assert await repo.record_appends(entries) == 2
    assert await repo.record_appends([LedgerEntry(sink, "a", T0)]) == 0  # a replayed PUT
    assert await _rows(conn, sink) == [
        ("a", T0, "pending", None, 0),
        ("b", T0, "skipped", REASON_NO_DATETIME, 0),
    ]


async def test_record_appends_skips_a_sink_deleted_or_disabled_after_the_lookup(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    disabled = await make_sink(_source(), enabled=False)
    gone = str(uuid.uuid4())  # never existed: an FK error would stall the outbox
    inserted = await PgCubeRepo(DATABASE_URL).record_appends(
        [LedgerEntry(disabled, "a", T0), LedgerEntry(gone, "a", T0)]
    )
    assert inserted == 0
    assert await _rows(conn, disabled) == []


async def test_sinks_with_stale_pending(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    stale = await make_sink(_source())
    fresh = await make_sink(_source())
    done = await make_sink(_source())
    off = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [LedgerEntry(s, "a", T0) for s in (stale, fresh, done, off)]
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET created_at = now() - interval '5 minutes'"
        " WHERE cube_sink_id = ANY(%s::uuid[])",
        ([stale, done, off],),
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended' WHERE cube_sink_id = %s",
        (done,),
    )
    await conn.execute(
        "UPDATE stac_higher.cube_sinks SET enabled = false WHERE id = %s", (off,)
    )
    found = set(await repo.sinks_with_stale_pending(120))
    assert stale in found
    assert not found & {fresh, done, off}


async def test_fail_pending_touches_pending_rows_only_and_never_the_sink(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    cur = await conn.execute(
        "SELECT updated_at FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    version = (await cur.fetchone())[0]
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    assert await repo.fail_pending(sink, "not_implemented") == 1
    assert await _rows(conn, sink) == [
        ("a", T0, "failed", "not_implemented", 1),
        ("b", T0, "appended", None, 0),
    ]
    # #98/#99: updated_at is the app's version; the pipeline never writes it.
    cur = await conn.execute(
        "SELECT updated_at FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    assert (await cur.fetchone())[0] == version
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_cube_config.py tests/test_integration_cubes_repo.py -q`
Expected: the vocabulary test FAILS with `ModuleNotFoundError: pipeline.cubes.repo`; the DB file skips (no `DATABASE_URL`; it runs in Task 6).

- [ ] **Step 4: Implement `cubes/repo.py`**

```python
"""Repository seam over the cube sink tables (virtual cube spec §4, §5).

A ``CubeRepo`` ABC the dispatcher and the cube jobs depend on (unit-tested
against ``tests/_cube_fake.py``) plus ``PgCubeRepo`` for production, whose SQL
is exercised by the DB-gated ``test_integration_cubes_repo.py``.

Ownership (ADR 0001): the app owns the DDL (migration 032). The pipeline
reads ``cube_sinks`` and writes ``cube_appends`` rows. It never writes
``cube_sinks.updated_at``: that column is the app's optimistic-lock version
(#98), and Z-3 writes nothing to ``cube_sinks`` at all.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

#: The dispatcher's skip reason for an item with neither ``datetime`` nor
#: ``start_datetime`` (spec §5.2; in ``cube-append-status.json``).
REASON_NO_DATETIME = "no_datetime"


@dataclass(frozen=True)
class CubeSinkRef:
    id: str
    cube_collection_id: str


@dataclass(frozen=True)
class LedgerEntry:
    """One ``cube_appends`` row to insert (``ON CONFLICT DO NOTHING``)."""

    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str = "pending"
    reason: str | None = None


class CubeRepo(abc.ABC):
    @abc.abstractmethod
    async def enabled_sinks_for_source(self, collection_id: str) -> list[CubeSinkRef]:
        """Enabled sinks whose source collection is ``collection_id``."""

    @abc.abstractmethod
    async def record_appends(self, entries: Sequence[LedgerEntry]) -> int:
        """Insert ledger rows, skipping any (sink, item) already present and
        any sink that is gone or disabled. Returns the rows inserted."""

    @abc.abstractmethod
    async def sinks_with_stale_pending(self, older_than_seconds: int) -> list[str]:
        """Ids of enabled sinks holding a ``pending`` row created more than
        ``older_than_seconds`` ago (the §5.3 backstop), sorted."""

    @abc.abstractmethod
    async def fail_pending(self, cube_sink_id: str, reason: str) -> int:
        """Mark every ``pending`` row of the sink ``failed`` with ``reason``
        (the Z-3 stub). Returns the rows changed."""


@dataclass
class PgCubeRepo(CubeRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def enabled_sinks_for_source(  # pragma: no cover
        self, collection_id: str
    ) -> list[CubeSinkRef]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, cube_collection_id FROM stac_higher.cube_sinks"
                " WHERE source_collection_id = %s AND enabled"
                " ORDER BY created_at, id",
                (collection_id,),
            )
            rows = await cur.fetchall()
        return [CubeSinkRef(id=r[0], cube_collection_id=r[1]) for r in rows]

    async def record_appends(  # pragma: no cover
        self, entries: Sequence[LedgerEntry]
    ) -> int:
        if not entries:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO stac_higher.cube_appends"
                " (cube_sink_id, item_id, item_datetime, status, reason)"
                " SELECT u.sink, u.item, u.at, u.status, u.reason"
                "   FROM unnest(%s::uuid[], %s::text[], %s::timestamptz[],"
                "               %s::text[], %s::text[])"
                "        AS u(sink, item, at, status, reason)"
                # A sink deleted or disabled since the lookup is skipped, not
                # an FK error that would leave the whole claim unprocessed.
                "   JOIN stac_higher.cube_sinks s ON s.id = u.sink AND s.enabled"
                " ON CONFLICT (cube_sink_id, item_id) DO NOTHING",
                (
                    [e.cube_sink_id for e in entries],
                    [e.item_id for e in entries],
                    [e.item_datetime for e in entries],
                    [e.status for e in entries],
                    [e.reason for e in entries],
                ),
            )
            inserted = cur.rowcount
            await conn.commit()
        return inserted

    async def sinks_with_stale_pending(  # pragma: no cover
        self, older_than_seconds: int
    ) -> list[str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT a.cube_sink_id::text"
                "  FROM stac_higher.cube_appends a"
                "  JOIN stac_higher.cube_sinks s ON s.id = a.cube_sink_id"
                " WHERE s.enabled AND a.status = 'pending'"
                "   AND a.created_at < now() - make_interval(secs => %s)"
                " ORDER BY 1",
                (older_than_seconds,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def fail_pending(  # pragma: no cover
        self, cube_sink_id: str, reason: str
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends"
                " SET status = 'failed', reason = %s,"
                "     attempts = attempts + 1, updated_at = now()"
                " WHERE cube_sink_id = %s AND status = 'pending'",
                (reason, cube_sink_id),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed
```

- [ ] **Step 5: Write the fake** — `tests/_cube_fake.py`:

```python
"""In-memory CubeRepo for dispatcher and cube-job unit tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from pipeline.cubes.repo import CubeRepo, CubeSinkRef, LedgerEntry


@dataclass
class FakeSink:
    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool = True


@dataclass
class FakeLedgerRow:
    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str
    reason: str | None
    attempts: int = 0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


@dataclass
class FakeCubeRepo(CubeRepo):
    sinks: list[FakeSink] = field(default_factory=list)
    #: (cube_sink_id, item_id) -> row, mirroring UNIQUE (cube_sink_id, item_id)
    ledger: dict[tuple[str, str], FakeLedgerRow] = field(default_factory=dict)
    #: enabled_sinks_for_source calls (asserts the per-batch cache)
    sink_calls: int = 0
    #: raise from enabled_sinks_for_source / record_appends when set
    lookup_error: Exception | None = None
    record_error: Exception | None = None

    async def enabled_sinks_for_source(self, collection_id: str) -> list[CubeSinkRef]:
        self.sink_calls += 1
        if self.lookup_error is not None:
            raise self.lookup_error
        return [
            CubeSinkRef(id=s.id, cube_collection_id=s.cube_collection_id)
            for s in self.sinks
            if s.source_collection_id == collection_id and s.enabled
        ]

    async def record_appends(self, entries: Sequence[LedgerEntry]) -> int:
        if self.record_error is not None:
            raise self.record_error
        live = {s.id for s in self.sinks if s.enabled}
        inserted = 0
        for e in entries:
            key = (e.cube_sink_id, e.item_id)
            if e.cube_sink_id not in live or key in self.ledger:
                continue
            self.ledger[key] = FakeLedgerRow(
                e.cube_sink_id, e.item_id, e.item_datetime, e.status, e.reason
            )
            inserted += 1
        return inserted

    async def sinks_with_stale_pending(self, older_than_seconds: int) -> list[str]:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=older_than_seconds)
        live = {s.id for s in self.sinks if s.enabled}
        return sorted(
            {
                r.cube_sink_id
                for r in self.ledger.values()
                if r.status == "pending" and r.created_at < cutoff and r.cube_sink_id in live
            }
        )

    async def fail_pending(self, cube_sink_id: str, reason: str) -> int:
        changed = 0
        for r in self.ledger.values():
            if r.cube_sink_id == cube_sink_id and r.status == "pending":
                r.status, r.reason = "failed", reason
                r.attempts += 1
                changed += 1
        return changed

    def backdate(self, cube_sink_id: str, item_id: str, seconds: int) -> None:
        """Test helper: age one ledger row's created_at."""
        row = self.ledger[(cube_sink_id, item_id)]
        row.created_at -= dt.timedelta(seconds=seconds)

    def rows(self, cube_sink_id: str) -> list[FakeLedgerRow]:
        return sorted(
            (r for r in self.ledger.values() if r.cube_sink_id == cube_sink_id),
            key=lambda r: r.item_id,
        )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_cube_config.py tests/test_integration_cubes_repo.py -q && uv run ruff check .`
Expected: PASS (DB file skipped).

- [ ] **Step 7: Commit**

```bash
git add services/pipeline/src/pipeline/cubes/repo.py services/pipeline/tests/_cube_fake.py services/pipeline/tests/test_cube_config.py services/pipeline/tests/test_integration_cubes_repo.py
git commit -m "Z-3: cube repo seam (sinks lookup, ledger insert, stale pending, stub fail)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `jobs/cubes.py` — lock helper, `cube_kick`, the `cube_append` stub

**Files:**
- Create: `services/pipeline/src/pipeline/jobs/cubes.py`
- Modify: `services/pipeline/src/pipeline/main.py` (import + `cubes.register(queue, settings)`)
- Test: `services/pipeline/tests/test_cube_jobs.py` (new), `services/pipeline/tests/test_main_jobs.py`

**Interfaces:**
- Consumes: `Enqueued`, `QueueBackend.enqueue(..., lock=, queueing_lock=)` (Task 1); `QueueBackend.retry_stalled`, `InMemoryQueue.strand` (Task 2); `CubeRepo`, `PgCubeRepo` (Task 3).
- Produces: `JOB_CUBE_APPEND = "pipeline.cube_append"`, `JOB_CUBE_KICK = "pipeline.cube_kick"`, `KICK_CRON = "*/5 * * * *"`, `KICK_STALE_SECONDS = 120`, `REASON_NOT_IMPLEMENTED = "not_implemented"`; `cube_lock(cube_sink_id: str) -> str`; `async enqueue_cube_append(queue: QueueBackend, cube_sink_id: str) -> Enqueued`; `cube_append_enqueuer(queue: QueueBackend) -> Callable[[list[str]], Awaitable[None]]`; `async kick_stale_sinks(repo: CubeRepo, queue: QueueBackend) -> int`; `register(queue: QueueBackend, settings: Settings, *, repo: CubeRepo | None = None) -> None`.

- [ ] **Step 1: Write the failing tests** — `tests/test_cube_jobs.py`:

```python
"""Z-3 cube jobs: the per-sink lock, the cube_kick backstop, the stub."""

import datetime as dt

import pytest

from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from pipeline.config import Settings
from pipeline.cubes.repo import LedgerEntry
from pipeline.jobs.cubes import (
    JOB_CUBE_APPEND,
    JOB_CUBE_KICK,
    KICK_CRON,
    KICK_STALE_SECONDS,
    REASON_NOT_IMPLEMENTED,
    cube_append_enqueuer,
    cube_lock,
    enqueue_cube_append,
    register,
)
from pipeline.queue.interface import QUEUE_DEFAULT, Enqueued
from pipeline.queue.memory import InMemoryQueue

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


@pytest.fixture
def repo() -> FakeCubeRepo:
    return FakeCubeRepo(
        sinks=[
            FakeSink("s1", "src", "cube1"),
            FakeSink("s2", "src", "cube2"),
            FakeSink("off", "src", "cube3", enabled=False),
        ]
    )


@pytest.fixture
def queue(repo: FakeCubeRepo) -> InMemoryQueue:
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)
    return q


def test_registration(queue: InMemoryQueue):
    assert queue.queues[JOB_CUBE_APPEND] == QUEUE_DEFAULT
    assert queue.periodic[JOB_CUBE_KICK].cron == KICK_CRON == "*/5 * * * *"
    assert KICK_STALE_SECONDS == 120


def test_cube_lock_is_per_sink():
    assert cube_lock("abc") == "cube:abc"


async def test_enqueue_cube_append_takes_both_locks(queue: InMemoryQueue):
    result = await enqueue_cube_append(queue, "s1")
    assert result == Enqueued(job_id="1")
    job = queue.jobs[0]
    assert (job.name, job.payload) == (JOB_CUBE_APPEND, {"cube_sink_id": "s1"})
    assert job.lock == job.queueing_lock == "cube:s1"


async def test_a_second_enqueue_for_a_waiting_sink_coalesces(queue: InMemoryQueue):
    await enqueue_cube_append(queue, "s1")
    again = await enqueue_cube_append(queue, "s1")
    other = await enqueue_cube_append(queue, "s2")
    assert again.coalesced is True
    assert other.coalesced is False
    assert len(queue.jobs) == 2


async def test_enqueuer_enqueues_each_sink(queue: InMemoryQueue):
    await cube_append_enqueuer(queue)(["s1", "s2", "s1"])
    assert [j.payload["cube_sink_id"] for j in queue.jobs] == ["s1", "s2"]


async def test_kick_re_enqueues_only_stale_pending_sinks(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends(
        [
            LedgerEntry("s1", "old", T0),
            LedgerEntry("s2", "new", T0),
        ]
    )
    # record_appends refuses a disabled sink (like the real JOIN): plant it.
    repo.ledger[("off", "old")] = FakeLedgerRow("off", "old", T0, "pending", None)
    repo.backdate("s1", "old", KICK_STALE_SECONDS + 1)
    repo.backdate("off", "old", KICK_STALE_SECONDS + 1)  # disabled: never kicked

    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)

    assert [(j.name, j.payload) for j in queue.jobs] == [
        (JOB_CUBE_APPEND, {"cube_sink_id": "s1"})
    ]


async def test_kick_skips_terminal_rows(queue: InMemoryQueue, repo: FakeCubeRepo):
    await repo.record_appends([LedgerEntry("s1", "a", T0, status="skipped", reason="late")])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert queue.jobs == []


async def test_kick_against_a_waiting_job_coalesces(queue: InMemoryQueue, repo: FakeCubeRepo):
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    await enqueue_cube_append(queue, "s1")
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert len(queue.jobs) == 1


async def test_kick_recovers_a_stranded_append_a_waiting_job_covers(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    repo.backdate("s1", "a", KICK_STALE_SECONDS + 1)
    stranded = await enqueue_cube_append(queue, "s1")
    queue.strand(stranded.job_id)  # its worker was SIGKILLed: holds cube:s1
    await enqueue_cube_append(queue, "s1")  # a later wake: waiting, blocked
    await queue.run_pending()
    assert repo.rows("s1")[0].status == "pending"  # wedged

    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    await queue.run_pending()
    assert [j.status for j in queue.jobs] == ["failed", "done"]
    assert repo.rows("s1")[0].status == "failed"  # the stub ran: unwedged


async def test_kick_requeues_a_stranded_append_with_nothing_waiting(queue: InMemoryQueue):
    stranded = await enqueue_cube_append(queue, "s1")
    queue.strand(stranded.job_id)
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    assert [j.status for j in queue.jobs] == ["pending"]


async def test_stub_marks_pending_rows_failed_not_implemented(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    await repo.record_appends(
        [
            LedgerEntry("s1", "a", T0),
            LedgerEntry("s1", "b", T0, status="skipped", reason="no_datetime"),
            LedgerEntry("s2", "c", T0),
        ]
    )
    await enqueue_cube_append(queue, "s1")
    await queue.run_pending()
    assert [(r.item_id, r.status, r.reason, r.attempts) for r in repo.rows("s1")] == [
        ("a", "failed", REASON_NOT_IMPLEMENTED, 1),
        ("b", "skipped", "no_datetime", 0),
    ]
    assert repo.rows("s2")[0].status == "pending"  # another sink's rows untouched
```

In `tests/test_main_jobs.py`, add the import and an assertion to `test_build_queue_registers_all_periodic_jobs`:

```python
from pipeline.jobs.cubes import JOB_CUBE_APPEND, JOB_CUBE_KICK
...
    # Z-3: the cube append job (a stub until Z-4) and its 5-minute backstop.
    assert {JOB_CUBE_APPEND, JOB_CUBE_KICK} <= registered
    assert queue.app.tasks[JOB_CUBE_APPEND].queue == "default"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_jobs.py tests/test_main_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.jobs.cubes'`.

- [ ] **Step 3: Implement `jobs/cubes.py`**

```python
"""Virtual cube sink jobs (virtual cube spec §5; ADR 0022).

One writer per cube repository: every ``pipeline.cube_append`` is deferred
with ``lock`` and ``queueing_lock`` = ``cube:{sink_id}``. The lock keeps two
appends to one repository from ever running together; the queueing lock
coalesces a burst of wake-ups into the one job that is already waiting, which
drains every pending ledger row when it runs (§5.1).

- The dispatcher (``dispatcher/loop.py``) writes the ledger rows and wakes the
  sink through :func:`cube_append_enqueuer` (§5.2).
- ``pipeline.cube_kick`` (every 5 minutes) first hands any ``cube_append`` a
  dead worker left running back to the queue (``QueueBackend.retry_stalled``),
  since it would hold its sink's lock forever. It then re-enqueues sinks
  holding ``pending`` rows older than 2 minutes, recovering a job lost between
  the ledger insert and the enqueue (§5.3).
- ``pipeline.cube_append`` is a **Z-3 stub**: it marks the sink's pending rows
  ``failed`` with reason ``not_implemented``. Z-4 (#90) replaces it with the
  real append. It writes nothing to ``cube_sinks``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.cubes.repo import CubeRepo, PgCubeRepo
from pipeline.queue.interface import Enqueued, QueueBackend

logger = logging.getLogger(__name__)

JOB_CUBE_APPEND = "pipeline.cube_append"
JOB_CUBE_KICK = "pipeline.cube_kick"
KICK_CRON = "*/5 * * * *"
#: §5.3: a pending row this old with no job in sight was probably stranded.
KICK_STALE_SECONDS = 120
#: The stub's failure reason (free text; failed reasons are not a closed set).
REASON_NOT_IMPLEMENTED = "not_implemented"


def cube_lock(cube_sink_id: str) -> str:
    """The sink's ``lock`` and ``queueing_lock`` (spec §2)."""
    return f"cube:{cube_sink_id}"


async def enqueue_cube_append(queue: QueueBackend, cube_sink_id: str) -> Enqueued:
    """Wake the sink's single writer. A coalesced result means a job is
    already waiting and will read the new ledger rows: success, not retry."""
    lock = cube_lock(cube_sink_id)
    return await queue.enqueue(
        JOB_CUBE_APPEND,
        {"cube_sink_id": cube_sink_id},
        lock=lock,
        queueing_lock=lock,
    )


def cube_append_enqueuer(queue: QueueBackend) -> Callable[[list[str]], Awaitable[None]]:
    """The dispatcher's callback: one ``cube_append`` per distinct sink."""

    async def _enqueue(cube_sink_ids: list[str]) -> None:
        for cube_sink_id in dict.fromkeys(cube_sink_ids):
            result = await enqueue_cube_append(queue, cube_sink_id)
            if result.coalesced:
                logger.debug(
                    "cube_append already waiting; coalesced",
                    extra={"cube_sink_id": cube_sink_id},
                )

    return _enqueue


async def kick_stale_sinks(repo: CubeRepo, queue: QueueBackend) -> int:
    """§5.3 backstop: re-enqueue every enabled sink with a stale pending row.
    Returns how many sinks were kicked (coalesced or not).

    First, any ``cube_append`` a dead worker left running goes back to the
    queue (plan decision 14). Until then it holds its sink's lock, and the
    enqueue below would only coalesce into a job that can never start."""
    await queue.retry_stalled(JOB_CUBE_APPEND)
    cube_sink_ids = await repo.sinks_with_stale_pending(KICK_STALE_SECONDS)
    await cube_append_enqueuer(queue)(cube_sink_ids)
    return len(cube_sink_ids)


def register(
    queue: QueueBackend, settings: Settings, *, repo: CubeRepo | None = None
) -> None:
    def _repo() -> CubeRepo:
        return repo if repo is not None else PgCubeRepo(settings.database_url)

    async def cube_append(cube_sink_id: str) -> None:
        # Z-3 stub (spec §15): Z-4 (#90) replaces this with the real append.
        failed = await _repo().fail_pending(cube_sink_id, REASON_NOT_IMPLEMENTED)
        logger.warning(
            "cube_append is not implemented yet; pending rows marked failed",
            extra={
                "cube_sink_id": cube_sink_id,
                "rows": failed,
                "reason": REASON_NOT_IMPLEMENTED,
            },
        )

    async def cube_kick(timestamp: int) -> None:
        kicked = await kick_stale_sinks(_repo(), queue)
        if kicked:
            logger.info(
                "cube_kick re-enqueued sinks with stale pending rows",
                extra={"sinks": kicked, "scheduled_timestamp": timestamp},
            )

    # Default queue: the append reads headers, not bytes (spec §6). Retry is
    # Z-4's (§6: 3 attempts); the stub cannot fail usefully.
    queue.register_task(cube_append, name=JOB_CUBE_APPEND)
    queue.register_periodic(cube_kick, name=JOB_CUBE_KICK, cron=KICK_CRON)
```

- [ ] **Step 4: Register in `main.py`** — add `cubes,` to the `from pipeline.jobs import (...)` list (alphabetical, after `backfill`), and after `pgstac_drain.register(queue, settings)`:

```python
    # Z-3 (virtual cube spec §5, ADR 0022): the per-sink cube_append (a stub
    # until Z-4) and the 5-minute cube_kick backstop. The dispatcher's
    # insert-only matching wakes the sinks.
    cubes.register(queue, settings)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cube_jobs.py tests/test_main_jobs.py -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/cubes.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_cube_jobs.py services/pipeline/tests/test_main_jobs.py
git commit -m "Z-3: cube_append stub and the cube_kick backstop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Dispatcher matching — insert-only, ledger before drain

**Files:**
- Modify: `services/pipeline/src/pipeline/dispatcher/loop.py`
- Modify: `services/pipeline/src/pipeline/jobs/dispatch.py`
- Test: `services/pipeline/tests/test_dispatch_cubes.py` (new)

**Interfaces:**
- Consumes: `CubeRepo`, `LedgerEntry`, `REASON_NO_DATETIME` (Task 3); `cube_append_enqueuer`, `JOB_CUBE_APPEND` (Task 4).
- Produces: `dispatch_once(..., cube_repo: CubeRepo | None = None, enqueue_cube_appends: EnqueueCubeAppends | None = None)` and the same two kwargs on `dispatch_until_empty`; `EnqueueCubeAppends = Callable[[list[str]], Awaitable[None]]`; `DispatchResult.cube_rows: int` and `DispatchResult.cube_sinks: int`.

- [ ] **Step 1: Write the failing tests** — `tests/test_dispatch_cubes.py`:

```python
"""Z-3 dispatcher matching (virtual cube spec §5.2)."""

import datetime as dt

import pytest

from _cube_fake import FakeCubeRepo, FakeSink
from _dispatch_fake import FakeDispatchRepo
from pipeline.config import Settings
from pipeline.cubes.repo import REASON_NO_DATETIME
from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import dispatch_once, dispatch_until_empty
from pipeline.dispatcher.repo import ItemEvent
from pipeline.jobs.cubes import JOB_CUBE_APPEND, cube_append_enqueuer, enqueue_cube_append
from pipeline.jobs.cubes import register as register_cube_jobs
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio

T0 = dt.datetime(2026, 10, 3, 17, 2, 36, 714359, tzinfo=dt.UTC)


def _item(item_id, collection="src", **properties):
    properties.setdefault("datetime", "2026-10-03T17:02:36.714359936Z")
    return {
        "id": item_id,
        "collection": collection,
        "properties": properties,
        "assets": {"data": {"href": f"s3://noaa-goes19/{item_id}.nc"}},
    }


def _event(event_id, item_id, op="insert", collection="src"):
    return ItemEvent(
        id=event_id,
        collection_id=collection,
        item_id=item_id,
        op=op,
        occurred_at=dt.datetime(2026, 10, 3, 18, 0, tzinfo=dt.UTC),
    )


async def _noop(*_args):
    return None


class Harness:
    """A dispatch repo + cube repo + in-memory queue with the real cube jobs."""

    def __init__(self, sinks=None):
        self.repo = FakeDispatchRepo()
        self.cubes = FakeCubeRepo(
            sinks=sinks
            if sinks is not None
            else [FakeSink("s1", "src", "cube1"), FakeSink("other", "elsewhere", "cube2")]
        )
        self.queue = InMemoryQueue()
        register_cube_jobs(self.queue, Settings.from_env(env={}), repo=self.cubes)
        self.deliveries: list[list[dict]] = []
        self.finalizes: list[dict] = []

    def add(self, event, item=None):
        self.repo.events.append(event)
        if item is not None:
            self.repo.items[(event.collection_id, event.item_id)] = item

    def deliver_to(self, association_id="d1"):
        # match_item needs a parseable config: path_template is required.
        self.repo.associations["src"] = [
            DeliverAssociation(
                id=association_id, collection_id="src", config={"path_template": "{filename}"}
            )
        ]

    async def _deliver(self, batches):
        self.deliveries.append(batches)

    async def _finalize(self, payloads):
        self.finalizes.extend(payloads)

    async def dispatch(self):
        return await dispatch_once(
            self.repo,
            self._deliver,
            enqueue_finalize=self._finalize,
            mark_delete_gc=_noop,
            cube_repo=self.cubes,
            enqueue_cube_appends=cube_append_enqueuer(self.queue),
        )

    def cube_jobs(self):
        return [j.payload["cube_sink_id"] for j in self.queue.jobs if j.name == JOB_CUBE_APPEND]


async def test_insert_on_a_source_writes_a_pending_row_and_wakes_the_sink():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    result = await h.dispatch()
    [row] = h.cubes.rows("s1")
    assert (row.item_id, row.item_datetime, row.status, row.reason) == ("a", T0, "pending", None)
    assert h.cube_jobs() == ["s1"]
    job = h.queue.jobs[0]
    assert job.lock == job.queueing_lock == "cube:s1"
    assert h.repo.processed == [1]
    assert (result.cube_rows, result.cube_sinks) == (1, 1)


async def test_update_and_delete_events_never_match():
    h = Harness()
    h.add(_event(1, "a", op="update"), _item("a"))
    h.add(_event(2, "b", op="delete"))
    await h.dispatch()
    assert h.cubes.ledger == {}
    assert h.cube_jobs() == []
    assert sorted(h.repo.processed) == [1, 2]


async def test_a_put_delete_plus_insert_writes_one_row():
    h = Harness()
    h.add(_event(1, "a", op="delete"), _item("a"))  # the item is live: replace pair
    h.add(_event(2, "a", op="insert"))
    await h.dispatch()
    # The same PUT replayed later (another delete + insert) adds nothing.
    h.add(_event(3, "a", op="delete"))
    h.add(_event(4, "a", op="insert"))
    await h.dispatch()
    assert [r.item_id for r in h.cubes.rows("s1")] == ["a"]
    assert sorted(h.repo.processed) == [1, 2, 3, 4]


async def test_a_coalesced_enqueue_still_drains_the_event():
    h = Harness()
    await enqueue_cube_append(h.queue, "s1")  # a job is already waiting
    h.add(_event(1, "a"), _item("a"))
    await h.dispatch()
    assert h.cube_jobs() == ["s1"]  # no second job
    assert h.cubes.rows("s1")[0].status == "pending"  # the waiting job will read it
    assert h.repo.processed == [1]


async def test_a_sink_on_another_collection_is_untouched():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await h.dispatch()
    assert h.cubes.rows("other") == []
    assert "other" not in h.cube_jobs()


async def test_no_datetime_is_skipped_and_wakes_nothing():
    h = Harness()
    item = _item("a")
    item["properties"] = {"datetime": None}
    h.add(_event(1, "a"), item)
    await h.dispatch()
    [row] = h.cubes.rows("s1")
    assert (row.status, row.reason) == ("skipped", REASON_NO_DATETIME)
    assert row.item_datetime == dt.datetime(2026, 10, 3, 18, 0, tzinfo=dt.UTC)  # occurred_at
    assert h.cube_jobs() == []


async def test_start_datetime_is_the_fallback():
    h = Harness()
    item = _item("a", datetime=None, start_datetime="2026-10-03T17:00:00Z")
    h.add(_event(1, "a"), item)
    await h.dispatch()
    assert h.cubes.rows("s1")[0].item_datetime == dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


async def test_an_unparseable_datetime_counts_as_missing():
    h = Harness()
    h.add(_event(1, "a"), _item("a", datetime="yesterday"))
    await h.dispatch()
    assert h.cubes.rows("s1")[0].reason == REASON_NO_DATETIME


async def test_nanosecond_datetime_lands_as_microseconds():
    h = Harness()
    h.add(_event(1, "a"), _item("a", datetime="2026-10-03T17:02:36.714359936Z"))
    await h.dispatch()
    assert h.cubes.rows("s1")[0].item_datetime == T0


async def test_one_job_per_sink_however_many_items():
    h = Harness(sinks=[FakeSink("s1", "src", "cube1"), FakeSink("s2", "src", "cube2")])
    for n in range(50):
        h.add(_event(n + 1, f"i{n}"), _item(f"i{n}"))
    result = await h.dispatch()
    assert sorted(h.cube_jobs()) == ["s1", "s2"]
    assert len(h.cubes.rows("s1")) == len(h.cubes.rows("s2")) == 50
    assert h.cubes.sink_calls == 1  # cached per collection per batch
    assert (result.cube_rows, result.cube_sinks) == (100, 2)


async def test_a_staged_insert_does_not_match():
    h = Harness()
    item = _item("a")
    item["assets"] = {"data": {"href": "staging://up1/scene.tif"}}
    h.add(_event(1, "a"), item)
    await h.dispatch()
    # Decision 13: the staged gate routes it to finalize, whose upsert emits
    # an update, which never feeds a cube. Asserting the finalize proves it
    # took the gate, not the poison-drain.
    assert [p["item_id"] for p in h.finalizes] == ["a"]
    assert h.cubes.ledger == {}


async def test_a_sink_lookup_failure_keeps_the_items_deliveries():
    h = Harness()
    h.cubes.lookup_error = RuntimeError('relation "stac_higher.cube_sinks" does not exist')
    h.deliver_to("d1")
    h.add(_event(1, "a"), _item("a"))
    h.add(_event(2, "b"), _item("b"))
    await h.dispatch()
    assert [b["association_id"] for b in h.deliveries[0]] == ["d1"]
    assert [i["item_id"] for i in h.deliveries[0][0]["items"]] == ["a", "b"]
    assert h.cubes.sink_calls == 1  # the failure is cached for the claim (decision 7)
    assert h.cubes.ledger == {}
    assert h.repo.processed == [1, 2]


async def test_a_ledger_insert_failure_leaves_the_claim_for_a_redrive():
    h = Harness()
    h.cubes.record_error = RuntimeError("db down")
    h.deliver_to("d1")
    h.add(_event(1, "a"), _item("a"))
    with pytest.raises(RuntimeError):
        await h.dispatch()
    assert h.repo.processed == []
    # Decision 6: the cube step runs first. The association matches, so an
    # empty list here means nothing else was queued yet.
    assert h.deliveries == []
    assert h.cube_jobs() == []
    # The redrive (the next wake) delivers once the insert works.
    h.cubes.record_error = None
    h.repo.claimed.clear()
    await h.dispatch()
    assert [b["association_id"] for b in h.deliveries[0]] == ["d1"]
    assert h.cube_jobs() == ["s1"]
    assert h.repo.processed == [1]


async def test_without_a_cube_repo_nothing_changes():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await dispatch_once(h.repo, h._deliver, enqueue_finalize=_noop, mark_delete_gc=_noop)
    assert h.cubes.ledger == {}
    assert h.repo.processed == [1]


async def test_dispatch_until_empty_passes_the_cube_hooks_through():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await dispatch_until_empty(
        h.repo,
        h._deliver,
        enqueue_finalize=_noop,
        mark_delete_gc=_noop,
        cube_repo=h.cubes,
        enqueue_cube_appends=cube_append_enqueuer(h.queue),
    )
    assert h.cube_jobs() == ["s1"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_dispatch_cubes.py -q`
Expected: FAIL — `dispatch_once() got an unexpected keyword argument 'cube_repo'`.

- [ ] **Step 3: Implement the loop changes** (`dispatcher/loop.py`).

Imports: add `import datetime as dt` and `from pipeline.cubes.repo import REASON_NO_DATETIME, CubeRepo, CubeSinkRef, LedgerEntry`.

Add after `EnqueueProcessRuns`:

```python
#: Z-3 (virtual cube spec §5.2): wake each listed cube sink's single writer.
#: Wired to ``jobs.cubes.cube_append_enqueuer`` (per-sink lock + queueing
#: lock; a coalesced enqueue is success).
EnqueueCubeAppends = Callable[[list[str]], Awaitable[None]]
```

`DispatchResult` gains:

```python
    #: cube ledger rows offered this pass, and sinks woken (Z-3).
    cube_rows: int = 0
    cube_sinks: int = 0
```

Add a helper after `_first_staged_href`:

```python
def _cube_item_datetime(item: dict[str, Any]) -> dt.datetime | None:
    """The item's time for the cube ledger (spec §5.2): ``datetime``, else
    ``start_datetime``. A value that does not parse counts as missing.
    Nanosecond strings truncate to microseconds (Postgres precision)."""
    properties = item.get("properties") or {}
    for key in ("datetime", "start_datetime"):
        value = properties.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)
    return None
```

`dispatch_once` signature gains, after `enqueue_process_runs`:

```python
    cube_repo: CubeRepo | None = None,
    enqueue_cube_appends: EnqueueCubeAppends | None = None,
```

Docstring: add a paragraph:

```
    Cube sinks (Z-3, virtual cube spec §5.2): an ``insert`` event on a
    collection with enabled sinks queues one ledger row per sink, written with
    ``ON CONFLICT DO NOTHING`` and followed by one ``cube_append`` per sink
    with a pending row, all before the drain. The cube step runs first among
    the post-loop writes because it is fully idempotent.
```

Before the `for event in events:` loop, add:

```python
    # Z-3: enabled cube sinks per source collection, cached per batch like
    # process sources; ledger rows keyed (sink, item) so a replace pair or a
    # repeated insert in one claim offers one row.
    sink_cache: dict[str, list[CubeSinkRef]] = {}
    cube_entries: dict[tuple[str, str], LedgerEntry] = {}
    # Cube matching runs only with both hooks wired (tests may pass neither).
    cubes = cube_repo if enqueue_cube_appends is not None else None
```

Inside the per-event `try`, after `matches.extend(item_matches)`:

```python
            # Z-3 (spec §5.2): only an INSERT feeds a cube; update and delete
            # never do (ADR 0022). Its own try: a lookup failure must not
            # mislabel the event as dead-lettered when its deliveries and
            # process runs are already batched. The event still drains and
            # this step is missing from the cube (decision 7 in the plan).
            if cubes is not None and event.op == "insert":
                if event.collection_id not in sink_cache:
                    try:
                        sink_cache[event.collection_id] = (
                            await cubes.enabled_sinks_for_source(event.collection_id)
                        )
                    except Exception:
                        # Cached as "no sinks" for the rest of the claim: a
                        # missing table costs one query and one traceback per
                        # claim, not one per event.
                        sink_cache[event.collection_id] = []
                        logger.exception(
                            "dispatch: cube sink lookup failed; this claim's items"
                            " from the collection are not queued for their cubes",
                            extra={
                                "event_id": event.id,
                                "collection_id": event.collection_id,
                                "item_id": event.item_id,
                            },
                        )
                sinks = sink_cache[event.collection_id]
                if sinks:
                    when = _cube_item_datetime(item)
                    for sink in sinks:
                        cube_entries.setdefault(
                            (sink.id, event.item_id),
                            LedgerEntry(
                                cube_sink_id=sink.id,
                                item_id=event.item_id,
                                item_datetime=when
                                or event.occurred_at
                                or dt.datetime.now(dt.UTC),
                                status="pending" if when is not None else "skipped",
                                reason=None if when is not None else REASON_NO_DATETIME,
                            ),
                        )
```

After the loop, **before** `if finalizes:`:

```python
    # Z-3: ledger rows, then one cube_append per sink with a pending row,
    # before the drain (spec §5.2). First among the post-loop writes: both are
    # idempotent (ON CONFLICT DO NOTHING, queueing_lock), so a raise here has
    # queued nothing for this claim yet, and the redrive repeats harmlessly.
    cube_sink_ids: list[str] = []
    if cube_entries and cubes is not None and enqueue_cube_appends is not None:
        entries = list(cube_entries.values())
        await cubes.record_appends(entries)
        cube_sink_ids = list(
            dict.fromkeys(e.cube_sink_id for e in entries if e.status == "pending")
        )
        if cube_sink_ids:
            await enqueue_cube_appends(cube_sink_ids)
```

and return:

```python
    return DispatchResult(
        claimed=len(events),
        matches=matches,
        finalizes=len(finalizes),
        process_runs=len(process_batches),
        cube_rows=len(cube_entries),
        cube_sinks=len(cube_sink_ids),
    )
```

`dispatch_until_empty` gains the same two kwargs and passes them to `dispatch_once`.

Update the module docstring with one paragraph:

```
Cube sinks (Z-3, virtual cube spec §5.2): an ``insert`` event on a source
collection with enabled cube sinks writes one ``cube_appends`` row per sink
(``ON CONFLICT DO NOTHING``; a PUT's delete + insert yields one row) and wakes
each sink's single writer under its ``cube:{id}`` lock before the drain.
Update and delete events never feed a cube.
```

- [ ] **Step 4: Wire production** (`jobs/dispatch.py`).

Imports: `from pipeline.cubes.repo import PgCubeRepo` and `from pipeline.jobs.cubes import cube_append_enqueuer`. In `run_dispatch`, after `gc_repo = ...`:

```python
        cube_repo = PgCubeRepo(settings.database_url)
```

and pass to `dispatch_until_empty`:

```python
            cube_repo=cube_repo,
            enqueue_cube_appends=cube_append_enqueuer(queue),
```

Add one line to the module docstring after the staged/delete sentence: "Insert events on a cube sink's source collection also write `cube_appends` rows and wake `pipeline.cube_append` (Z-3, `jobs/cubes.py`)."

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_dispatch_cubes.py tests/test_dispatch_loop.py tests/test_dispatch_listener.py -q && uv run ruff check .`
Expected: PASS, and the existing dispatcher tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/dispatcher/loop.py services/pipeline/src/pipeline/jobs/dispatch.py services/pipeline/tests/test_dispatch_cubes.py
git commit -m "Z-3: dispatcher matches inserts to cube sinks, ledger before drain

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, real-DB check, gates, PR

**Files:**
- Modify: `services/pipeline/README.md` (the dispatch paragraph, around line 232)
- Modify: `docs/FEATURES.md` (Z table, after the Z-2 row)

- [ ] **Step 1: README** — after the `- **dispatch** …` bullet in `services/pipeline/README.md`, add:

```markdown
- **cube sinks** (`jobs/cubes.py`, `cubes/repo.py`; virtual cube spec §5) —
  an `insert` event on a collection with enabled cube sinks writes one
  `cube_appends` ledger row per sink (`ON CONFLICT DO NOTHING`; no datetime →
  `skipped: no_datetime`) and enqueues `pipeline.cube_append {cube_sink_id}`
  with `lock` = `queueing_lock` = `cube:{id}`, before the event drains. A
  second enqueue while one is waiting comes back `Enqueued(coalesced=True)`,
  never an exception. `pipeline.cube_kick` (`*/5 * * * *`) first requeues any
  `cube_append` a dead worker left `doing` (`QueueBackend.retry_stalled`;
  heartbeat silent 300 s), which would otherwise hold its sink's lock forever,
  then re-enqueues sinks with `pending` rows older than 2 minutes. Until Z-4, `cube_append` is a stub
  that marks pending rows `failed: not_implemented`. The pipeline never writes
  `cube_sinks.updated_at` (the app's version, #98).
```

- [ ] **Step 2: FEATURES.md** — after the Z-2 row:

```markdown
| Z-3 · Queue lock seam, dispatcher matching, `cube_kick` | ✅ | `QueueBackend.enqueue(..., lock=, queueing_lock=) -> Enqueued`; Procrastinate's `AlreadyEnqueued` becomes `Enqueued(coalesced=True)` inside the backend; the in-memory backend coalesces waiting jobs and runs same-lock jobs one at a time. The dispatcher feeds `cube_appends` from **insert** events only (`PgCubeRepo.enabled_sinks_for_source`, cached per claim; no datetime → `skipped: no_datetime`) and enqueues one `pipeline.cube_append` per sink under `cube:{id}` before the drain. `pipeline.cube_kick` (5 min) first requeues a `cube_append` a dead worker left `doing` (`QueueBackend.retry_stalled`, or closes it `failed` when a waiting job covers the sink), then re-enqueues sinks with pending rows older than 2 min. `cube_append` is a stub (`failed: not_implemented`) until Z-4 |
```

- [ ] **Step 3: Throwaway Postgres on :5499** (scratchpad; `$SP` = the session scratchpad directory). One command per call:

```bash
initdb -D $SP/z3-pg -U postgres --auth=trust
pg_ctl -D $SP/z3-pg -o "-p 5499 -c listen_addresses=localhost -c unix_socket_directories=''" -l $SP/z3-pg.log start
createdb -h localhost -p 5499 -U postgres z3
```

Apply the app migrations, from the worktree root. This is Z-2's recipe; a bare cluster migrates through 032 without pgstac (#97: 30 migrations applied):

```bash
DATABASE_URL=postgresql://postgres@localhost:5499/z3 npx tsx -e "import('./app/src/lib/db/migrate.ts').then(m=>m.runMigrations()).then(()=>process.exit(0),e=>{console.error(e);process.exit(1)})"
psql -h localhost -p 5499 -U postgres -d z3 -Atc "SELECT to_regclass('stac_higher.cube_appends')"
```

Expected: the second command prints `stac_higher.cube_appends`.

- [ ] **Step 4: Run the DB-gated tests against it** (from `services/pipeline/`):

```bash
DATABASE_URL=postgresql://postgres@localhost:5499/z3 uv run pytest tests/test_integration_cubes_repo.py tests/test_integration_db.py -q
```

Expected: PASS (not skipped), including `test_retry_stalled_against_real_procrastinate`. Then stop and delete the cluster:

```bash
pg_ctl -D $SP/z3-pg stop && rm -rf $SP/z3-pg $SP/z3-pg.log
```

- [ ] **Step 5: Gates**

```bash
uv run pytest -q          # services/pipeline
uv run ruff check .       # services/pipeline
npm run verify            # repo root
```

All green; note the counts for the PR body.

- [ ] **Step 6: Commit the docs**

```bash
git add services/pipeline/README.md docs/FEATURES.md
git commit -m "Z-3: docs (pipeline README, FEATURES row)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: PR** — `git push -u origin feat/z3-cube-dispatch`, then `gh pr create --base main --title "Z-3: queue lock seam, dispatcher matching and cube_kick"` with a body starting `Closes #89`, listing the gates run (with counts), the real-DB check, "Lead-only steps: none", and the plan's decisions 1–15 as deviations/choices (14 goes beyond spec §5.3). The body also says:
  - **Coalescing writes an ERROR to the Postgres server log.** Each coalesced enqueue is an INSERT that hits `procrastinate_jobs_queueing_lock_idx_v1`, so the server logs it, about once per sink per claim at steady load. Procrastinate's periodic scheduler already does the same; accepted.
  - **The stub's `failed` rows are terminal.** A sink created on a live stack before Z-4 merges gets `failed: not_implemented` rows that nothing revisits (the kick ignores `failed`).

  Then comment on #90 with what Z-4 inherits: (a) either reset `failed`/`not_implemented` rows to `pending` on first run, or state that no sink may exist before Z-4; (b) decision 15: a stalled-job false positive can run two appends for one sink, so the append must stay safe under that (optimistic Icechunk commit, conflict → reopen and redo, duplicates → `appended`); (c) when it adds `RetrySpec` to `cube_append`, `retry_stalled`'s `retry_job` counts as an attempt. On merge: flip #90 from `blocked` to `ready` if #89 was its only blocker, and remove the worktree.
