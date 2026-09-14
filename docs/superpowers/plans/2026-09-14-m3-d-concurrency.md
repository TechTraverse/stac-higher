# M3-D · Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The pipeline worker runs 12 jobs at once instead of one — 8 on a `default` queue and 4 on a `bytes` queue that holds the memory-heavy stages — with the one ledger transition that cannot survive concurrency (`ingest_files` settled → fetching) made an atomic compare-and-set, ITEMIZE's per-item `flow_stats` bumps batched into one rollup write per association per interval, and the in-process shared state that S-C's audit did not cover reviewed and written down.

**Architecture:** `WORKER_CONCURRENCY` (12) and `WORKER_BYTES_CONCURRENCY` (4) become `Settings`; `ProcrastinateQueue.run_worker()` starts two Procrastinate workers in the same process (`queues=["default"]` with 8 slots, `queues=["bytes"]` with 4), and `register_task` gains a `queue=` argument that `ingest_fetch`, `ingest_itemize` and `deliver` use. The FETCH stage claims a ledger row with a single `UPDATE … WHERE id = %s AND status = 'settled'` (`IngestRepo.transition_ledger`, the `claim_due_runs` shape) instead of read-then-write. A `FlowStatsBatcher` (`flow/batcher.py`, one instance per process) sums ITEMIZE's deltas in memory and a loop in `main.run()` writes one `bump_flow_stats` per association every `FLOW_STATS_FLUSH_SECONDS`. `main.run()` also sizes the default thread executor to the concurrency (every blocking call is `asyncio.to_thread`) and logs a WARNING when `DB_POOL_MAX` is below `WORKER_CONCURRENCY + 4`. A `pipeline_jobs_in_flight{job}` gauge in `instrument_handler` is the outside-the-process evidence the concurrency is in effect.

**Tech Stack:** Python 3.12, Procrastinate 3.9 (`App.run_worker_async(queues=, concurrency=, name=)`; `App.task(queue=)`), psycopg 3 + psycopg_pool, prometheus_client, pytest (`asyncio_mode = "auto"`), ruff.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §3 (M3-D row + dependency spine), §5 (the review must cover module-level state, shared clients, `to_thread` sizing), §7 decisions 2 and 4 (concurrency default 12; the `flow_stats` batching carve-out), §8 (the conceded objection); `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-C (the claim audit table, the FETCH defect, the queue-split recommendation) and M3-S-D "`flow_stats` — measured"; `TODO.md` M3 queue, "M3-D · concurrency" slice text and the "M3-B landed" / "M3-C landed" notes.

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/m3-d-concurrency -b ai/m3-d-concurrency ai/main` — only after **M3-C has merged into `ai/main`** (Task 0 checks; spec §3: "the one ordering that is not negotiable is M3-C → M3-D"). Pipeline-only: no `npm install` needed.
- **Gates before merge:** from `services/pipeline/`: `uv run pytest -q` and `uv run ruff check .`; from the worktree root `npm run verify` (the lead runs it after merge; teammates run pytest + ruff only). Teammates never run e2e, the dev server, or Docker.
- **Settled numbers (spec §7):** `WORKER_CONCURRENCY` default **12** — the TOTAL job slots in the process; `WORKER_BYTES_CONCURRENCY` default **4** of those go to the `bytes` queue, the `default` queue gets the remaining **8**. `DB_POOL_MAX` (16) already satisfies `>= WORKER_CONCURRENCY + 4`; it is not changed.
- **Queue names:** exactly two — `"default"` (Procrastinate's own default queue name; every task and every periodic that does not say otherwise) and `"bytes"`. The `bytes` queue carries **`pipeline.ingest_fetch`, `pipeline.ingest_itemize`, `pipeline.deliver`** and nothing else: the jobs that hold object bytes or a GDAL block cache in the worker's memory. `process_run_now` stays on `default` (it holds a slot for a whole container run and stages inputs through its own 4-way semaphore); `finalize`/`process_finalize` stay on `default` (same-bucket server-side copies). Memory, not I/O, is what the `bytes` queue bounds.
- **Memory envelope (S-E, amended):** `per-worker peak RSS ≈ 255 MiB + (GDAL_CACHEMAX + FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY) × WORKER_BYTES_CONCURRENCY` — the multiplier is the `bytes` queue's slots, because only its jobs hold buffers. With M3-C's defaults: 96 MB × 4 ≈ 384 MB above the baseline.
- **The claim IS the guard (S-C):** `UPDATE stac_higher.ingest_files SET status = 'fetching', item_id = %s, updated_at = now() WHERE id = %s AND status = 'settled'`, skip on `rowcount = 0`. No lock held across the fetch, no new table, no new column. ADR 0001: the pipeline runs no DDL.
- **`flow_stats` batching scope (spec §7.4):** ITEMIZE's `bump_flow_stats` only. DISCOVER already writes one rollup per tick; the delivery repo's `_apply_flow_stats` runs inside the `delivery_log` transaction and is M3-E territory (cut) — **do not touch `delivery/repo.py`**. Counts stay exact; `last_activity_at`/`last_error_at` are stamped at flush time (≤ one interval late; the M2-B windows are seconds to minutes).
- **Do not touch:** `delivery/worker.py`, `finalize/`, the pgstac writer and its session GUCs (ADR 0020), `pipeline/db/pool.py`'s sizing, the `procrastinate` schema.
- **No new dependency.** Structured logging: data in `extra={...}`, messages constant; never use `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 0: Precondition — M3-C is on `ai/main`

**Files:** none.

- [ ] From the worktree: `git log --oneline ai/main -20 | grep -i 'm3-c'` shows the M3-C merge, and `grep -n 'gdal_cachemax_mb\|fetch_transfer_concurrency' services/pipeline/src/pipeline/config.py` prints both fields. If either is missing, STOP and report — raising concurrency against whole-object buffering is the OOM the spec forbids.

---

### Task 1: The three concurrency settings

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py` — module docstring env contract (three bullets after the `DB_POOL_MIN` / `DB_POOL_MAX` bullet); a constants block after `DEFAULT_DB_POOL_MAX = 16` (and after M3-C's memory-envelope block if that sits there — append after the LAST `DEFAULT_*` constant); a parse helper beside `_parse_pgstac_queue_drainer`; three dataclass fields after `db_pool_max: int = DEFAULT_DB_POOL_MAX`; a `default_queue_concurrency` property; three `from_env` kwargs after `db_pool_max=...`; a module-level `sizing_warnings()` function at the end of the file
- Modify: `services/pipeline/README.md` — "Environment contract" table: three rows after the `DB_POOL_MAX` row
- Modify: `docker-compose.yml` — the pipeline service `environment:` list, after the `DB_POOL_MAX=${DB_POOL_MAX:-16}` line (M3-C's `GDAL_CACHEMAX` lines may follow it — append after whichever is last in that M3 block)
- Test: `services/pipeline/tests/test_config.py` (append)

**Interfaces:**
- Produces: `Settings.worker_concurrency: int` (default 12), `Settings.worker_bytes_concurrency: int` (default 4), `Settings.flow_stats_flush_seconds: float` (default 2.0), property `Settings.default_queue_concurrency -> int` (= `worker_concurrency - worker_bytes_concurrency`); constants `DEFAULT_WORKER_CONCURRENCY`, `DEFAULT_WORKER_BYTES_CONCURRENCY`, `DEFAULT_FLOW_STATS_FLUSH_SECONDS`; `SizingWarning(NamedTuple)` with `message: str`, `extra: dict[str, int]`; `sizing_warnings(settings: Settings) -> list[SizingWarning]`. Env names: `WORKER_CONCURRENCY`, `WORKER_BYTES_CONCURRENCY`, `FLOW_STATS_FLUSH_SECONDS`.

- [ ] **Step 1: Failing tests** — append to `tests/test_config.py` (match the file's existing `Settings` import):

```python
def test_worker_concurrency_defaults_to_the_settled_split():
    """M3-D (spec §7 decision 2): 12 slots total, 4 of them for the bytes queue."""
    from pipeline.config import (
        DEFAULT_FLOW_STATS_FLUSH_SECONDS,
        DEFAULT_WORKER_BYTES_CONCURRENCY,
        DEFAULT_WORKER_CONCURRENCY,
        Settings,
    )

    settings = Settings.from_env(env={})
    assert settings.worker_concurrency == DEFAULT_WORKER_CONCURRENCY == 12
    assert settings.worker_bytes_concurrency == DEFAULT_WORKER_BYTES_CONCURRENCY == 4
    assert settings.default_queue_concurrency == 8
    assert settings.flow_stats_flush_seconds == DEFAULT_FLOW_STATS_FLUSH_SECONDS == 2.0
    # The README invariant M3-B documented: the pool clears the slots + ticks.
    assert settings.db_pool_max >= settings.worker_concurrency + 4


def test_worker_concurrency_reads_its_env_names():
    settings = Settings.from_env(
        env={
            "WORKER_CONCURRENCY": "6",
            "WORKER_BYTES_CONCURRENCY": "2",
            "FLOW_STATS_FLUSH_SECONDS": "0.5",
        }
    )
    assert settings.worker_concurrency == 6
    assert settings.worker_bytes_concurrency == 2
    assert settings.default_queue_concurrency == 4
    assert settings.flow_stats_flush_seconds == 0.5


def test_worker_concurrency_rejects_an_impossible_split():
    import pytest

    with pytest.raises(ValueError, match="WORKER_BYTES_CONCURRENCY"):
        Settings.from_env(env={"WORKER_CONCURRENCY": "4", "WORKER_BYTES_CONCURRENCY": "4"})
    with pytest.raises(ValueError, match="WORKER_BYTES_CONCURRENCY"):
        Settings.from_env(env={"WORKER_BYTES_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="WORKER_CONCURRENCY"):
        Settings.from_env(env={"WORKER_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="FLOW_STATS_FLUSH_SECONDS"):
        Settings.from_env(env={"FLOW_STATS_FLUSH_SECONDS": "0"})


def test_sizing_warnings_flag_an_undersized_pool():
    from pipeline.config import sizing_warnings

    assert sizing_warnings(Settings.from_env(env={})) == []
    warnings = sizing_warnings(Settings.from_env(env={"DB_POOL_MAX": "10"}))
    assert len(warnings) == 1
    assert "DB_POOL_MAX" in warnings[0].message
    assert warnings[0].extra == {"db_pool_max": 10, "worker_concurrency": 12, "required": 16}
```

- [ ] **Step 2:** `uv run pytest tests/test_config.py -q -k "worker_concurrency or sizing"` → FAIL (ImportError / AttributeError).

- [ ] **Step 3: Implement.** In `config.py`:

Docstring bullets (after the `DB_POOL_MIN` / `DB_POOL_MAX` bullet, same style):
```
- ``WORKER_CONCURRENCY`` — jobs the worker process runs at once, both queues
  together (M3-D, default 12; spec §7 decision 2).
- ``WORKER_BYTES_CONCURRENCY`` — how many of those slots belong to the ``bytes``
  queue (ingest FETCH/ITEMIZE, deliver — the jobs that hold object bytes or a
  GDAL cache; default 4). The ``default`` queue runs everything else with the
  remainder. At least 1, and less than ``WORKER_CONCURRENCY``.
- ``FLOW_STATS_FLUSH_SECONDS`` — how long ITEMIZE's per-item flow_stats deltas
  are summed in memory before one rollup write per association (default 2.0).
```
Constants (after the last `DEFAULT_*` constant in the file):
```python
# --- Worker concurrency (M3-D, spec §3 / S-C) -------------------------------
#: Jobs in flight per worker process, both queues together — S-C's 12–16 band
#: for the byte-realistic mix, at the conservative end (spec §7, decision 2).
#: DB_POOL_MAX must be >= this + 4 (the periodic ticks that overlap jobs).
DEFAULT_WORKER_CONCURRENCY = 12
#: Slots reserved for the `bytes` queue: ingest FETCH/ITEMIZE and deliver, the
#: jobs that hold object bytes or a GDAL block cache. Resident memory scales
#: with THIS number (S-E: 255 MiB + (GDAL_CACHEMAX + fetch buffers) x slots),
#: so the cheap stages on `default` are never capped by the memory budget.
DEFAULT_WORKER_BYTES_CONCURRENCY = 4
#: ITEMIZE's flow_stats deltas are summed in memory and written once per
#: association per this interval (spec §7.4 carve-out). The row lock behind
#: `bump_flow_stats` measured flat at ~460 bumps/s per association (S-D), so
#: at concurrency 12 every ITEMIZE on one association would otherwise queue
#: ~2 ms behind the others — a serialization point, not a throughput ceiling.
DEFAULT_FLOW_STATS_FLUSH_SECONDS = 2.0
```
Parse helper (beside `_parse_pgstac_queue_drainer`):
```python
def _parse_worker_concurrency(env: dict[str, str]) -> tuple[int, int]:
    total = int(env.get("WORKER_CONCURRENCY", str(DEFAULT_WORKER_CONCURRENCY)))
    bytes_slots = int(
        env.get("WORKER_BYTES_CONCURRENCY", str(DEFAULT_WORKER_BYTES_CONCURRENCY))
    )
    if total < 1:
        raise ValueError(f"WORKER_CONCURRENCY must be >= 1, got {total}")
    if bytes_slots < 1 or bytes_slots >= total:
        raise ValueError(
            "WORKER_BYTES_CONCURRENCY must be >= 1 and < WORKER_CONCURRENCY"
            f" ({total}), got {bytes_slots}"
        )
    return total, bytes_slots


def _parse_flush_seconds(raw: str | None) -> float:
    value = float(raw) if raw is not None else DEFAULT_FLOW_STATS_FLUSH_SECONDS
    if value <= 0:
        raise ValueError(f"FLOW_STATS_FLUSH_SECONDS must be > 0, got {value}")
    return value
```
Dataclass fields (after `db_pool_max`):
```python
    #: Worker concurrency (M3-D) — see the DEFAULT_WORKER_* constants.
    worker_concurrency: int = DEFAULT_WORKER_CONCURRENCY
    worker_bytes_concurrency: int = DEFAULT_WORKER_BYTES_CONCURRENCY
    flow_stats_flush_seconds: float = DEFAULT_FLOW_STATS_FLUSH_SECONDS

    @property
    def default_queue_concurrency(self) -> int:
        """Slots left for the `default` queue once the `bytes` queue has its share."""
        return self.worker_concurrency - self.worker_bytes_concurrency
```
`from_env` — before the `return cls(...)`, add `worker_concurrency, worker_bytes_concurrency = _parse_worker_concurrency(env)` (the function already has `env` as a dict — match how it is bound there), and the kwargs after `db_pool_max=...`:
```python
            worker_concurrency=worker_concurrency,
            worker_bytes_concurrency=worker_bytes_concurrency,
            flow_stats_flush_seconds=_parse_flush_seconds(env.get("FLOW_STATS_FLUSH_SECONDS")),
```
Module end:
```python
class SizingWarning(NamedTuple):
    """A startup WARNING `main.run()` logs: the message is constant, the numbers ride in `extra`."""

    message: str
    extra: dict[str, int]


def sizing_warnings(settings: Settings) -> list[SizingWarning]:
    """Cross-setting invariants that do not error but degrade under load (M3-D).

    A pool smaller than the job slots plus the overlapping periodic ticks makes
    callers wait `pool.timeout` and raise PoolTimeout — invisible until the
    first load rehearsal, so it is said out loud at startup instead.
    """
    warnings: list[SizingWarning] = []
    required = settings.worker_concurrency + 4
    if settings.db_pool_max < required:
        warnings.append(
            SizingWarning(
                "DB_POOL_MAX is below WORKER_CONCURRENCY + 4; repo checkouts will wait"
                " and raise PoolTimeout under load",
                {
                    "db_pool_max": settings.db_pool_max,
                    "worker_concurrency": settings.worker_concurrency,
                    "required": required,
                },
            )
        )
    return warnings
```
(`from typing import NamedTuple` — add to the existing typing import.)

README rows (after `DB_POOL_MAX`):
```
| `WORKER_CONCURRENCY` | `12` | Jobs the worker process runs at once, both queues together (M3-D; spec §7 decision 2, S-C's 12–16 band at the conservative end). Size `DB_POOL_MAX` to at least this + 4; the process logs a WARNING at startup when it is not. |
| `WORKER_BYTES_CONCURRENCY` | `4` | Of those slots, how many the `bytes` queue gets — `pipeline.ingest_fetch`, `pipeline.ingest_itemize`, `pipeline.deliver`, the jobs that hold object bytes or a GDAL block cache. Resident memory scales with THIS number (see "Concurrency (M3-D)"); the `default` queue runs everything else with the remainder (8). At least 1, less than `WORKER_CONCURRENCY`. |
| `FLOW_STATS_FLUSH_SECONDS` | `2.0` | ITEMIZE's per-item `flow_stats` deltas are summed in memory and written once per association per interval; `last_activity_at` trails the item by at most this. Counts are exact; a crash loses at most one interval of telemetry, never a row. |
```
Compose lines (after the last M3 env line in the pipeline block):
```yaml
      # M3-D: 12 job slots — 8 on the `default` queue, 4 on the `bytes` queue
      # (FETCH/ITEMIZE/deliver, the jobs that hold bytes). Memory scales with
      # WORKER_BYTES_CONCURRENCY; DB_POOL_MAX must stay >= WORKER_CONCURRENCY + 4.
      - WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-12}
      - WORKER_BYTES_CONCURRENCY=${WORKER_BYTES_CONCURRENCY:-4}
      - FLOW_STATS_FLUSH_SECONDS=${FLOW_STATS_FLUSH_SECONDS:-2.0}
```

- [ ] **Step 4:** `uv run pytest tests/test_config.py -q` → PASS; `uv run ruff check .` clean.

- [ ] **Step 5: Commit** — `feat(pipeline): WORKER_CONCURRENCY / WORKER_BYTES_CONCURRENCY / FLOW_STATS_FLUSH_SECONDS — the M3-D concurrency split as settings`

---

### Task 2: Two queues, two workers, one process

**Files:**
- Modify: `services/pipeline/src/pipeline/queue/interface.py` — constants `QUEUE_DEFAULT`, `QUEUE_BYTES` after `RetrySpec`; `register_task` gains `queue: str = QUEUE_DEFAULT`
- Modify: `services/pipeline/src/pipeline/queue/memory.py` — `InMemoryQueue.__init__` gains `self.queues: dict[str, str] = {}` (beside `self.retry_specs`); `register_task` records it
- Modify: `services/pipeline/src/pipeline/queue/procrastinate_backend.py` — `register_task(queue=)` → `self.app.task(..., queue=queue)`; `run_worker(*, concurrency, bytes_concurrency)` runs two workers
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py` — `fetch` and `itemize` registered with `queue=QUEUE_BYTES`
- Modify: `services/pipeline/src/pipeline/jobs/dispatch.py:322` — `deliver` registered with `queue=QUEUE_BYTES`
- Modify: `services/pipeline/src/pipeline/metrics.py` — `JOBS_IN_FLIGHT` gauge + `instrument_handler` inc/dec; `__all__`
- Modify: `services/pipeline/src/pipeline/main.py` — `blocking_executor(settings)`; `run()` sets the default executor, logs `sizing_warnings`, passes the concurrency to `run_worker`
- Test: `services/pipeline/tests/test_procrastinate_backend.py` (append), `tests/test_ingest_jobs.py` (extend `test_register_wires_poll_periodic_and_stage_tasks`), `tests/test_delivery_jobs.py` (append one assertion where `dispatch.register` is exercised — read the file's registration test), `tests/test_main_jobs.py` (append; update the `ExplodingQueue` stub's `run_worker` to accept `**kwargs`), `tests/test_metrics.py` (append)

**Interfaces:**
- Consumes: `Settings.worker_concurrency`, `Settings.worker_bytes_concurrency`, `Settings.default_queue_concurrency`, `sizing_warnings()` (Task 1).
- Produces:
  ```python
  # queue/interface.py
  QUEUE_DEFAULT = "default"   # Procrastinate's own default queue name — every task/periodic that does not say otherwise
  QUEUE_BYTES = "bytes"       # ingest_fetch, ingest_itemize, deliver
  class QueueBackend:
      def register_task(self, func, *, name, retry=None, queue: str = QUEUE_DEFAULT) -> None: ...
  # queue/memory.py
  InMemoryQueue.queues: dict[str, str]        # name -> queue
  # queue/procrastinate_backend.py
  async def run_worker(self, *, concurrency: int, bytes_concurrency: int) -> None
  # metrics.py
  JOBS_IN_FLIGHT = Gauge("pipeline_jobs_in_flight", ..., ["job"], registry=REGISTRY)
  # main.py
  def blocking_executor(settings: Settings) -> ThreadPoolExecutor   # max_workers = worker_concurrency + 4
  ```

- [ ] **Step 1: Failing tests.**

Append to `tests/test_procrastinate_backend.py`:
```python
def test_register_task_lands_on_the_named_queue(queue: ProcrastinateQueue):
    from pipeline.queue.interface import QUEUE_BYTES, QUEUE_DEFAULT

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.cheap")
    queue.register_task(handler, name="jobs.heavy", queue=QUEUE_BYTES)
    assert queue.app.tasks["jobs.cheap"].queue == QUEUE_DEFAULT == "default"
    assert queue.app.tasks["jobs.heavy"].queue == QUEUE_BYTES == "bytes"


async def test_run_worker_starts_one_worker_per_queue(queue: ProcrastinateQueue, monkeypatch):
    """M3-D: two Procrastinate workers in one process — the bytes queue's
    concurrency bounds memory, the default queue's is the rest."""
    calls: list[dict] = []

    async def fake_run_worker_async(**kwargs):
        calls.append(kwargs)

    async def fake_open():
        pass

    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    monkeypatch.setattr(queue, "_ensure_open", fake_open)

    await queue.run_worker(concurrency=12, bytes_concurrency=4)

    by_name = {c["name"]: c for c in calls}
    assert set(by_name) == {"default", "bytes"}
    assert by_name["default"]["queues"] == ["default"]
    assert by_name["default"]["concurrency"] == 8
    assert by_name["bytes"]["queues"] == ["bytes"]
    assert by_name["bytes"]["concurrency"] == 4
```

In `tests/test_ingest_jobs.py`, extend `test_register_wires_poll_periodic_and_stage_tasks` (import `QUEUE_BYTES, QUEUE_DEFAULT` from `pipeline.queue.interface`):
```python
    # M3-D: the byte-holding stages run on the bounded `bytes` queue; the
    # cheap stages and the periodics stay on `default`.
    assert queue.queues[JOB_FETCH] == QUEUE_BYTES
    assert queue.queues[JOB_ITEMIZE] == QUEUE_BYTES
    assert queue.queues[JOB_DISCOVER] == QUEUE_DEFAULT
    assert queue.queues[JOB_GROUP] == QUEUE_DEFAULT
```
In `tests/test_delivery_jobs.py`, in the test that calls `dispatch.register(queue, settings)` on an `InMemoryQueue` (read the file; if none registers on an `InMemoryQueue`, add one):
```python
    assert queue.queues[JOB_DELIVER] == QUEUE_BYTES
```
Append to `tests/test_main_jobs.py`:
```python
def test_build_queue_puts_only_the_byte_holding_jobs_on_the_bytes_queue():
    from pipeline.jobs.dispatch import JOB_DELIVER
    from pipeline.queue.interface import QUEUE_BYTES, QUEUE_DEFAULT

    queue = build_queue(Settings.from_env(env={}))
    on_bytes = {name for name, task in queue.app.tasks.items() if task.queue == QUEUE_BYTES}
    assert on_bytes == {JOB_FETCH, JOB_ITEMIZE, JOB_DELIVER}
    assert queue.app.tasks[HEARTBEAT_JOB].queue == QUEUE_DEFAULT
    assert queue.app.tasks[JOB_RUN_NOW].queue == QUEUE_DEFAULT


def test_blocking_executor_is_sized_to_the_concurrency():
    """Every blocking call is `asyncio.to_thread`; the loop's default executor
    (min(32, cpus + 4) threads) would be a hidden ceiling below 12 on a small
    container, so main sizes it to the slots plus the overlapping ticks."""
    from pipeline.main import blocking_executor

    executor = blocking_executor(Settings.from_env(env={"WORKER_CONCURRENCY": "12"}))
    try:
        assert executor._max_workers == 16
    finally:
        executor.shutdown(wait=False)
```
Append to `tests/test_metrics.py` (reuse its imports; `render_metrics` is exported):
```python
async def test_instrument_handler_tracks_jobs_in_flight():
    import asyncio

    from pipeline.metrics import instrument_handler, render_metrics

    release = asyncio.Event()

    async def handler():
        await release.wait()

    wrapped = instrument_handler(handler, "jobs.inflight")
    task = asyncio.create_task(wrapped())
    await asyncio.sleep(0)
    assert b'pipeline_jobs_in_flight{job="jobs.inflight"} 1.0' in render_metrics()
    release.set()
    await task
    assert b'pipeline_jobs_in_flight{job="jobs.inflight"} 0.0' in render_metrics()
```

- [ ] **Step 2:** `uv run pytest tests/test_procrastinate_backend.py tests/test_ingest_jobs.py tests/test_main_jobs.py tests/test_metrics.py -q` → FAIL (`queue` kwarg unknown / `queues` attribute missing / gauge absent).

- [ ] **Step 3: Implement.**

`queue/interface.py` — after `RetrySpec`:
```python
#: Procrastinate's own default queue name — every task and periodic that does
#: not say otherwise. Runs with WORKER_CONCURRENCY - WORKER_BYTES_CONCURRENCY slots.
QUEUE_DEFAULT = "default"
#: The bounded queue (M3-D): jobs that hold object bytes or a GDAL block cache
#: — ingest FETCH/ITEMIZE, deliver. Runs with WORKER_BYTES_CONCURRENCY slots,
#: so resident memory is bounded by that number, not by the total.
QUEUE_BYTES = "bytes"
```
and `register_task(self, func: JobHandler, *, name: str, retry: RetrySpec | None = None, queue: str = QUEUE_DEFAULT) -> None` with one docstring line: "``queue`` names the worker pool the job runs in (M3-D): ``QUEUE_DEFAULT`` unless the handler holds bytes."

`queue/memory.py`: `self.queues: dict[str, str] = {}` in `__init__`; in `register_task` add the `queue` parameter and `self.queues[name] = queue`.

`queue/procrastinate_backend.py`:
```python
import asyncio
...
from pipeline.queue.interface import (
    QUEUE_BYTES,
    QUEUE_DEFAULT,
    JobHandler,
    ...
)

    def register_task(
        self,
        func: JobHandler,
        *,
        name: str,
        retry: RetrySpec | None = None,
        queue: str = QUEUE_DEFAULT,
    ) -> None:
        ...
        self.app.task(instrument_handler(func, name), name=name, retry=strategy, queue=queue)

    async def run_worker(self, *, concurrency: int, bytes_concurrency: int) -> None:
        """Two Procrastinate workers in this process, one per queue (M3-D).

        A single worker with one semaphore cannot bound the byte-holding jobs
        without also capping the cheap ones — a slot claimed by a FETCH waiting
        on an in-process semaphore is still a slot. Two workers each claim only
        their own queue's jobs, so ``bytes`` never holds more than its share.
        Both run a periodic deferrer; the second defer of every tick hits the
        UNIQUE (task_name, periodic_id, defer_timestamp) row and is logged by
        Procrastinate as already deferred — one execution per tick, as before.
        """
        await self._ensure_open()
        await asyncio.gather(
            self.app.run_worker_async(
                queues=[QUEUE_DEFAULT],
                concurrency=concurrency - bytes_concurrency,
                name=QUEUE_DEFAULT,
            ),
            self.app.run_worker_async(
                queues=[QUEUE_BYTES], concurrency=bytes_concurrency, name=QUEUE_BYTES
            ),
        )
```
`jobs/ingest.py` (import `QUEUE_BYTES` from `pipeline.queue.interface`, beside `QueueBackend, RetrySpec`):
```python
    queue.register_task(fetch, name=JOB_FETCH, retry=STAGE_RETRY, queue=QUEUE_BYTES)
    queue.register_task(itemize, name=JOB_ITEMIZE, retry=STAGE_RETRY, queue=QUEUE_BYTES)
```
`jobs/dispatch.py:322`: `queue.register_task(deliver, name=JOB_DELIVER, retry=DELIVER_RETRY, queue=QUEUE_BYTES)` (same import).

`metrics.py` — declare after `JOB_SECONDS` (keep the file's comment style) and add `"JOBS_IN_FLIGHT",` to `__all__` between `"INGEST_EVENTS"` and `"JOB_RUNS"`:
```python
JOBS_IN_FLIGHT = Gauge(
    "pipeline_jobs_in_flight",
    "Handlers executing right now, by job — the outside-the-process evidence"
    " that WORKER_CONCURRENCY is in effect (M3-D)",
    ["job"],
    registry=REGISTRY,
)
```
`instrument_handler.wrapped`:
```python
        start = time.monotonic()
        in_flight = JOBS_IN_FLIGHT.labels(job=name)
        in_flight.inc()
        try:
            ...
        finally:
            in_flight.dec()
            JOB_SECONDS.labels(job=name).observe(time.monotonic() - start)
```
`main.py`:
```python
from concurrent.futures import ThreadPoolExecutor
...
from pipeline.config import Settings, sizing_warnings


def blocking_executor(settings: Settings) -> ThreadPoolExecutor:
    """The loop's default executor, sized to the job slots plus the overlapping
    periodic ticks. Every blocking call in the worker is `asyncio.to_thread`
    (boto3, rasterio, pgstac), so the stdlib default of min(32, cpus + 4)
    threads would be a hidden concurrency ceiling on a small container."""
    return ThreadPoolExecutor(
        max_workers=settings.worker_concurrency + 4, thread_name_prefix="pipeline-blocking"
    )
```
In `run()`, first lines of the `try` (before `queue.setup()`):
```python
        asyncio.get_running_loop().set_default_executor(blocking_executor(settings))
        for warning in sizing_warnings(settings):
            logger.warning(warning.message, extra=warning.extra)
```
and the gather's worker line becomes:
```python
            queue.run_worker(
                concurrency=settings.worker_concurrency,
                bytes_concurrency=settings.worker_bytes_concurrency,
            ),
```
Also log the split in the existing "pipeline service starting" line's `extra`: add `"worker_concurrency": settings.worker_concurrency, "worker_bytes_concurrency": settings.worker_bytes_concurrency`.

`tests/test_main_jobs.py` `ExplodingQueue` (and any other `run_worker` stub in `tests/`): `async def run_worker(self, **_kwargs: object) -> None:` — read the stub, keep its body.

- [ ] **Step 4:** the four focused files → PASS; then `uv run pytest -q` (all) and `uv run ruff check .` clean. If any test file constructs a `QueueBackend` subclass of its own with a `register_task` that lacks `queue`, add the parameter there too (the ABC signature changed).

- [ ] **Step 5: Commit** — `feat(queue): two workers per process — the bytes queue (FETCH/ITEMIZE/deliver) bounded by WORKER_BYTES_CONCURRENCY, jobs_in_flight gauge, executor sized to the slots (M3-D)`

---

### Task 3: The FETCH claim is a compare-and-set

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/repo.py` — `IngestRepo.transition_ledger` (ABC, after `set_ledger_fields`), `PgIngestRepo.transition_ledger` (after `set_ledger_fields`)
- Modify: `services/pipeline/tests/_ingest_fake.py` — `FakeIngestRepo.transition_ledger` (after `set_ledger_fields`)
- Modify: `services/pipeline/src/pipeline/ingest/fetch.py:70-75` (copy loop) and `:121-130` (reference loop)
- Test: `services/pipeline/tests/test_ingest_fetch.py` (append)

**Interfaces:**
- Produces:
  ```python
  class IngestRepo:
      async def transition_ledger(self, entry_id: str, *, expected_status: str, status: str, **fields: Any) -> bool: ...
  ```
  Returns `True` when the row moved (it was in `expected_status`), `False` when another worker got there first — nothing written. `fields` are the same mutable columns `set_ledger_fields` accepts.

- [ ] **Step 1: Failing tests** — append to `tests/test_ingest_fetch.py` (add `import asyncio` to the imports):

```python
class _YieldingRepo(FakeIngestRepo):
    """Yields to the event loop between the read and the claim, the way a real
    round trip does — two concurrent FETCH calls interleave like two workers."""

    async def get_latest_ledger(self, association_id, source_path):
        latest = await super().get_latest_ledger(association_id, source_path)
        await asyncio.sleep(0)
        return latest


async def test_two_concurrent_fetches_store_the_row_once():
    """S-C's one real defect: the settled -> fetching move was a read-then-write
    guard, so two overlapping FETCH jobs both fetched and both bumped. The
    claim is now the guard (M3-D): exactly one wins the row."""
    repo = _YieldingRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await asyncio.gather(
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
    )

    assert sorted(stored) == [0, 1]
    assert len(s3.puts) == 1
    assert adapter.get_calls == ["products/scene.tif"]
    (row,) = repo.rows.values()
    assert row.status == "stored"


async def test_two_concurrent_reference_fetches_store_the_row_once():
    repo = _YieldingRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/", "storage_mode": "reference"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await asyncio.gather(
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
    )

    assert sorted(stored) == [0, 1]
    (row,) = repo.rows.values()
    assert row.status == "stored"
    assert row.item_id == "scene"


async def test_transition_ledger_is_a_no_op_when_the_row_moved_on():
    repo = FakeIngestRepo()
    row = await _settled(repo, "scene.tif")
    assert await repo.transition_ledger(
        row.id, expected_status="settled", status="fetching", item_id="scene"
    )
    assert row.status == "fetching" and row.item_id == "scene"
    assert not await repo.transition_ledger(
        row.id, expected_status="settled", status="fetching", item_id="other"
    )
    assert row.item_id == "scene"
```
(Read `FakeAdapter` in `_ingest_fake.py`: if its `get` does not consult `storage_mode` and `public_object_url` is missing, add `def public_object_url(self, path): return f"https://src.example/{path}"` to the fake — check whether the existing reference-mode tests already rely on one.)

- [ ] **Step 2:** `uv run pytest tests/test_ingest_fetch.py -q` → the two concurrent tests FAIL (2 puts / both stored), the `transition_ledger` test errors (AttributeError).

- [ ] **Step 3: Implement.**

`ingest/repo.py` ABC, after `set_ledger_fields`:
```python
    @abc.abstractmethod
    async def transition_ledger(
        self, entry_id: str, *, expected_status: str, status: str, **fields: Any
    ) -> bool:
        """Compare-and-set (M3-D): move the row to ``status`` and apply ``fields``
        only if it is still in ``expected_status``; ``False`` (and nothing
        written) when another worker moved it first. The FETCH claim — the
        same one-statement shape as ``process_runs.claim_due_runs`` (S-C)."""
```
`PgIngestRepo`, after `set_ledger_fields`:
```python
    async def transition_ledger(  # pragma: no cover
        self, entry_id: str, *, expected_status: str, status: str, **fields: Any
    ) -> bool:
        unknown = set(fields) - _LEDGER_MUTABLE
        if unknown:
            raise ValueError(f"non-mutable ledger columns: {sorted(unknown)}")
        assignments = ", ".join(f"{col} = %s" for col in ("status", *fields))
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"UPDATE stac_higher.ingest_files SET {assignments}, updated_at = now()"
                " WHERE id = %s AND status = %s",
                (status, *fields.values(), entry_id, expected_status),
            )
            await conn.commit()
            return cur.rowcount == 1
```
(`"status"` must be in `_LEDGER_MUTABLE` already — it is what `set_ledger_fields(status=...)` relies on.)

`tests/_ingest_fake.py`, after `set_ledger_fields`:
```python
    async def transition_ledger(
        self, entry_id: str, *, expected_status: str, status: str, **fields: Any
    ) -> bool:
        row = self.rows[entry_id]
        if row.status != expected_status:
            return False
        row.status = status
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = self.now
        return True
```
`ingest/fetch.py` copy loop — replace lines 71-75 with:
```python
        latest = await repo.get_latest_ledger(association.id, source_path)
        if latest is None:
            continue
        # M3-D: the claim IS the guard — one atomic compare-and-set. A second
        # FETCH for the same group (GROUP re-emitting before this ran, or two
        # overlapping GROUP ticks) loses the row here instead of fetching the
        # same bytes twice and bumping the rollup twice (S-C).
        claimed = await repo.transition_ledger(
            latest.id, expected_status=STATUS_SETTLED, status=STATUS_FETCHING, item_id=item_id
        )
        if not claimed:
            continue
```
Reference loop — keep the `latest is None or latest.status != STATUS_SETTLED` pre-check (it avoids computing an href for a row that is visibly done) and replace the `set_ledger_fields(..., status=STATUS_STORED, item_id=..., source_href=href)` call:
```python
            href = adapter.public_object_url(source_fetch_path(config.source_path, source_path))
            claimed = await repo.transition_ledger(
                latest.id,
                expected_status=STATUS_SETTLED,
                status=STATUS_STORED,
                item_id=item_id,
                source_href=href,
            )
            if claimed:
                stored += 1
```
Update the `_reference_stage` docstring's "Idempotent (only acts on a still-`settled` row)" to "Idempotent: the settled → stored move is a compare-and-set (M3-D)". Update the module/function docstrings that describe the "idempotent guard" the same way.

- [ ] **Step 4:** `uv run pytest tests/test_ingest_fetch.py tests/test_ingest_jobs.py tests/test_ingest_group.py -q` → PASS; `uv run pytest -q`; `uv run ruff check .`.

- [ ] **Step 5: Commit** — `fix(ingest): the FETCH claim is an atomic compare-and-set — two concurrent FETCHes store a row once (M3-D, S-C's one defect)`

---

### Task 4: ITEMIZE's `flow_stats` deltas, batched

**Files:**
- Create: `services/pipeline/src/pipeline/flow/batcher.py`
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py` — `register(queue, settings, *, batcher=None)`; the `itemize` handler's two `bump_flow_stats` calls become `batcher.add(...)`
- Modify: `services/pipeline/src/pipeline/main.py` — `build_queue` passes the process batcher to `ingest.register`; `run()` gathers the flush loop
- Test: `services/pipeline/tests/test_flow_batcher.py` (new), `tests/test_ingest_jobs.py` (append + extend the existing extracting test)

**Interfaces:**
- Produces:
  ```python
  # flow/batcher.py
  @dataclass
  class FlowDelta: files: int = 0; bytes_added: int = 0; items: int = 0; failed: int = 0; latency_seconds: float | None = None
      def add(self, *, files=0, bytes_added=0, items=0, failed=0, latency_seconds=None) -> None
  class FlowStatsBatcher:
      def add(self, association_id: str, *, files=0, bytes_added=0, items=0, failed=0, latency_seconds=None) -> None
      @property
      def pending(self) -> dict[str, FlowDelta]          # a copy
      def drain(self) -> dict[str, FlowDelta]            # swaps the pending map out
      async def flush(self, repo: IngestRepo) -> int     # one bump_flow_stats per association; returns how many
      async def run(self, repo_factory: Callable[[], IngestRepo], interval_seconds: float) -> None   # loop until cancelled; final flush
  FLOW_BATCHER = FlowStatsBatcher()                      # the process's one instance (like metrics.REGISTRY / heartbeat.STATE)
  # jobs/ingest.py
  def register(queue: QueueBackend, settings: Settings, *, batcher: FlowStatsBatcher | None = None) -> None   # None -> FLOW_BATCHER
  ```

- [ ] **Step 1: Failing tests.**

Create `tests/test_flow_batcher.py`:
```python
"""FlowStatsBatcher (M3-D): ITEMIZE's per-item deltas become one rollup write
per association per flush — the DISCOVER shape, applied to ITEMIZE."""

from __future__ import annotations

import asyncio

from _ingest_fake import FakeIngestRepo
from pipeline.flow.batcher import FlowDelta, FlowStatsBatcher


def test_deltas_sum_and_latency_keeps_the_last_value():
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1, bytes_added=10, latency_seconds=1.5)
    batcher.add("a1", items=1, bytes_added=5)
    batcher.add("a1", failed=1, latency_seconds=2.5)
    batcher.add("a2", items=1)

    assert batcher.pending == {
        "a1": FlowDelta(items=2, bytes_added=15, failed=1, latency_seconds=2.5),
        "a2": FlowDelta(items=1),
    }


def test_drain_swaps_the_pending_map_out():
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)
    drained = batcher.drain()
    assert drained == {"a1": FlowDelta(items=1)}
    assert batcher.pending == {}


async def test_flush_writes_one_bump_per_association_with_the_summed_delta():
    repo = FakeIngestRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1, bytes_added=10, latency_seconds=1.5)
    batcher.add("a1", items=1, bytes_added=5, latency_seconds=2.5)
    batcher.add("a2", failed=1)

    written = await batcher.flush(repo)

    assert written == 2
    assert repo.flow_stats["a1"]["items"] == 2
    assert repo.flow_stats["a1"]["bytes"] == 15
    assert repo.flow_stats["a1"]["last_latency_seconds"] == 2.5
    assert "last_activity_at" in repo.flow_stats["a1"]
    assert repo.flow_stats["a2"]["failed"] == 1
    assert "last_error_at" in repo.flow_stats["a2"]
    assert batcher.pending == {}


async def test_flush_with_nothing_pending_writes_nothing():
    repo = FakeIngestRepo()
    assert await batcher_flush_count(repo) == 0


async def batcher_flush_count(repo):
    return await FlowStatsBatcher().flush(repo)


async def test_a_failed_write_keeps_the_delta_for_the_next_flush(caplog):
    class _FlakyRepo(FakeIngestRepo):
        fail = True

        async def bump_flow_stats(self, association_id, **kw):
            if self.fail:
                raise RuntimeError("db away")
            await super().bump_flow_stats(association_id, **kw)

    repo = _FlakyRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)

    assert await batcher.flush(repo) == 0
    assert batcher.pending == {"a1": FlowDelta(items=1)}
    assert any("flow_stats flush failed" in r.message for r in caplog.records)

    repo.fail = False
    batcher.add("a1", items=1)
    assert await batcher.flush(repo) == 1
    assert repo.flow_stats["a1"]["items"] == 2


async def test_run_flushes_on_the_interval_and_once_more_on_cancel():
    repo = FakeIngestRepo()
    batcher = FlowStatsBatcher()
    batcher.add("a1", items=1)

    task = asyncio.create_task(batcher.run(lambda: repo, 0.01))
    await asyncio.sleep(0.05)
    assert repo.flow_stats["a1"]["items"] == 1

    batcher.add("a1", items=1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert repo.flow_stats["a1"]["items"] == 2
```
(Replace the `batcher_flush_count` helper with the direct call `await FlowStatsBatcher().flush(repo)` — it is shown only to keep the test one line; write it inline.)

In `tests/test_ingest_jobs.py`, extend `test_itemize_handler_bumps_nothing_when_the_group_goes_to_an_extractor`: construct `batcher = FlowStatsBatcher()` (import from `pipeline.flow.batcher`), call `ingest.register(queue, settings, batcher=batcher)`, and add `assert batcher.pending == {}` beside the existing `assert repo.flow_stats == {}`. Then append (same monkeypatch scaffold as that test):
```python
async def test_itemize_handler_batches_the_rollup_instead_of_writing_it(monkeypatch):
    """M3-D (spec §7.4): the per-item bump goes to the process batcher; the
    association row is written once per flush, not once per ITEMIZE."""
    from pipeline.flow.batcher import FlowDelta, FlowStatsBatcher

    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    batcher = FlowStatsBatcher()
    ingest.register(queue, settings, batcher=batcher)

    assoc = IngestAssociation(
        id="a1", collection_id="col", config={"source_path": "/o"}, connection=None
    )
    config = parse_ingest_config({"source_path": "/o"})
    repo = FakeIngestRepo()

    async def _fake_load(_settings, _aid):
        return (repo, assoc, config)

    outcomes = iter(
        [
            ItemizeOutcome("itemized", "scene", None, bytes=10, latency_seconds=1.5),
            ItemizeOutcome("failed", "scene2", None),
        ]
    )

    async def _fake_run_itemize(*_a, **_k):
        return next(outcomes)

    monkeypatch.setattr(ingest, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(ingest, "_load_association", _fake_load)
    monkeypatch.setattr(ingest, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(ingest, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(ingest, "PgPgstacWriter", lambda _u: object())
    monkeypatch.setattr(ingest, "PgProcessRepo", lambda _u: object())
    monkeypatch.setattr(ingest, "run_itemize", _fake_run_itemize)

    await queue.tasks[JOB_ITEMIZE](association_id="a1", item_id="scene", source_paths=["a.nc"])
    await queue.tasks[JOB_ITEMIZE](association_id="a1", item_id="scene2", source_paths=["b.nc"])

    assert repo.flow_stats == {}
    assert batcher.pending == {
        "a1": FlowDelta(items=1, bytes_added=10, failed=1, latency_seconds=1.5)
    }
    await batcher.flush(repo)
    assert repo.flow_stats["a1"]["items"] == 1
    assert repo.flow_stats["a1"]["failed"] == 1
```
(Read `ItemizeOutcome` in `ingest/itemize.py` for its real field names and positional order — `bytes` and `latency_seconds` are what the handler reads; construct it the way the file's existing test does.)

- [ ] **Step 2:** `uv run pytest tests/test_flow_batcher.py tests/test_ingest_jobs.py -q` → FAIL (ModuleNotFoundError / unexpected kwarg `batcher`).

- [ ] **Step 3: Implement.**

`flow/batcher.py`:
```python
"""Batch ITEMIZE's flow_stats deltas into one rollup write per association (M3-D).

``bump_flow_stats`` takes the association row ``FOR UPDATE`` — measured flat at
~460 bumps/s per association (S-D), a row lock, not a throughput ceiling. At
concurrency 12 every ITEMIZE on one association would queue ~2 ms behind the
others, a serialization point in a ~44 ms stage. DISCOVER already writes one
rollup per tick; this gives ITEMIZE the same shape: deltas are summed here and
written once per association per ``FLOW_STATS_FLUSH_SECONDS`` (spec §7.4).

Counts are exact. ``last_activity_at`` / ``last_error_at`` are stamped at flush
time, so they trail the item by at most one interval — inside the M2-B
expectation windows (seconds to minutes). Telemetry only: a crash loses at most
one interval of deltas, never a row. Single-threaded by construction — every
caller is a coroutine on the worker's event loop — so no lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pipeline.ingest.repo import IngestRepo

logger = logging.getLogger(__name__)


@dataclass
class FlowDelta:
    files: int = 0
    bytes_added: int = 0
    items: int = 0
    failed: int = 0
    #: The LAST observed latency — what `apply_ingest_activity` stores.
    latency_seconds: float | None = None

    def add(
        self,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        self.files += files
        self.bytes_added += bytes_added
        self.items += items
        self.failed += failed
        if latency_seconds is not None:
            self.latency_seconds = latency_seconds


class FlowStatsBatcher:
    def __init__(self) -> None:
        self._pending: dict[str, FlowDelta] = {}

    def add(
        self,
        association_id: str,
        *,
        files: int = 0,
        bytes_added: int = 0,
        items: int = 0,
        failed: int = 0,
        latency_seconds: float | None = None,
    ) -> None:
        self._pending.setdefault(association_id, FlowDelta()).add(
            files=files,
            bytes_added=bytes_added,
            items=items,
            failed=failed,
            latency_seconds=latency_seconds,
        )

    @property
    def pending(self) -> dict[str, FlowDelta]:
        return dict(self._pending)

    def drain(self) -> dict[str, FlowDelta]:
        drained, self._pending = self._pending, {}
        return drained

    async def flush(self, repo: IngestRepo) -> int:
        """One ``bump_flow_stats`` per association with a pending delta.

        A failed write puts that delta back (merged with whatever arrived in
        the meantime) for the next flush and logs — the counts are not lost,
        only late.
        """
        written = 0
        for association_id, delta in self.drain().items():
            try:
                await repo.bump_flow_stats(
                    association_id,
                    files=delta.files,
                    bytes_added=delta.bytes_added,
                    items=delta.items,
                    failed=delta.failed,
                    latency_seconds=delta.latency_seconds,
                )
                written += 1
            except Exception:
                logger.exception(
                    "flow_stats flush failed; delta retained",
                    extra={"association_id": association_id},
                )
                self._pending.setdefault(association_id, FlowDelta()).add(
                    files=delta.files,
                    bytes_added=delta.bytes_added,
                    items=delta.items,
                    failed=delta.failed,
                    latency_seconds=delta.latency_seconds,
                )
        return written

    async def run(self, repo_factory: Callable[[], IngestRepo], interval_seconds: float) -> None:
        """Flush every ``interval_seconds`` until cancelled, then once more."""
        repo = repo_factory()
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.flush(repo)
        finally:
            try:
                await self.flush(repo)
            except Exception:
                logger.warning("final flow_stats flush failed", exc_info=True)


#: The process's one batcher — the itemize handler adds to it, `main.run()`
#: flushes it. Tests construct their own.
FLOW_BATCHER = FlowStatsBatcher()
```
`jobs/ingest.py`: `from pipeline.flow.batcher import FLOW_BATCHER, FlowStatsBatcher`; `def register(queue: QueueBackend, settings: Settings, *, batcher: FlowStatsBatcher | None = None) -> None:` with `batcher = batcher if batcher is not None else FLOW_BATCHER` as its first line (read the function's real signature first; keep any existing parameters). In `itemize`, replace the two `await repo.bump_flow_stats(...)` calls with `batcher.add(...)` (same keyword arguments) and rewrite the comment above them:
```python
        # Flow telemetry (M2-A), batched (M3-D): the per-item delta goes to the
        # process batcher and the association row is written once per flush —
        # the per-association row lock is not on the ITEMIZE hot path anymore.
        # "skipped" and "extracting" still write nothing.
```
`main.py`: `from pipeline.flow.batcher import FLOW_BATCHER`; `from pipeline.ingest.repo import PgIngestRepo`; in `build_queue`: `ingest.register(queue, settings, batcher=FLOW_BATCHER)`; in `run()`'s gather add:
```python
            # M3-D: ITEMIZE's flow_stats deltas, written once per association
            # per interval; a final flush runs when the gather is cancelled.
            FLOW_BATCHER.run(
                lambda: PgIngestRepo(settings.database_url), settings.flow_stats_flush_seconds
            ),
```

- [ ] **Step 4:** the two focused files → PASS; `uv run pytest -q`; `uv run ruff check .`.

- [ ] **Step 5: Commit** — `feat(flow): batch ITEMIZE's flow_stats deltas — one rollup write per association per FLOW_STATS_FLUSH_SECONDS (M3-D, spec §7.4)`

---

### Task 5: Docs — the concurrency written down, the shared-state audit, I-40

**Files:**
- Modify: `services/pipeline/README.md` — new `## Concurrency (M3-D)` section after `## Connection pooling (M3-B)`; the M3-C `## Memory envelope` section's formula multiplier; the "Health endpoint" section gains one sentence on `pipeline_jobs_in_flight`
- Modify: `services/pipeline/src/pipeline/loadgen/README.md` — one paragraph after the extractor guidance
- Modify: `docs/ISSUES.md` — I-40 gains an M3-D paragraph
- Modify: `docs/FEATURES.md` — the M3 paragraph/row where the M3-C sentence lives (grep `M3-C`): append the M3-D sentence

- [ ] **Step 1:** README section (after the M3-B section):

```markdown
## Concurrency (M3-D)

One process, two Procrastinate workers, twelve job slots (`WORKER_CONCURRENCY`):

| queue | slots | jobs | why |
|---|---:|---|---|
| `default` | `WORKER_CONCURRENCY - WORKER_BYTES_CONCURRENCY` (8) | every periodic tick, DISCOVER, GROUP, dispatch, backfill, finalize, process triggers/runs/finalize, GC, notify, drains | metadata and SQL; the memory budget never caps them |
| `bytes` | `WORKER_BYTES_CONCURRENCY` (4) | `pipeline.ingest_fetch`, `pipeline.ingest_itemize`, `pipeline.deliver` | the jobs that hold object bytes (FETCH's stream buffers, delivery's transfer) or a GDAL block cache (EXTRACT) |

Two workers rather than one semaphore because a slot a FETCH holds while
waiting on an in-process semaphore is still a slot: only a worker that claims
just its own queue leaves the other queue's slots free. Both workers run a
periodic deferrer; the second defer of every tick hits the
`UNIQUE (task_name, periodic_id, defer_timestamp)` row and is logged by
Procrastinate as already deferred — one execution per tick, as before.

**Memory:** `peak RSS ≈ 255 MiB + (GDAL_CACHEMAX + FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY) × WORKER_BYTES_CONCURRENCY`
— the multiplier is the `bytes` queue's slots, because only its jobs hold
buffers (M3-C's defaults: 96 MB × 4 ≈ 384 MB above the baseline). Raise
`WORKER_BYTES_CONCURRENCY` when the byte-realistic mix needs more than four
transfers in flight (S-C: ~10 slots for 30 items/s of 34 MB streamed objects;
~3 when the server-side copy gate allows) and size the container from the
formula. `WORKER_CONCURRENCY` is the total; `DB_POOL_MAX >= WORKER_CONCURRENCY + 4`
or the process logs a WARNING at startup and callers wait on the pool under load.

**The FETCH claim.** `ingest_files` settled → fetching is a single
`UPDATE … WHERE id = %s AND status = 'settled'` (`IngestRepo.transition_ledger`;
`rowcount = 0` means another worker won the row and this one skips it). S-C's
audit found this the only ledger transition that was a read-then-write guard
rather than a claim; every other leg was already `FOR UPDATE SKIP LOCKED` or a
single conditional statement. Two overlapping FETCHes for one group (GROUP
re-emitting before FETCH ran, or two overlapping GROUP ticks) now store the row
once instead of fetching the same bytes twice and bumping the rollup twice.

**`flow_stats` batching (spec §7.4).** ITEMIZE's per-item bump goes to the
process `FlowStatsBatcher` and the association row is written once per
association per `FLOW_STATS_FLUSH_SECONDS` — the DISCOVER shape (one rollup
per tick) applied to ITEMIZE. The row lock behind `bump_flow_stats` measured
flat at ~460 bumps/s per association (S-D): not a ceiling at 30 items/s, but a
serialization point every concurrent ITEMIZE on one association would queue
behind. Counts are exact; `last_activity_at` / `last_error_at` trail by at most
one interval; a crash loses at most one interval of telemetry, never a row.
DISCOVER's bump and the delivery repo's in-transaction rollup are unchanged
(delivery batching is M3-E, cut).

**Blocking calls.** Every blocking call in the worker is `asyncio.to_thread`
(boto3, rasterio, pgstac). `main.run()` sizes the loop's default executor to
`WORKER_CONCURRENCY + 4` threads; the stdlib default (`min(32, cpus + 4)`)
would be a hidden ceiling below the slots on a small container.

**Shared in-process state under 12 concurrent jobs** — the review spec §5/§8
asked for beyond S-C's database-level audit:

| state | where | shared how | verdict |
|---|---|---|---|
| async repo pool registry | `db/pool.py` `_pools` + `asyncio.Lock` | one pool per DSN, double-checked under the lock; sized `DB_POOL_MAX` | safe; the pool is the bound |
| pgstac writer pool registry | `stac/pgstac_writer.py` `_POOLS` + `threading.Lock`, `WRITER_POOL_MAX = 4` | sync pool used from `to_thread`; at most 4 upserts in flight | safe; ITEMIZE (≤ 4 on `bytes`) plus the finalize legs can exceed 4 and wait `pool.timeout` — watch for `PoolTimeout` in a load run before raising it |
| boto3 clients | `storage/platform.build_platform_client`, `connections/adapters/s3.py` | built per job handler / per adapter instance; never module-level | safe (boto3 clients are also documented thread-safe) |
| GDAL / rasterio | `ingest/raster_io.open_raster` | `rasterio.Env` per open, `GDAL_CACHEMAX` per env; GDAL's block cache is process-global and bounded by the setting | safe; the cache bound is the setting, not per job |
| Prometheus registry | `metrics.REGISTRY` | module-level, client-library locking | safe |
| heartbeat state | `jobs/heartbeat.STATE` | single writer (the periodic, `queueing_lock`) | safe |
| flow_stats batcher | `flow/batcher.FLOW_BATCHER` | event-loop-only callers, no threads | safe by construction |
| process input staging | `process/staging.py` own `asyncio.Semaphore(PROCESS_INPUT_STAGE_CONCURRENCY)` | per run | safe; runs stay on `default` |
| `global` statements | none in `pipeline/` outside `loadgen/fixtures.py` | — | — |
| Procrastinate connector pool | `PsycopgConnector` (psycopg_pool defaults) | claims/finishes only; job work uses the repo pool | left at the default; revisit only if `procrastinate` claim latency shows in a load run |
```
Memory-envelope section (M3-C's): change `× WORKER_CONCURRENCY` in the formula to `× WORKER_BYTES_CONCURRENCY` and add "(M3-D: the `bytes` queue's slots — see Concurrency)". Health section: "`pipeline_jobs_in_flight{job}` on `/metrics` is the in-process evidence that the slots are in use (M3-D)."

- [ ] **Step 2:** loadgen README, after the extractor guidance: "**Concurrency (M3-D).** The worker runs 12 slots (8 `default`, 4 `bytes`); `pipeline_jobs_in_flight` on `:8083/metrics` shows the split in use. G-3's concurrency check (`setup --metadata extractor --deliver`, then `feed`) is run at this concurrency: `process_runs.attempts` stays 1 for succeeded runs, `procrastinate_jobs` holds one `pipeline.process_run_now` per run id, and a burst of N items for one source yields ≤ N runs."

- [ ] **Step 3:** ISSUES I-40 — append a paragraph: "**M3-D (2026-09-<merge day>):** in-process concurrency is 12 across two queues (`default` 8 / `bytes` 4); the one non-atomic ledger leg S-C found (`ingest_files` settled → fetching) is a compare-and-set; ITEMIZE's `flow_stats` bump is batched. Multi-instance stays a deployment option, not a slice: the periodic deferrer dedupes across processes and every claim leg is atomic (S-C's audit + M3-D's table in the pipeline README). Leader election remains Phase 8." Leave its status emoji unless the entry's own text says what closes it.

- [ ] **Step 4:** FEATURES M3 sentence: "M3-D (2026-09-<merge day>) raised worker concurrency to 12 across a `default` (8) and a `bytes` (4: FETCH/ITEMIZE/deliver) queue, made the FETCH claim a compare-and-set, batched ITEMIZE's `flow_stats` writes, sized the blocking-call executor to the slots, and added `pipeline_jobs_in_flight`; measured numbers in `TODO.md` "M3-D landed"."

- [ ] **Step 5:** `uv run ruff check .` (docs only, still run) → commit: `docs(pipeline): M3-D — concurrency, the two queues, the memory envelope multiplier, the shared-state audit; I-40 updated`

---

### Task 6: Measure, the G-3 check, merge (lead only, Docker)

**Files:** `TODO.md` ("M3-D landed" note under Discovered follow-ups; tick M3-D; the G queue's G-3 owed-check line), `docs/FEATURES.md` / `docs/ISSUES.md` dates.

- [ ] Merge `ai/m3-d-concurrency` into `ai/main` `--no-ff`; `npm run verify`; pytest + ruff. Deploy: `docker compose build pipeline && docker compose up -d pipeline`; confirm the startup log carries `worker_concurrency: 12, worker_bytes_concurrency: 4` and no sizing WARNING; the canary is fresh.
- [ ] **Throughput + memory** (`set -a; source .env; set +a`, from `services/pipeline/`): `uv run python -m pipeline.loadgen --label m3d setup --mode copy --metadata defaults_only`; `feed --rate 0 --count 2000 --asset-bytes 65536` (saturation) while `docker stats stac-higher-pipeline-1 --no-stream` is sampled every 5 s into a file and `curl -s :8083/metrics | grep jobs_in_flight` every 10 s; then `feed --rate 30 --count 1800` with `watch --seconds 120 --interval 20`. Record: items/s at saturation vs M3-B's baseline (kept pace with 30/s; the pre-pool baseline lagged at 21–24/s), peak RSS and whether it is flat across the sustained window, max `jobs_in_flight` per queue (≤ 8 default, ≤ 4 bytes), `db_pool` `requests_waiting` from `/health`, any `PoolTimeout` in the pipeline log, and the duplicate-work check — `flow_stats->>'items'` for the loadgen association equals the pgstac item count for its collection (a double FETCH would over-count).
- [ ] **G-3 owed check** (G-3 plan Task 6): `--label m3dx setup --metadata extractor --deliver`, `feed --rate 30 --count 300 --profile opaque`; then `select id, attempts from stac_higher.process_runs where attempts > 1 and status = 'succeeded'` is empty; `select count(*), count(distinct (args->>'run_id')) from procrastinate.procrastinate_jobs where task_name = 'pipeline.process_run_now'` are equal; a burst of N items for one source produced ≤ N runs. Record in TODO.md's G queue and tick the owed line.
- [ ] `teardown` both labels. Write the "M3-D landed" note (deviations, every number, owed steps); tick M3-D; update `docs/FEATURES.md` dates; commit docs; the K queue's K-4 is now unblocked (K plan text).

---

## Self-review

- **Spec coverage:** §3 M3-D row — concurrency as a setting default 12 (T1, T2); byte-heavy stages on their own queue with a smaller concurrency (T2: `bytes` = 4); `ingest_files` compare-and-set claim (T3); `flow_stats` batching per §7.4 (T4); "the review must go beyond S-C's audit" — module-level state, shared clients, `to_thread` sizing (T2 executor + T5 audit table; the final whole-branch review is pointed at the table); the G-3 loadgen concurrency check (T6); operational contract in the pipeline README (T5, spec §5). §5's "concurrency is a setting with a conservative default so a deployment can back it out without a release" — T1. Spec §7 decision 2's envelope formula — amended multiplier in T5 with the reasoning.
- **Placeholder scan:** the one "read the file first" instruction per task names the file and the fact to read (the fake adapter's `public_object_url`, `ItemizeOutcome`'s fields, the `ExplodingQueue` stub, `register`'s real signature); the docs task's "<merge day>" is filled by the lead at Task 6.
- **Type consistency:** `transition_ledger(entry_id, *, expected_status, status, **fields) -> bool` is the same in the ABC, the Pg impl, the fake and both `fetch.py` call sites; `FlowStatsBatcher.add` / `FlowDelta.add` share the `bump_flow_stats` keyword set; `run_worker(*, concurrency, bytes_concurrency)` matches `main.run()`'s call and the backend test; `QUEUE_DEFAULT`/`QUEUE_BYTES` are the strings Procrastinate's `Task.queue` carries (`"default"` is its own default, so untouched registrations are unchanged).
- **Known trade recorded, not hidden:** two periodic deferrers (one per worker) — deduped by the UNIQUE defer row, one extra "already deferred" debug line per tick. Recorded in T2's docstring and T5's README.
