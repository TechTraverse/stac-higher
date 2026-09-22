# M3-G · Queue-Table Retention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Procrastinate's own tables stop growing without bound (~7.1 GB/day at the M3 budget, on the catalog's instance): succeeded jobs are deleted as they finish, stragglers are pruned hourly, failed jobs are kept for a bounded debugging window, and the table's size is a metric.

**Architecture:** Two mechanisms Procrastinate already ships, configured: `Worker(delete_jobs="successful")` on BOTH of M3-D's workers (the row and its three events go the moment `procrastinate_finish_job_v1` runs — `procrastinate_events.job_id` cascades), and a periodic `pipeline.queue_retention` job (hourly, offset like the M2-G sweep) that calls `JobManager.delete_old_jobs` twice: succeeded/cancelled/aborted stragglers older than `QUEUE_RETENTION_HOURS` (24), and — by lead ruling — failed jobs older than `QUEUE_FAILED_RETENTION_HOURS` (720 = 30 days; `0` keeps them forever, the spec's letter). The job also sets a `pipeline_queue_jobs_rows{status}` gauge from one `count(*) … GROUP BY status`, which is the backlog assertion the spec asks of this slice. The `QueueBackend` ABC gains `delete_old_jobs` and `count_jobs_by_status` (no-ops on `InMemoryQueue`) so the job module stays backend-agnostic.

**Tech Stack:** Procrastinate 3.9 (`WorkerOptions.delete_jobs`, `JobManager.delete_old_jobs(nb_hours, queue, include_failed, include_cancelled, include_aborted)`), psycopg 3, prometheus_client, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §3 (M3-G row: "`delete_jobs` policy on the worker (successful only) + a periodic `delete_old_jobs`, keeping failures"); `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-F "The real risk: nothing prunes the queue's tables" (3 events/job, ~660 B/job, 43.2M rows/day); GitHub issue #6 "M3-G · queue-table retention".

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/m3-g-queue-retention -b feat/m3-g-queue-retention main` (GitHub issue #6) — only after **M3-D (#4) has merged** (Task 0 checks `run_worker(*, concurrency, bytes_concurrency)` exists on `ai/main`: the two `run_worker_async` calls are where `delete_jobs` goes). Pipeline-only; no `npm install`.
- **Gates:** `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/`. Teammates never run e2e, the dev server, or Docker.
- **Settled values:** `QUEUE_DELETE_JOBS` default `successful` (accepted: `never` | `successful` | `always` — the `DeleteJobCondition` names; `always` is allowed for a deployment that wants no history at all, but the default keeps failures); `QUEUE_RETENTION_HOURS` default **24** (succeeded / cancelled / aborted stragglers); `QUEUE_FAILED_RETENTION_HOURS` default **720**, `0` = never prune failed. Cron `41 * * * *` (hourly, offset from M2-G's `17 * * * *`).
- **Keep failures (spec):** the worker policy never deletes a failed job; the periodic prunes failed jobs only past `QUEUE_FAILED_RETENTION_HOURS`, never inside it.
- **Metric:** `pipeline_queue_jobs_rows{status}` (Gauge, labels `todo | doing | succeeded | failed | cancelled | aborted`) set on every retention tick from `SELECT status, count(*) FROM <schema>.procrastinate_jobs GROUP BY status`; plus `pipeline_queue_jobs_pruned_total{status}` (Counter) for what the tick removed — `delete_old_jobs` returns nothing, so count before/after per status inside the tick.
- **ADR 0001** is not touched: the queue schema is Procrastinate's, the pipeline runs no DDL on it (the pruning is DML the library ships).
- **No new dependency.** Structured logging: data in `extra={...}`, messages constant; never `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL or name>
  ```

---

### Task 0: Precondition

- [ ] On `main`: `grep -n "async def run_worker(self, \*, concurrency" services/pipeline/src/pipeline/queue/procrastinate_backend.py` matches and `grep -n "install_signal_handlers=False" …` matches twice (M3-D merged). If not, STOP.

### Task 1: Settings + the worker delete policy

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py` (three constants, three fields, three env lines, docstring bullets, a parser for the condition name)
- Modify: `services/pipeline/src/pipeline/queue/procrastinate_backend.py` (`ProcrastinateQueue.delete_jobs: str = "successful"` field; both `run_worker_async` calls pass `delete_jobs=self.delete_jobs`)
- Modify: `services/pipeline/src/pipeline/main.py` (`build_queue` passes `delete_jobs=settings.queue_delete_jobs`)
- Modify: `services/pipeline/README.md` (three env-contract rows), `docker-compose.yml` (three pipeline env lines after the M3-D block)
- Test: `services/pipeline/tests/test_config.py` (append), `services/pipeline/tests/test_procrastinate_backend.py` (extend `test_run_worker_starts_one_worker_per_queue`), `services/pipeline/tests/test_main_jobs.py` (append)

**Interfaces:**
- Produces:
  ```python
  # config.py
  DEFAULT_QUEUE_DELETE_JOBS = "successful"
  DEFAULT_QUEUE_RETENTION_HOURS = 24
  DEFAULT_QUEUE_FAILED_RETENTION_HOURS = 720
  QUEUE_DELETE_JOB_CONDITIONS = ("never", "successful", "always")
  Settings.queue_delete_jobs: str; Settings.queue_retention_hours: int; Settings.queue_failed_retention_hours: int   # 0 = never
  # procrastinate_backend.py
  ProcrastinateQueue(database_url, schema=..., pool_min_size=..., pool_max_size=..., delete_jobs: str = "successful")
  ```

- [ ] **Step 1: Failing tests**

`test_config.py`:
```python
def test_queue_retention_settings_defaults_and_env():
    from pipeline.config import (
        DEFAULT_QUEUE_DELETE_JOBS,
        DEFAULT_QUEUE_FAILED_RETENTION_HOURS,
        DEFAULT_QUEUE_RETENTION_HOURS,
        Settings,
    )

    s = Settings.from_env({})
    assert (s.queue_delete_jobs, s.queue_retention_hours, s.queue_failed_retention_hours) == (
        DEFAULT_QUEUE_DELETE_JOBS, DEFAULT_QUEUE_RETENTION_HOURS, DEFAULT_QUEUE_FAILED_RETENTION_HOURS
    ) == ("successful", 24, 720)
    s = Settings.from_env({"QUEUE_DELETE_JOBS": "never", "QUEUE_RETENTION_HOURS": "6", "QUEUE_FAILED_RETENTION_HOURS": "0"})
    assert (s.queue_delete_jobs, s.queue_retention_hours, s.queue_failed_retention_hours) == ("never", 6, 0)


def test_queue_delete_jobs_rejects_an_unknown_condition():
    import pytest

    from pipeline.config import Settings

    with pytest.raises(ValueError, match="QUEUE_DELETE_JOBS"):
        Settings.from_env({"QUEUE_DELETE_JOBS": "sometimes"})
```
`test_procrastinate_backend.py` — in `test_run_worker_starts_one_worker_per_queue`, after the `install_signal_handlers` assertions add:
```python
    assert by_name["default"]["delete_jobs"] == "successful"
    assert by_name["bytes"]["delete_jobs"] == "successful"
```
and a new test:
```python
@pytest.mark.asyncio
async def test_run_worker_passes_a_configured_delete_policy(monkeypatch):
    queue = ProcrastinateQueue("postgresql://u:p@h/db", delete_jobs="never")
    seen: list[dict] = []

    async def fake_run_worker_async(**kwargs):
        seen.append(kwargs)

    async def fake_open():
        pass

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(queue.app, "run_worker_async", fake_run_worker_async)
    await queue.run_worker(concurrency=3, bytes_concurrency=1)
    assert {k["delete_jobs"] for k in seen} == {"never"}
```
(match the existing test's construction of the queue and its fakes — read `test_run_worker_starts_one_worker_per_queue` and mirror it exactly.)
`test_main_jobs.py`:
```python
def test_build_queue_passes_the_delete_policy_from_settings():
    from pipeline.main import build_queue

    queue = build_queue(Settings.from_env(env={"QUEUE_DELETE_JOBS": "never"}))
    assert queue.delete_jobs == "never"
```

- [ ] **Step 2: Run to verify they fail** — the three files → AttributeError/ImportError/KeyError.

- [ ] **Step 3: Implement**

`config.py` — after the M3-D worker-concurrency constants:
```python
# --- Queue-table retention (M3-G, spec §3 / S-F) ---
# Procrastinate keeps every finished job and its ~3 events forever unless told
# otherwise: ~660 B/job, ~7 GB/day at the M3 budget, on the catalog's instance.
# The worker deletes SUCCEEDED jobs as they finish; the hourly retention job
# prunes stragglers and, after a long window, failed jobs (an operator's
# debugging history — kept, but not forever).
QUEUE_DELETE_JOB_CONDITIONS = ("never", "successful", "always")
DEFAULT_QUEUE_DELETE_JOBS = "successful"
DEFAULT_QUEUE_RETENTION_HOURS = 24
#: 0 = never prune failed jobs.
DEFAULT_QUEUE_FAILED_RETENTION_HOURS = 720
```
a parser beside `_parse_worker_concurrency`:
```python
def _parse_queue_delete_jobs(raw: str) -> str:
    value = raw.strip().lower()
    if value not in QUEUE_DELETE_JOB_CONDITIONS:
        raise ValueError(
            f"QUEUE_DELETE_JOBS must be one of {QUEUE_DELETE_JOB_CONDITIONS}, got {raw!r}"
        )
    return value
```
three `Settings` fields (after `flow_stats_flush_seconds`), three `from_env` lines (`queue_delete_jobs=_parse_queue_delete_jobs(env.get("QUEUE_DELETE_JOBS", DEFAULT_QUEUE_DELETE_JOBS))`, the two ints), three docstring bullets after the M3-D ones.

`procrastinate_backend.py`: a dataclass field `delete_jobs: str = "successful"` (with a comment naming `jobs.DeleteJobCondition`), and `delete_jobs=self.delete_jobs` in BOTH `run_worker_async` calls. `main.py` `build_queue`: `delete_jobs=settings.queue_delete_jobs`.

README rows (Environment contract, after the M3-D rows): `QUEUE_DELETE_JOBS` `successful` — the worker's per-job deletion policy (`never` | `successful` | `always`; M3-G); `QUEUE_RETENTION_HOURS` `24` — hourly prune of succeeded/cancelled/aborted stragglers older than this; `QUEUE_FAILED_RETENTION_HOURS` `720` — failed jobs older than this are pruned; `0` keeps them forever. Compose: three `${VAR:-default}` lines.

- [ ] **Step 4: Gates and commit**
```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/src/pipeline/queue/procrastinate_backend.py services/pipeline/src/pipeline/main.py services/pipeline/README.md docker-compose.yml services/pipeline/tests/test_config.py services/pipeline/tests/test_procrastinate_backend.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(queue): QUEUE_DELETE_JOBS / QUEUE_RETENTION_HOURS / QUEUE_FAILED_RETENTION_HOURS; both workers delete succeeded jobs as they finish (M3-G)"
```

### Task 2: The hourly `queue_retention` job and the rows gauge

**Files:**
- Modify: `services/pipeline/src/pipeline/queue/interface.py` (two ABC methods)
- Modify: `services/pipeline/src/pipeline/queue/memory.py` (no-op implementations)
- Modify: `services/pipeline/src/pipeline/queue/procrastinate_backend.py` (the two implementations)
- Modify: `services/pipeline/src/pipeline/metrics.py` (two metrics + `__all__`)
- Create: `services/pipeline/src/pipeline/jobs/queue_retention.py`
- Modify: `services/pipeline/src/pipeline/main.py` (`build_queue` registers it after `history.register`)
- Test: `services/pipeline/tests/test_queue_retention.py` (new), `services/pipeline/tests/test_procrastinate_backend.py` (append)

**Interfaces:**
- Produces:
  ```python
  # queue/interface.py
  class QueueBackend:
      async def delete_old_jobs(self, *, nb_hours: int, include_failed: bool) -> None: ...   # succeeded+cancelled+aborted always; failed when include_failed
      async def count_jobs_by_status(self) -> dict[str, int]: ...                             # {} on a backend without a table
  # metrics.py
  QUEUE_JOBS_ROWS = Gauge("pipeline_queue_jobs_rows", "...", ["status"])
  QUEUE_JOBS_PRUNED = Counter("pipeline_queue_jobs_pruned_total", "...", ["status"])
  # jobs/queue_retention.py
  JOB_NAME = "pipeline.queue_retention"; CRON = "41 * * * *"
  @dataclass(frozen=True)
  class RetentionResult: before: dict[str, int]; after: dict[str, int]
  async def retention_tick(queue: QueueBackend, *, retention_hours: int, failed_retention_hours: int) -> RetentionResult
  def register(queue: QueueBackend, settings: Settings) -> None
  ```

- [ ] **Step 1: Failing tests**

`tests/test_queue_retention.py`:
```python
"""M3-G: the hourly prune of Procrastinate's own tables, keeping failures for a window."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pipeline.config import Settings
from pipeline.jobs.queue_retention import CRON, JOB_NAME, register, retention_tick
from pipeline.queue.memory import InMemoryQueue


@dataclass
class RecordingQueue(InMemoryQueue):
    """An InMemoryQueue that records prune calls and serves scripted counts."""

    prunes: list[tuple[int, bool]] = field(default_factory=list)
    counts: list[dict[str, int]] = field(default_factory=list)

    async def delete_old_jobs(self, *, nb_hours: int, include_failed: bool) -> None:
        self.prunes.append((nb_hours, include_failed))

    async def count_jobs_by_status(self) -> dict[str, int]:
        return self.counts.pop(0) if self.counts else {}


@pytest.mark.asyncio
async def test_prunes_stragglers_then_failed_past_their_window():
    queue = RecordingQueue(counts=[{"succeeded": 10, "failed": 3}, {"succeeded": 0, "failed": 1}])
    result = await retention_tick(queue, retention_hours=24, failed_retention_hours=720)
    assert queue.prunes == [(24, False), (720, True)]
    assert result.before == {"succeeded": 10, "failed": 3}
    assert result.after == {"succeeded": 0, "failed": 1}


@pytest.mark.asyncio
async def test_zero_failed_retention_never_prunes_failed():
    queue = RecordingQueue(counts=[{"failed": 3}, {"failed": 3}])
    await retention_tick(queue, retention_hours=24, failed_retention_hours=0)
    assert queue.prunes == [(24, False)]


@pytest.mark.asyncio
async def test_rows_gauge_and_pruned_counter_are_set():
    from pipeline.metrics import QUEUE_JOBS_PRUNED, QUEUE_JOBS_ROWS

    queue = RecordingQueue(counts=[{"succeeded": 10, "failed": 3}, {"succeeded": 2, "failed": 3}])
    before = QUEUE_JOBS_PRUNED.labels(status="succeeded")._value.get()
    await retention_tick(queue, retention_hours=24, failed_retention_hours=720)
    assert QUEUE_JOBS_ROWS.labels(status="succeeded")._value.get() == 2
    assert QUEUE_JOBS_ROWS.labels(status="failed")._value.get() == 3
    assert QUEUE_JOBS_PRUNED.labels(status="succeeded")._value.get() - before == 8


def test_job_registered_hourly_offset():
    queue = InMemoryQueue()
    register(queue, Settings.from_env(env={}))
    assert JOB_NAME in queue.periodic
    assert queue.periodic[JOB_NAME].cron == CRON == "41 * * * *"


def test_build_queue_includes_queue_retention():
    from pipeline.main import build_queue

    queue = build_queue(Settings.from_env(env={}))
    assert JOB_NAME in {t for t in queue.app.tasks}
```
(If `prometheus_client`'s `_value.get()` is not how the existing `test_metrics.py` reads a gauge, use that file's helper — e.g. rendering `render_metrics()` and asserting the exposition line — and say so in the report.)

`test_procrastinate_backend.py`:
```python
@pytest.mark.asyncio
async def test_delete_old_jobs_calls_the_manager_twice_shaped(queue: ProcrastinateQueue, monkeypatch):
    calls: list[dict] = []

    async def fake_delete_old_jobs(**kwargs):
        calls.append(kwargs)

    async def fake_open():
        pass

    monkeypatch.setattr(queue, "_ensure_open", fake_open)
    monkeypatch.setattr(queue.app.job_manager, "delete_old_jobs", fake_delete_old_jobs)
    await queue.delete_old_jobs(nb_hours=24, include_failed=False)
    await queue.delete_old_jobs(nb_hours=720, include_failed=True)
    assert calls == [
        {"nb_hours": 24, "queue": None, "include_failed": False, "include_cancelled": True, "include_aborted": True},
        {"nb_hours": 720, "queue": None, "include_failed": True, "include_cancelled": True, "include_aborted": True},
    ]
```

- [ ] **Step 2: Run to verify they fail** — ImportError on `pipeline.jobs.queue_retention`; `InMemoryQueue` has no `delete_old_jobs`.

- [ ] **Step 3: Implement**

`queue/interface.py` (after `run_worker`):
```python
    async def delete_old_jobs(self, *, nb_hours: int, include_failed: bool) -> None:  # noqa: B027 — optional hook
        """M3-G: prune finished jobs older than ``nb_hours`` — succeeded,
        cancelled and aborted always; failed only when asked. A backend
        without durable job rows has nothing to prune."""

    async def count_jobs_by_status(self) -> dict[str, int]:
        """M3-G: rows per job status, for the retention gauge. ``{}`` when the
        backend keeps no rows."""
        return {}
```
`queue/memory.py`: nothing to add if the ABC defaults are concrete (they are, above) — verify `InMemoryQueue` still instantiates.

`procrastinate_backend.py`:
```python
    async def delete_old_jobs(self, *, nb_hours: int, include_failed: bool) -> None:
        await self._ensure_open()
        await self.app.job_manager.delete_old_jobs(
            nb_hours=nb_hours,
            queue=None,
            include_failed=include_failed,
            include_cancelled=True,
            include_aborted=True,
        )

    async def count_jobs_by_status(self) -> dict[str, int]:
        await self._ensure_open()
        rows = await self.app.job_manager.connector.execute_query_all_async(
            f"SELECT status::text AS status, count(*) AS n FROM {self.schema}.procrastinate_jobs GROUP BY status"
        )
        return {str(r["status"]): int(r["n"]) for r in rows}
```
(Check the connector's query API in the installed package — `execute_query_all_async(query, **arguments)` on `PsycopgConnector`; the schema name is already validated by the constructor's `rejects_unsafe_schema_name` guard, so the f-string is safe; if the connector sets `search_path` to the schema, the bare table name works too — use whichever the existing code does for its own schema-qualified statements, e.g. `check_connection`.)

`metrics.py`:
```python
QUEUE_JOBS_ROWS = Gauge(
    "pipeline_queue_jobs_rows",
    "Rows in procrastinate_jobs per status after the last retention tick (M3-G)",
    ["status"],
    registry=REGISTRY,
)
QUEUE_JOBS_PRUNED = Counter(
    "pipeline_queue_jobs_pruned_total",
    "procrastinate_jobs rows removed by the retention tick, per status (M3-G)",
    ["status"],
    registry=REGISTRY,
)
```
+ `__all__`.

`jobs/queue_retention.py`:
```python
"""Queue-table retention (M3-G, spec §3, S-F).

Procrastinate retains every finished job and its events forever unless told
otherwise — ~7 GB/day at the M3 budget, on the catalog's own instance, and a
claim path working against a table that is 99.9 % dead rows. The worker
already deletes SUCCEEDED jobs as they finish (``QUEUE_DELETE_JOBS``); this
hourly tick prunes the stragglers and, after a long window, failed jobs —
an operator's debugging history, kept for ``QUEUE_FAILED_RETENTION_HOURS``,
not forever. The rows gauge is the slice's backlog assertion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline.config import Settings
from pipeline.metrics import QUEUE_JOBS_PRUNED, QUEUE_JOBS_ROWS
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.queue_retention"
CRON = "41 * * * *"  # hourly, offset from history_retention's :17


@dataclass(frozen=True)
class RetentionResult:
    before: dict[str, int]
    after: dict[str, int]


async def retention_tick(
    queue: QueueBackend, *, retention_hours: int, failed_retention_hours: int
) -> RetentionResult:
    before = await queue.count_jobs_by_status()
    await queue.delete_old_jobs(nb_hours=retention_hours, include_failed=False)
    if failed_retention_hours > 0:
        await queue.delete_old_jobs(nb_hours=failed_retention_hours, include_failed=True)
    after = await queue.count_jobs_by_status()
    for status, n in after.items():
        QUEUE_JOBS_ROWS.labels(status=status).set(n)
    for status, n in before.items():
        removed = n - after.get(status, 0)
        if removed > 0:
            QUEUE_JOBS_PRUNED.labels(status=status).inc(removed)
    return RetentionResult(before=before, after=after)


def register(queue: QueueBackend, settings: Settings) -> None:
    async def queue_retention(timestamp: int) -> None:
        result = await retention_tick(
            queue,
            retention_hours=settings.queue_retention_hours,
            failed_retention_hours=settings.queue_failed_retention_hours,
        )
        logger.info(
            "queue retention pruned finished jobs",
            extra={
                "before": result.before,
                "after": result.after,
                "scheduled_timestamp": timestamp,
            },
        )

    queue.register_periodic(queue_retention, name=JOB_NAME, cron=CRON)
```
`main.py` `build_queue`: `queue_retention.register(queue, settings)` after `history.register(...)`, with a one-line comment.

- [ ] **Step 4: Gates and commit**
```bash
git add services/pipeline/src/pipeline/queue/interface.py services/pipeline/src/pipeline/queue/memory.py services/pipeline/src/pipeline/queue/procrastinate_backend.py services/pipeline/src/pipeline/metrics.py services/pipeline/src/pipeline/jobs/queue_retention.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_queue_retention.py services/pipeline/tests/test_procrastinate_backend.py
git commit -m "feat(queue): hourly queue_retention job — delete_old_jobs for stragglers and (after 30 d) failed jobs; pipeline_queue_jobs_rows gauge (M3-G)"
```

### Task 3: Docs

**Files:**
- Modify: `services/pipeline/README.md` — a `## Queue-table retention (M3-G)` section after `## Concurrency (M3-D)` (M3-D's docs task adds it; if it is absent, after `## Memory envelope (M3-C)`)
- Modify: `docs/decisions/README.md` — one line under ADR 0012's invariant noting the queue's tables are retained by M3-G's policy, not by `history_retention`
- Modify: `docs/backend.md` — if it lists pipeline periodic jobs, add `pipeline.queue_retention` (hourly :41)

- [ ] **Step 1:** README section:
```markdown
## Queue-table retention (M3-G)

Procrastinate keeps every finished job and its ~3 events unless told
otherwise (S-F measured ~660 B/job — ~7 GB/day at the M3 budget, on the
catalog's instance). Two settings bound it: `QUEUE_DELETE_JOBS`
(`successful` — both workers delete a succeeded job the moment it finishes;
`never` keeps everything, `always` keeps nothing) and the hourly
`pipeline.queue_retention` tick (`:41`), which prunes succeeded / cancelled /
aborted stragglers older than `QUEUE_RETENTION_HOURS` (24) and failed jobs
older than `QUEUE_FAILED_RETENTION_HOURS` (720 = 30 days; `0` keeps failures
forever). Failed jobs are the history an operator debugs from — the default
keeps them for a month, never deletes them at finish. `GET :8083/metrics`
exposes `pipeline_queue_jobs_rows{status}` after every tick and
`pipeline_queue_jobs_pruned_total{status}`; at steady state `succeeded` stays
near zero. M2-G's `history_retention` (ADR 0012) covers the application's
tables only.
```
- [ ] **Step 2:** the ADR 0012 index note and the backend.md row (if applicable). Gates (ruff only — docs) and commit: `docs(pipeline): queue-table retention section (M3-G)`.

### Task 4: Deploy, backlog assertion, merge (lead only, Docker)

- [ ] Worktree gates; rebase onto `main`; verify + pytest + ruff; push, open the PR (`Closes #6`), squash-merge when CI is green; `docker compose build pipeline && docker compose up -d pipeline`.
- [ ] **Backlog assertion (spec §3):** record `SELECT status, count(*) FROM procrastinate.procrastinate_jobs GROUP BY status` and `pg_total_relation_size` of both tables before; run `loadgen --label m3g setup --mode copy` + `feed --profile metadata --rate 0 --count 2000` (2000 items → ~4 000 ingest jobs + deliveries); after the run: `succeeded` rows ≈ 0 (deleted at finish), the two tables' size flat within one tick; then set `QUEUE_DELETE_JOBS=never` for the test? No — instead insert 50 synthetic finished rows aged 48 h via SQL (`INSERT INTO procrastinate.procrastinate_jobs (queue_name, task_name, status, ...)` plus a `procrastinate_events` row with `at = now() - interval '48 hours'`, one `failed` set aged 31 days), trigger the retention tick (`docker compose restart pipeline` then wait for :41, or invoke `retention_tick` from a one-off `uv run python -c` against the stack), and confirm: the 48 h succeeded rows are gone, the failed rows younger than 30 d remain, the 31 d failed row is gone; `pipeline_queue_jobs_rows` reflects it. Teardown loadgen; delete the synthetic rows.
- [ ] PR body = the landed note (the failed-retention ruling, the numbers); `docs/FEATURES.md`; `docs/ISSUES.md` if anything is left; worktree removal; canary.

## Self-review

- Spec §3 M3-G row: worker `delete_jobs` successful-only (T1) + periodic `delete_old_jobs` (T2), keeping failures (the worker never deletes failed; the periodic keeps them 30 days by ruling — recorded in the constraints and the landed note). S-F's backlog assertion → the rows gauge + T4's measurement.
- Type consistency: `delete_old_jobs(*, nb_hours, include_failed)` and `count_jobs_by_status()` identical on the ABC, `ProcrastinateQueue`, the `RecordingQueue` test double and `retention_tick`; `RetentionResult(before, after)`; metric names match between `metrics.py`, the tick and the README.
- Placeholders: none.
