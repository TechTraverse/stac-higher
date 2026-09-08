# M3-B · Connection Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every pipeline repo statement runs on a pooled `psycopg` connection instead of a freshly-forked one, so the ~14 connects per ingested item (4.19 ms each) collapse to ~0 and M3-D's concurrency raise does not turn connection churn into a `max_connections` incident.

**Architecture:** One new module, `src/pipeline/db/pool.py`, holds a process-wide registry of `psycopg_pool.AsyncConnectionPool` keyed by DSN. Pools are created with `open=False` and opened lazily under an `asyncio.Lock` on first use, so a DB-gated test or a CLI script gets a working pool with no wiring and a process that never touches Postgres opens nothing. Every pooled connection runs M3-A's `configure_pgstac_session_async` as its `configure` hook — spec §4.2's stated home for the two pgstac session GUCs. The twelve repo `_connect()` helpers each change one body (`return (await get_async_pool(self.database_url)).connection()`); because `pool.connection()` is an async context manager with the same commit-on-success / rollback-on-exception semantics as `async with await AsyncConnection.connect(url)`, **not one call site and not one line of SQL changes**. `main.run()` closes the pools in its `finally`; `/health` grows a `db_pool` block so the pool is visible from outside the process.

**Tech Stack:** Python 3.12, psycopg 3.3.4, psycopg-pool 3.3.1, pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff, FastAPI (health app), uv.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` (§3 slices + dependency spine, §4.2 "the two `SET`s belong in the pool's `on_connect` hook", §5 testing & risk, §7 settled decisions) and `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-D ("The cost the brief did not list: no connection pooling", and the write-amplification table). Queue text: `TODO.md:101-107`.

## Global Constraints

- **Worktree off `ai/main`:** `git worktree add .claude/worktrees/m3-b-pool -b ai/m3-b-pool ai/main`. `npm install` at the worktree root is **not** needed for a pipeline-only slice.
- **Gates, all three, before merge:** from the worktree root `npm run verify` (the CI gate — it must still pass even though no TypeScript changes); from `services/pipeline/` `uv run pytest` **and** `uv run ruff check .`.
- **M3-A is a hard precondition**, and it lands first (plan: `docs/superpowers/plans/2026-09-07-m3-a-pgstac-write-path.md`). Task 0 verifies it is on `ai/main` before anything else.

  **Landed by M3-A — consume it, do not re-create it:**
  1. `services/pipeline/pyproject.toml` already lists `"psycopg[binary]>=3.2,<4"` and `"psycopg-pool>=3.2,<4"` as explicit dependencies (M3-A Task 1 Step 1). **Verify present; add only if missing, and do not re-pin them.**
  2. `src/pipeline/db/__init__.py` and `src/pipeline/db/pgstac_session.py` exist, exporting `PGSTAC_SESSION_SQL`, `configure_pgstac_session` (sync) and `configure_pgstac_session_async` (async). Each hook executes the two `SET`s **and then COMMITs** — `SET` is transactional, so an uncommitted one would be undone by the pool's `reset`. That commit is what makes it safe for this slice to leave `reset` at its default.
  3. `src/pipeline/stac/pgstac_writer.py` holds the **sync** writer pool: `WRITER_POOL_MAX = 4`, `writer_pool(dsn)`, `close_writer_pools()` (a `psycopg_pool.ConnectionPool` with `open=True`, `name="pgstac-writer"`, `configure=configure_pgstac_session`). `main.run()`'s `finally` already calls `close_writer_pools()` before `queue.aclose()`; this slice's `close_pools()` goes **beside** it, not instead of it.
  4. `src/pipeline/stac/query_queue.py` holds `PgPgstacQueueRepo(database_url)`, whose `_connect()` opens `psycopg.AsyncConnection.connect(url, autocommit=True)`. Its job is `src/pipeline/jobs/pgstac_drain.py` (`pipeline.pgstac_queue_drain`).
  5. `src/pipeline/metrics.py` already imports `Gauge` (three pgstac queue metrics). This slice adds none.
- **Two pools, deliberately — do not merge them.** M3-A's writer pool is **sync** (pypgstac is synchronous; the upsert runs in `asyncio.to_thread`) and pypgstac sets `autocommit=True` on its checkouts. This slice's pool is **async** and transactional, and serves the repos. Both carry the same GUCs through the same hook module; they cannot share a pool.
- **Schema ownership (ADR 0001):** the pipeline never runs DDL. Nothing in this slice issues `CREATE`/`ALTER`/`DROP`.
- **No behaviour change to any SQL.** Transaction boundaries are preserved *by construction*: `pool.connection()` yields the connection inside `async with conn:`, so psycopg's connection `__aexit__` commits on success and rolls back on exception exactly as it does for a directly-opened connection — the only difference is that `putconn` returns it to the pool afterwards instead of closing it. If a step needs to edit SQL, the step is wrong.
- **Pipeline logging:** structured data goes in `extra={...}`, never interpolated into the message.
- **Never pooled, deliberately — the four exemptions:**
  1. Procrastinate's `PsycopgConnector` — it owns its own psycopg pool.
  2. The dispatch listener's dedicated autocommit LISTEN connection (`src/pipeline/dispatcher/listener.py:42`) — it is held open for the process lifetime and must never be returned to a pool.
  3. `ProcrastinateQueue.setup()` and `check_connection()` (`src/pipeline/queue/procrastinate_backend.py:96` and `:128`) — the first runs before anything else exists, the second **is** the health probe and must not report a pool's cached liveness as the database's.
  4. **`PgPgstacQueueRepo._connect()` in `src/pipeline/stac/query_queue.py` (M3-A) — stays direct, autocommit.** `CALL pgstac.run_queued_queries()` is a PROCEDURE that COMMITs inside itself, which Postgres refuses inside a transaction block; this slice's pool is transactional, so putting that repo on it would break the drainer. M3-A's own docstring says so ("M3-B: keep this off the transactional repo pool for that reason").
- **No new dependency is actually added**: psycopg 3.3.4 and psycopg-pool 3.3.1 are already installed transitively via `pypgstac[psycopg]==0.9.11`, and M3-A already made them explicit.
- **`/health` gets no Prometheus gauge.** `pool.get_stats()` is already a dict; `/health` is the operator-visible evidence. YAGNI — even though M3-A has now imported `Gauge` into `metrics.py`, do not reach for it here.
- **Line numbers in this plan are pre-M3-A.** M3-A inserts into `config.py` (constants after line 85, fields after `gc_batch_items`, `from_env` after `gc_batch_items=`), `main.py` (imports and one `register` call) and `metrics.py`. Every reference below names the **anchor symbol or text** as well as the number: trust the anchor, re-derive the number.
- **Commit messages** end with the executing session's attribution trailer:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```
  (Executor: substitute your own model name and session URL. Do not copy a URL from this plan — there is none.)

---

### Task 0: Worktree + M3-A precondition check

**Files:** none (verification only).

**Interfaces:**
- Consumes, all from M3-A: `pipeline.db.pgstac_session` (`PGSTAC_SESSION_SQL: tuple[str, str]`, `configure_pgstac_session(conn: psycopg.Connection) -> None`, `async configure_pgstac_session_async(conn: psycopg.AsyncConnection) -> None` — each executes both `SET`s then COMMITs); `pipeline.stac.pgstac_writer.close_writer_pools() -> None`; `pipeline.stac.query_queue.PgPgstacQueueRepo`.
- Produces: a worktree at `.claude/worktrees/m3-b-pool` on branch `ai/m3-b-pool`.

- [ ] **Step 1: Create the worktree**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher
git worktree add .claude/worktrees/m3-b-pool -b ai/m3-b-pool ai/main
cd .claude/worktrees/m3-b-pool/services/pipeline
uv sync --extra dev
```

- [ ] **Step 2: Verify M3-A landed**

Run from `services/pipeline/` in the worktree:

```bash
uv run python -c "
import inspect
from pipeline.db.pgstac_session import (
    PGSTAC_SESSION_SQL, configure_pgstac_session, configure_pgstac_session_async,
)
from pipeline.stac.pgstac_writer import WRITER_POOL_MAX, close_writer_pools, writer_pool
from pipeline.stac.query_queue import PgPgstacQueueRepo
print(PGSTAC_SESSION_SQL)
print('async hook:', inspect.iscoroutinefunction(configure_pgstac_session_async))
print('writer pool max:', WRITER_POOL_MAX)
"
grep -n 'psycopg\[binary\]\|psycopg-pool' pyproject.toml
grep -n 'close_writer_pools' src/pipeline/main.py
```

Expected output:

```
('SET pgstac.use_queue TO TRUE', 'SET pgstac.update_collection_extent TO TRUE')
async hook: True
writer pool max: 4
```
plus two `pyproject.toml` hits (`"psycopg[binary]>=3.2,<4"`, `"psycopg-pool>=3.2,<4"`) and two `main.py` hits (the import and the call inside `run()`'s `finally`).

**If any of that is missing, STOP.** M3-A has not merged to `ai/main`. Do not stub `pgstac_session`, do not write the two `SET`s inline here (the whole point of spec §4.2 is that one module owns them), and do not build the writer pool yourself. Report the blocker and wait.

Also note for later tasks what already exists, so you do not re-create it:

```bash
ls src/pipeline/db/__init__.py src/pipeline/db/pgstac_session.py \
   src/pipeline/stac/query_queue.py src/pipeline/jobs/pgstac_drain.py
```
Expected: all four present.

- [ ] **Step 3: Establish the pytest/ruff baseline**

```bash
uv run pytest -q
uv run ruff check .
```
Expected: both green. Anything already failing on `ai/main` is not yours to fix in this slice — record it and continue.

---

### Task 1: The two pool settings (and a dependency check)

**Files:**
- Verify (usually no edit): `services/pipeline/pyproject.toml`, the `dependencies` array — M3-A added `psycopg[binary]`/`psycopg-pool`
- Modify: `services/pipeline/src/pipeline/config.py` — module docstring env contract (lines 1-30, anchor: the `EGRESS_ALLOW_HOSTS` bullet); a new constants block after `DEFAULT_FLOW_STATS_RETENTION_DAYS = 400` (pre-M3-A line 145, the **last** constant in the file); two dataclass fields after `flow_stats_retention_days` (pre-M3-A line 232, the **last** field); two `from_env` kwargs after the `flow_stats_retention_days=...` block (pre-M3-A ends line 361, the **last** kwarg). All three anchors are at the end of their region, which is where M3-A does *not* insert (it inserts around `gc_batch_items`) — so there is no conflict, only a line-number shift.
- Test: `services/pipeline/tests/test_config.py` (append)

**Interfaces:**
- Produces:
  ```python
  DEFAULT_DB_POOL_MIN: int = 2
  DEFAULT_DB_POOL_MAX: int = 16
  Settings.db_pool_min: int   # env DB_POOL_MIN
  Settings.db_pool_max: int   # env DB_POOL_MAX
  ```
  Task 2's `get_async_pool` reads exactly these two field names off `Settings.from_env()`.

- [ ] **Step 1: Write the failing test**

Append to `services/pipeline/tests/test_config.py`:

```python
def test_db_pool_defaults():
    """M3-B: the async repo pool is sized from env, with a default that already
    clears M3-D's concurrency (12) plus the periodic ticks that overlap it."""
    from pipeline.config import DEFAULT_DB_POOL_MAX, DEFAULT_DB_POOL_MIN, Settings

    settings = Settings.from_env(env={})
    assert settings.db_pool_min == DEFAULT_DB_POOL_MIN == 2
    assert settings.db_pool_max == DEFAULT_DB_POOL_MAX == 16
    # The sizing invariant the README documents: a pool smaller than the
    # concurrent checkouts makes callers wait and then raise PoolTimeout.
    assert settings.db_pool_max > 12


def test_db_pool_env_overrides():
    from pipeline.config import Settings

    settings = Settings.from_env(env={"DB_POOL_MIN": "1", "DB_POOL_MAX": "32"})
    assert settings.db_pool_min == 1
    assert settings.db_pool_max == 32
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_config.py -q -k db_pool`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_DB_POOL_MIN' from 'pipeline.config'`.

- [ ] **Step 3: Add the constants, the fields and the env reads**

In `services/pipeline/src/pipeline/config.py`, insert after `DEFAULT_FLOW_STATS_RETENTION_DAYS = 400` (pre-M3-A line 145; it stays the last constant in the file — M3-A's `PGSTAC_QUEUE_*` constants go in above it, near `GC_BATCH_ITEMS`):

```python
# --- Database connection pool (M3-B, spec §3 / S-D) ------------------------
#: Connections the async repo pool keeps warm. Small: the pool grows on demand
#: and `max_idle` (600 s) trims it back, so a mostly-idle deployment holds two
#: backends, not sixteen.
DEFAULT_DB_POOL_MIN = 2
#: Ceiling on concurrent checkouts. Must exceed the worker's job concurrency
#: (M3-D default 12) plus the periodic ticks that can overlap a job — dispatch
#: poll, flow monitor, history sweep, GC — or a caller waits `pool.timeout`
#: (30 s) and then raises `psycopg_pool.PoolTimeout`. Formula in
#: services/pipeline/README.md: DB_POOL_MAX >= WORKER_CONCURRENCY + 4.
DEFAULT_DB_POOL_MAX = 16
```

Add to the module docstring (`config.py:1-30`), right after the `EGRESS_ALLOW_HOSTS` bullet (pre-M3-A line 15):

```
- ``DB_POOL_MIN`` / ``DB_POOL_MAX`` — size bounds for the process-wide async
  connection pool the repos check out of (M3-B). ``DB_POOL_MAX`` must exceed
  the worker's job concurrency plus the overlapping periodic ticks.
```

Add the two dataclass fields after `flow_stats_retention_days` (pre-M3-A line 232 — the last field):

```python
    #: Async repo connection pool (M3-B) — see the DEFAULT_DB_POOL_* constants.
    db_pool_min: int = DEFAULT_DB_POOL_MIN
    db_pool_max: int = DEFAULT_DB_POOL_MAX
```

Add the two env reads as the last kwargs of `from_env`, after the
`flow_stats_retention_days=...` block (pre-M3-A it ends on line 361):

```python
            db_pool_min=int(env.get("DB_POOL_MIN", str(DEFAULT_DB_POOL_MIN))),
            db_pool_max=int(env.get("DB_POOL_MAX", str(DEFAULT_DB_POOL_MAX))),
```

- [ ] **Step 4: Verify the psycopg dependencies (M3-A landed them)**

```bash
cd services/pipeline
grep -n 'psycopg\[binary\]\|psycopg-pool' pyproject.toml
```
Expected: two hits, `"psycopg[binary]>=3.2,<4"` and `"psycopg-pool>=3.2,<4"`,
added by M3-A Task 1 Step 1. **Leave them exactly as they are** — do not
re-pin to a tighter floor; the lock already resolves 3.3.4 / 3.3.1 and churning
the pin here would produce a lockfile diff for no behavioural reason.

Only if the grep comes back empty (M3-A shipped without them), add them after
the `"pypgstac[psycopg]==0.9.11",` line and re-lock:

```toml
    "psycopg[binary]>=3.2,<4",
    "psycopg-pool>=3.2,<4",
```
```bash
uv lock && uv sync --extra dev
```

- [ ] **Step 5: Run the tests**

```bash
cd services/pipeline
uv run pytest tests/test_config.py -q
uv run ruff check .
uv run python -c "import psycopg, psycopg_pool; print(psycopg.__version__, psycopg_pool.__version__)"
```
Expected: PASS, ruff clean, and `3.3.4 3.3.1`.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/tests/test_config.py
# only if Step 4 actually had to edit them:
git add services/pipeline/pyproject.toml services/pipeline/uv.lock 2>/dev/null || true
git commit -m "feat(pipeline): DB_POOL_MIN/DB_POOL_MAX settings for the async repo pool (M3-B)"
```

---

### Task 2: `pipeline.db.pool` — the process-wide async pool registry

**Files:**
- Create: `services/pipeline/src/pipeline/db/pool.py`
- Test: `services/pipeline/tests/test_db_pool.py` (new)

**Interfaces:**
- Consumes: `pipeline.db.pgstac_session.configure_pgstac_session_async` (M3-A); `Settings.db_pool_min` / `Settings.db_pool_max` (Task 1).
- Produces:
  ```python
  async def get_async_pool(database_url: str) -> psycopg_pool.AsyncConnectionPool
  async def close_pools() -> None
  def pool_stats() -> dict[str, dict[str, int]]   # {redacted dsn identity: get_stats()}
  def pool_name(database_url: str) -> str          # "host:port/dbname", no user, no password
  ```
  Task 3 calls `get_async_pool(...).connection()`; Task 4 calls `close_pools()` and `pool_stats()`.

**Design notes the implementer must not re-decide:**
- `configure` runs **once per new connection**, not per checkout — that is exactly the "on_connect" semantics spec §4.2 asks for.
- `reset` stays at its default (it rolls back any transaction left open on return). Do not pass one. This is safe **because M3-A's hook COMMITs after issuing the two `SET`s**: `SET` is transactional, so an uncommitted GUC would be rolled back by the very first `reset` and the pool would quietly stop carrying it. Task 5's live test is what proves the pairing works.
- `check` stays unset (no liveness round-trip on checkout). A pool connection killed by a database restart raises on first use; the job fails, Procrastinate retries, and the pool replaces the connection. Paying a round-trip per checkout would give back a large slice of what the pool just bought.
- `max_lifetime` (3600 s) and `max_idle` (600 s) stay at their defaults.
- The registry key is the DSN. The **stats** key is `pool_name(dsn)` — the DSN carries a password and `/health` is unauthenticated inside the compose network.

- [ ] **Step 1: Write the failing test**

Create `services/pipeline/tests/test_db_pool.py`:

```python
"""M3-B: the process-wide async connection pool registry.

These tests never touch a database — `psycopg_pool.AsyncConnectionPool` is
replaced by a recording fake, so what is under test is the registry's
behaviour: one pool per DSN, opened once, configured with M3-A's pgstac
session hook, sized from Settings, closed and cleared on shutdown.
"""

from __future__ import annotations

import asyncio

import pytest

from pipeline.db import pool as dbpool

DSN = "postgresql://username:password@localhost:5433/postgis"
OTHER_DSN = "postgresql://username:password@localhost:5433/other"


class FakePool:
    """Records what `get_async_pool` asked psycopg_pool for."""

    instances: list["FakePool"] = []

    def __init__(self, conninfo: str, **kwargs) -> None:
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.name = kwargs.get("name", "fake")
        self.min_size = kwargs.get("min_size")
        self.max_size = kwargs.get("max_size")
        self.opens = 0
        self.closes = 0
        FakePool.instances.append(self)

    async def open(self) -> None:
        self.opens += 1

    async def close(self) -> None:
        self.closes += 1

    def get_stats(self) -> dict[str, int]:
        return {
            "pool_min": self.min_size,
            "pool_max": self.max_size,
            "pool_size": self.min_size,
            "pool_available": self.min_size,
            "requests_waiting": 0,
        }


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch):
    """Empty registry + recording pool class for every test in this module."""
    FakePool.instances = []
    monkeypatch.setattr(dbpool, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(dbpool, "_pools", {})
    yield FakePool
    monkeypatch.setattr(dbpool, "_pools", {})


async def test_one_pool_per_dsn_opened_once():
    first = await dbpool.get_async_pool(DSN)
    second = await dbpool.get_async_pool(DSN)
    assert first is second
    assert first.opens == 1
    assert len(FakePool.instances) == 1


async def test_distinct_dsns_get_distinct_pools():
    a = await dbpool.get_async_pool(DSN)
    b = await dbpool.get_async_pool(OTHER_DSN)
    assert a is not b
    assert len(FakePool.instances) == 2


async def test_concurrent_first_use_creates_exactly_one_pool():
    # The lazy-open path is the one place two coroutines can race: without the
    # lock both would build a pool and one would leak, unopened and unclosed.
    pools = await asyncio.gather(*(dbpool.get_async_pool(DSN) for _ in range(8)))
    assert len({id(p) for p in pools}) == 1
    assert len(FakePool.instances) == 1
    assert FakePool.instances[0].opens == 1


async def test_pool_configures_the_pgstac_session_guc_hook():
    """Spec §4.2: the two pgstac session SETs live in the pool's on_connect."""
    from pipeline.db.pgstac_session import configure_pgstac_session_async

    created = await dbpool.get_async_pool(DSN)
    assert created.kwargs["configure"] is configure_pgstac_session_async
    # Lazy open, never psycopg_pool's implicit one.
    assert created.kwargs["open"] is False


async def test_pool_sizes_come_from_settings(monkeypatch):
    monkeypatch.setenv("DB_POOL_MIN", "3")
    monkeypatch.setenv("DB_POOL_MAX", "21")
    created = await dbpool.get_async_pool(DSN)
    assert (created.min_size, created.max_size) == (3, 21)


async def test_pool_sizes_default_when_env_is_absent(monkeypatch):
    monkeypatch.delenv("DB_POOL_MIN", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    created = await dbpool.get_async_pool(DSN)
    assert (created.min_size, created.max_size) == (2, 16)


def test_pool_name_drops_user_and_password():
    # /health serves this string; the DSN carries a password.
    name = dbpool.pool_name(DSN)
    assert name == "localhost:5433/postgis"
    assert "password" not in name
    assert "username" not in name


def test_pool_name_fills_in_omitted_parts():
    assert dbpool.pool_name("postgresql://database/postgis") == "database:5432/postgis"


async def test_pool_stats_is_keyed_by_the_redacted_name():
    await dbpool.get_async_pool(DSN)
    stats = dbpool.pool_stats()
    assert list(stats) == ["localhost:5433/postgis"]
    assert stats["localhost:5433/postgis"]["pool_max"] == 16


def test_pool_stats_is_empty_before_any_pool_exists():
    assert dbpool.pool_stats() == {}


async def test_close_pools_closes_every_pool_and_clears_the_registry():
    await dbpool.get_async_pool(DSN)
    await dbpool.get_async_pool(OTHER_DSN)

    await dbpool.close_pools()

    assert all(p.closes == 1 for p in FakePool.instances)
    assert dbpool.pool_stats() == {}
    # And a later call builds a fresh pool rather than handing back a closed one.
    await dbpool.get_async_pool(DSN)
    assert len(FakePool.instances) == 3


async def test_close_pools_is_a_no_op_when_nothing_was_opened():
    await dbpool.close_pools()  # must not raise — main.run()'s finally always calls it
    assert dbpool.pool_stats() == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_db_pool.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'pipeline.db.pool'`.

- [ ] **Step 3: Write the module**

Create `services/pipeline/src/pipeline/db/pool.py`:

```python
"""Process-wide async connection pools (M3-B, spec §3; scoping notes M3-S-D).

Every pipeline repo used to open a fresh ``psycopg.AsyncConnection`` per
statement — 4.19 ms measured, ~14 per ingested item, ~420 connections/s at the
M3 budget (≈1.8 core-seconds of connect per wall-clock second) plus a backend
fork each. Invisible at concurrency 1 behind the pgstac call; first-order the
moment M3-D raises concurrency, and a ``max_connections`` risk with it. This
module is the one place a pooled connection comes from.

Lazy by design: a pool is built with ``open=False`` and opened under a lock on
first use, so a DB-gated test or a CLI script that constructs a repo gets a
working pool with no wiring, and a process that never touches Postgres opens
nothing.

Every pooled connection runs ``configure_pgstac_session_async`` once, when it
is created — spec §4.2's stated home for M3-A's two pgstac session GUCs. They
are harmless on the non-pgstac statements the repos issue.

NOT pooled, deliberately:

- Procrastinate's ``PsycopgConnector`` owns its own psycopg pool;
- the dispatch listener holds a dedicated autocommit connection for LISTEN;
- ``ProcrastinateQueue.setup()`` runs before anything else exists, and
  ``check_connection()`` IS the health probe — it must reach the database, not
  report a pool's cached liveness.

ADR 0001: nothing here runs DDL.
"""

from __future__ import annotations

import asyncio
import logging

from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from pipeline.config import Settings
from pipeline.db.pgstac_session import configure_pgstac_session_async

logger = logging.getLogger(__name__)

#: DSN -> open pool. Process-wide: one pool per distinct database, shared by
#: every repo instance, because repos are constructed per job (they are frozen
#: dataclasses holding only a DSN) and must not each own connections.
_pools: dict[str, AsyncConnectionPool] = {}
_lock = asyncio.Lock()


def pool_name(database_url: str) -> str:
    """A DSN identity safe to log and to serve on ``/health``.

    ``get_stats()`` is published under this string, so it carries neither the
    password nor the user — ``/health`` is unauthenticated inside the compose
    network.
    """
    parts = conninfo_to_dict(database_url)
    host = parts.get("host") or "localhost"
    port = parts.get("port") or "5432"
    dbname = parts.get("dbname") or "?"
    return f"{host}:{port}/{dbname}"


async def get_async_pool(database_url: str) -> AsyncConnectionPool:
    """The open pool for ``database_url``, creating and opening it on first use."""
    existing = _pools.get(database_url)
    if existing is not None:
        return existing

    async with _lock:
        # Re-check: another coroutine may have won the race to the lock.
        existing = _pools.get(database_url)
        if existing is not None:
            return existing

        settings = Settings.from_env()
        pool = AsyncConnectionPool(
            database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            open=False,
            # Runs once per NEW connection (not per checkout): spec §4.2.
            configure=configure_pgstac_session_async,
            name=pool_name(database_url),
        )
        await pool.open()
        _pools[database_url] = pool
        logger.info(
            "db pool opened",
            extra={
                "pool": pool.name,
                "min_size": settings.db_pool_min,
                "max_size": settings.db_pool_max,
            },
        )
        return pool


async def close_pools() -> None:
    """Close every pool and clear the registry. Idempotent; safe with none open."""
    async with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        await pool.close()
        logger.info("db pool closed", extra={"pool": pool.name})


def pool_stats() -> dict[str, dict[str, int]]:
    """``psycopg_pool`` stats per pool, keyed by the redacted DSN identity.

    Served on ``/health``. ``pool_min``/``pool_max``/``pool_size``/
    ``pool_available``/``requests_waiting`` are always present; the cumulative
    counters (``connections_num``, ``requests_num``, ``requests_queued``,
    ``usage_ms``, …) appear only once they are non-zero, which is why callers
    must not index them blindly.
    """
    return {pool.name: pool.get_stats() for pool in _pools.values()}
```

`services/pipeline/src/pipeline/db/__init__.py` already exists — M3-A Task 1
created it alongside `pgstac_session.py`. Confirm rather than write:

```bash
cd services/pipeline && ls src/pipeline/db/
```
Expected: `__init__.py  pgstac_session.py  pool.py`. Do not overwrite
`__init__.py`; if it is somehow absent, create it with a one-line docstring
(`"""Database-level plumbing shared by every pipeline runtime (M3-A, M3-B)."""`)
and say so in your report, because that means M3-A landed incompletely.

- [ ] **Step 4: Run the tests**

```bash
cd services/pipeline
uv run pytest tests/test_db_pool.py -q
uv run ruff check .
```
Expected: PASS (12 tests), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/db/ services/pipeline/tests/test_db_pool.py
git commit -m "feat(pipeline): process-wide async psycopg pool with the pgstac session hook (M3-B)"
```

---

### Task 3: Every repo checks out of the pool

**Files:** fourteen edits, each replacing one `psycopg.AsyncConnection.connect(...)` with a pool checkout. The twelve identical `_connect` helpers (`async def _connect(self)` at the line given, body on the two lines below it):

- Modify: `services/pipeline/src/pipeline/notify/repo.py:138-142` (`PgNotifyRepo`)
- Modify: `services/pipeline/src/pipeline/ingest/repo.py:290-294` (`PgIngestRepo`)
- Modify: `services/pipeline/src/pipeline/dispatcher/repo.py:95-99` (`PgDispatchRepo`)
- Modify: `services/pipeline/src/pipeline/connections/repo.py:109-113` (`PgConnectionRepo`)
- Modify: `services/pipeline/src/pipeline/history/sweep.py:97-101` (`PgHistoryRepo`)
- Modify: `services/pipeline/src/pipeline/delivery/backfill.py:158-162` (`PgBackfillRepo`)
- Modify: `services/pipeline/src/pipeline/gc/repo.py:105-109` (`PgGcRepo`)
- Modify: `services/pipeline/src/pipeline/delivery/repo.py:201-205` (`PgDeliveryRepo`)
- Modify: `services/pipeline/src/pipeline/finalize/repo.py:163-167` (`PgFinalizeRepo`)
- Modify: `services/pipeline/src/pipeline/process/repo.py:307-311` (`PgProcessRepo`)
- Modify: `services/pipeline/src/pipeline/flow/daily_repo.py:52-56` (`PgDailyStatsRepo`)
- Modify: `services/pipeline/src/pipeline/flow/repo.py:185-189` (`PgFlowMonitorRepo`)

Plus the two one-off direct connects:

- Modify: `services/pipeline/src/pipeline/jobs/process.py:127-134` (the `build_secret_resolver._resolve` body)
- Modify: `services/pipeline/src/pipeline/stac/pgstac_writer.py:62-73` (`PgPgstacWriter.get_collection_bbox` — the async extent read only; M3-A's `writer_pool` / `_upsert_sync` / `close_writer_pools` in the same file are **not** touched)

**Not touched — the four exemptions from Global Constraints.** In particular
`services/pipeline/src/pipeline/stac/query_queue.py` also defines an
`async def _connect(self)` and it **stays** on
`psycopg.AsyncConnection.connect(url, autocommit=True)`: `CALL
pgstac.run_queued_queries()` COMMITs inside itself and Postgres refuses that
inside a transaction block, which is what this slice's pool gives you. Thirteen
`_connect` helpers exist after M3-A; twelve of them move.

**Interfaces:**
- Consumes: `pipeline.db.pool.get_async_pool(database_url) -> AsyncConnectionPool` (Task 2).
- Produces: nothing new. `_connect()` keeps its name, its `self`-only signature and its await-then-`async with` contract, so **every one of the ~120 `async with await self._connect() as conn:` call sites is untouched** — `pool.connection()` is an async context manager with the same commit/rollback semantics.

- [ ] **Step 1: Write the failing test**

There is no unit-level assertion that a repo pools — the repo methods are all
`# pragma: no cover` psycopg wrappers, and the real proof is Task 5's DB-gated
backend-PID test. What *is* unit-testable, and is the regression that actually
bites (one repo missed in a fourteen-file sweep), is that **no repo opens a raw
connection any more**. Create `services/pipeline/tests/test_db_pool_adoption.py`:

```python
"""M3-B: no pipeline repo opens a raw connection any more.

A fourteen-file sweep is exactly the change where one file gets missed, and a
missed repo is invisible — it works, it is just slow, and it stays slow until
someone re-measures under load. This is the cheap guard.
"""

from __future__ import annotations

import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pipeline"

#: The modules that legitimately open a connection themselves, and why.
#: (Global Constraints lists the same four exemptions with the full reasoning.)
ALLOWED = {
    # Procrastinate owns its own pool; setup() runs before anything else
    # exists and check_connection() IS the health probe — it must reach the
    # database, not report a pool's cached liveness.
    "queue/procrastinate_backend.py",
    # A dedicated autocommit connection held open for LISTEN "item_events".
    "dispatcher/listener.py",
    # M3-A's drainer: `CALL pgstac.run_queued_queries()` COMMITs inside itself
    # and cannot run in a transaction block, so it needs an AUTOCOMMIT
    # connection — which is exactly what the transactional repo pool is not.
    "stac/query_queue.py",
}

#: The twelve repo seams this slice moves onto the pool.
POOLED_REPOS = {
    "connections/repo.py",
    "delivery/backfill.py",
    "delivery/repo.py",
    "dispatcher/repo.py",
    "finalize/repo.py",
    "flow/daily_repo.py",
    "flow/repo.py",
    "gc/repo.py",
    "history/sweep.py",
    "ingest/repo.py",
    "notify/repo.py",
    "process/repo.py",
}


def _relative(path: pathlib.Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_repo_opens_a_raw_async_connection():
    offenders = sorted(
        _relative(path)
        for path in SRC.rglob("*.py")
        if "AsyncConnection.connect(" in path.read_text()
        and _relative(path) not in ALLOWED
    )
    assert offenders == [], (
        "these modules still fork a connection per statement instead of "
        f"checking out of pipeline.db.pool: {offenders}"
    )


def test_every_pooled_repo_helper_checks_out_of_the_pool():
    for rel in sorted(POOLED_REPOS):
        text = (SRC / rel).read_text()
        assert "async def _connect(self)" in text, f"{rel}: the seam was renamed"
        assert "get_async_pool" in text, f"{rel}: still not on the pool"
        assert "AsyncConnection.connect(" not in text, f"{rel}: raw connect left behind"


def test_the_exempt_drainer_is_still_autocommit():
    """M3-A's query_queue repo must NOT be swept onto the transactional pool."""
    text = (SRC / "stac/query_queue.py").read_text()
    assert "AsyncConnection.connect(self.database_url, autocommit=True)" in text
    assert "get_async_pool" not in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_db_pool_adoption.py -q`
Expected: 1 passed (`test_the_exempt_drainer_is_still_autocommit` — M3-A already
satisfies it), 2 failed —
`test_no_repo_opens_a_raw_async_connection` lists all fourteen modules, and
`test_every_pooled_repo_helper_checks_out_of_the_pool` fails on
`connections/repo.py: still not on the pool`.

(Two other files define an `async def _connect(self)` and are deliberately
outside `POOLED_REPOS`: `connections/adapters/sftp.py` — an *asyncssh*
connection, nothing to do with psycopg — and M3-A's `stac/query_queue.py`,
which the third test pins in place. This is why the test enumerates the twelve
by name rather than counting matches: a count would silently absorb either of
them.)

- [ ] **Step 3: Replace the twelve repo helpers**

Each of the twelve is byte-identical today:

```python
    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)
```

Replace each with:

```python
    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()
```

A single sweep does all twelve — run it from `services/pipeline/src/pipeline`,
then read the diff before staging:

```bash
cd services/pipeline/src/pipeline
python3 - <<'PY'
import pathlib

OLD = '''    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)
'''
NEW = '''    async def _connect(self):  # pragma: no cover - thin pool wrapper
        # M3-B: a checkout from the process-wide pool, not a fresh backend.
        # `pool.connection()` is an async context manager with the same
        # commit-on-success / rollback-on-error semantics, so every
        # `async with await self._connect() as conn:` call site is unchanged.
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()
'''

targets = [
    "notify/repo.py", "ingest/repo.py", "dispatcher/repo.py",
    "connections/repo.py", "history/sweep.py", "delivery/backfill.py",
    "gc/repo.py", "delivery/repo.py", "finalize/repo.py",
    "process/repo.py", "flow/daily_repo.py", "flow/repo.py",
]
for rel in targets:
    path = pathlib.Path(rel)
    text = path.read_text()
    assert text.count(OLD) == 1, f"{rel}: expected exactly one helper, found {text.count(OLD)}"
    path.write_text(text.replace(OLD, NEW))
    print("rewrote", rel)
PY
git -C ../../../.. diff --stat -- services/pipeline/src/pipeline
```
Expected: twelve files, `12 files changed, 60 insertions(+), 36 deletions(-)`
(±, depending on M3-A's diff). If the script asserts, a helper drifted — fix
that one file by hand to the exact `NEW` body and re-run.

- [ ] **Step 4: Replace the secret-resolver connect**

`services/pipeline/src/pipeline/jobs/process.py`, inside `build_secret_resolver`.
Current lines 126-134:

```python
    async def _resolve(ref) -> str:  # pragma: no cover - needs a DB + key
        import json

        import psycopg

        # Raises loudly when the key is unset or malformed — a run must never
        # start with a secret it could not resolve.
        key = load_master_key({"CREDENTIALS_MASTER_KEY": settings.credentials_master_key or ""})
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
```

Replace with:

```python
    async def _resolve(ref) -> str:  # pragma: no cover - needs a DB + key
        import json

        from pipeline.db.pool import get_async_pool

        # Raises loudly when the key is unset or malformed — a run must never
        # start with a secret it could not resolve.
        key = load_master_key({"CREDENTIALS_MASTER_KEY": settings.credentials_master_key or ""})
        pool = await get_async_pool(settings.database_url)
        async with pool.connection() as conn:
```

The rest of `_resolve` (the `SELECT credentials …`, the `row` handling, the
`decrypt`) is unchanged — do not touch it. `psycopg` was imported for that one
call and has no other use in the function.

- [ ] **Step 5: Replace the collection-extent read**

`services/pipeline/src/pipeline/stac/pgstac_writer.py`, lines 62-67. Current:

```python
    async def get_collection_bbox(  # pragma: no cover - thin psycopg wrapper
        self, collection_id: str
    ) -> list[float] | None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
```

Replace with:

```python
    async def get_collection_bbox(  # pragma: no cover - thin pool wrapper
        self, collection_id: str
    ) -> list[float] | None:
        # M3-B: the async pool, same as the repos. (The UPSERT path keeps its
        # own SYNC pool from M3-A — pypgstac is synchronous and runs in
        # asyncio.to_thread.)
        from pipeline.db.pool import get_async_pool

        pool = await get_async_pool(self.dsn)
        async with pool.connection() as conn:
```

The `SELECT content->'extent'->'spatial'->'bbox'->0 …`, the `row` guard and
the `[float(v) for v in row[0]]` return are unchanged.

- [ ] **Step 6: Prove no SQL moved**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher/.claude/worktrees/m3-b-pool
git diff -U0 -- services/pipeline/src/pipeline | grep '^[-+]' | grep -iE "SELECT|INSERT|UPDATE|DELETE|CALL |FOR UPDATE|ON CONFLICT"
```
Expected: **no output**. Any hit means a statement moved in a slice whose whole
claim is that none did — revert that hunk.

- [ ] **Step 7: Run the tests**

```bash
cd services/pipeline
uv run pytest -q
uv run ruff check .
```
Expected: PASS across the whole suite (the repo methods are all fake-backed in
unit tests, so nothing here should move), `test_db_pool_adoption.py` green,
ruff clean.

- [ ] **Step 8: Commit**

```bash
git add services/pipeline/src/pipeline services/pipeline/tests/test_db_pool_adoption.py
git commit -m "refactor(pipeline): twelve repos + two one-offs check out of the async pool (M3-B)"
```

---

### Task 4: Pool lifecycle in `main.run()` and a `db_pool` block on `/health`

**Files:**
- Modify: `services/pipeline/src/pipeline/main.py` — imports (anchor: `from pipeline.config import Settings`, pre-M3-A line 16; M3-A adds `pgstac_drain` to the `from pipeline.jobs import (...)` block and `from pipeline.stac.pgstac_writer import close_writer_pools` nearby) and the `finally` block of `run()` (anchor: the `close_writer_pools()` M3-A put there; pre-M3-A lines 102-103)
- Modify: `services/pipeline/src/pipeline/health.py:12-15` (imports) and `:41` (the response body) — M3-A does not touch this file
- Test: `services/pipeline/tests/test_health.py` (append), `services/pipeline/tests/test_main_jobs.py` (append — M3-A also appends here, to assert `pipeline.pgstac_queue_drain` is registered; the two additions are independent)

**Interfaces:**
- Consumes: `pipeline.db.pool.close_pools()`, `pipeline.db.pool.pool_stats()` (Task 2); `pipeline.stac.pgstac_writer.close_writer_pools()` (M3-A).
- Produces: `/health` response gains `"db_pool": {"<host:port/dbname>": {...psycopg_pool stats...}}` — `{}` before any pool exists.

- [ ] **Step 1: Write the failing test**

Append to `services/pipeline/tests/test_health.py`:

```python
def test_health_reports_no_pools_before_any_are_opened(monkeypatch):
    """M3-B: the block is always present, and empty is a truthful answer —
    a process that has not touched Postgres has opened no pool."""
    from pipeline.db import pool as dbpool

    monkeypatch.setattr(dbpool, "_pools", {})
    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    assert body["db_pool"] == {}


def test_health_reports_db_pool_stats(monkeypatch):
    """The pool is session-scoped and otherwise invisible from outside the
    process; this block is the operator-visible evidence it is in use
    (spec §5: observability is the mitigation, not garnish)."""
    from pipeline.db import pool as dbpool

    class StubPool:
        name = "database:5432/postgis"

        def get_stats(self):
            return {
                "pool_min": 2,
                "pool_max": 16,
                "pool_size": 4,
                "pool_available": 3,
                "requests_waiting": 0,
                "connections_num": 4,
            }

    monkeypatch.setattr(
        dbpool, "_pools", {"postgresql://username:password@database:5432/postgis": StubPool()}
    )

    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    assert body["db_pool"] == {
        "database:5432/postgis": {
            "pool_min": 2,
            "pool_max": 16,
            "pool_size": 4,
            "pool_available": 3,
            "requests_waiting": 0,
            "connections_num": 4,
        }
    }
    # The DSN's password must never reach an unauthenticated endpoint.
    assert "password" not in str(body["db_pool"])
```

Append to `services/pipeline/tests/test_main_jobs.py`:

```python
async def test_run_closes_both_pools_before_the_queue(monkeypatch):
    """M3-B: pools are process-wide, so `run()` owns their shutdown.

    Both of them close BEFORE `queue.aclose()`, because Procrastinate's own
    pool is the last thing released and nothing after that point may still
    want a connection. M3-A's SYNC writer pool and M3-B's ASYNC repo pool are
    separate objects and both must be released — this asserts the order rather
    than just the calls, because "closed, eventually" is not the contract.
    """
    import pipeline.main as main_module

    order: list[str] = []

    class ExplodingQueue:
        name = "stub"

        async def setup(self) -> None:
            raise RuntimeError("stop here")

        async def aclose(self) -> None:
            order.append("queue")

    async def fake_close_pools() -> None:
        order.append("async-pools")

    def fake_close_writer_pools() -> None:
        order.append("writer-pools")

    monkeypatch.setattr(main_module, "build_queue", lambda settings: ExplodingQueue())
    monkeypatch.setattr(main_module, "close_pools", fake_close_pools)
    monkeypatch.setattr(main_module, "close_writer_pools", fake_close_writer_pools)

    with pytest.raises(RuntimeError, match="stop here"):
        await main_module.run(Settings.from_env(env={}))

    assert order == ["async-pools", "writer-pools", "queue"]
```

and add `import pytest` to the top of `tests/test_main_jobs.py` (it currently
imports none).

If M3-A already added an equivalent assertion for `close_writer_pools`, keep
both — this one is about ordering across all three, and losing it would let a
later edit drop the async close without a test noticing.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd services/pipeline
uv run pytest tests/test_health.py tests/test_main_jobs.py -q
```
Expected: FAIL — `KeyError: 'db_pool'` in both health tests, and
`AttributeError: <module 'pipeline.main'> does not have the attribute 'close_pools'`
from `monkeypatch.setattr`.

- [ ] **Step 3: Wire the lifecycle**

`services/pipeline/src/pipeline/main.py` — add the import beside
`from pipeline.config import Settings` (pre-M3-A line 16; ruff's isort keeps
`pipeline.db` before `pipeline.health`):

```python
from pipeline.db.pool import close_pools
```

and extend the `finally` block of `run()` — after M3-A it reads
`close_writer_pools()` then `await queue.aclose()`; it becomes:

```python
    finally:
        # Both pools before the queue: `queue.aclose()` releases
        # Procrastinate's own pool, and nothing after that point may still
        # want a connection. The async pool serves the repos (M3-B); the sync
        # one serves the pgstac writer (M3-A) — separate objects, separate
        # runtimes, both ours to release.
        await close_pools()
        close_writer_pools()
        await queue.aclose()
```

`close_writer_pools()` is synchronous — do **not** `await` it.

- [ ] **Step 4: Add the health block**

`services/pipeline/src/pipeline/health.py` — add the import after
`from pipeline import __version__` (line 12):

```python
from pipeline.db.pool import pool_stats
```

and add one key to the `/health` response body, after `"heartbeat"` (line 41):

```python
                "heartbeat": heartbeat_state.as_dict(),
                # M3-B: the repo pool is session-scoped and otherwise
                # invisible from outside the process. `{}` means no pool has
                # been opened yet, which is the truthful answer for a process
                # that has not touched Postgres. Cumulative counters
                # (connections_num, requests_num, …) appear only once non-zero.
                "db_pool": pool_stats(),
```

Do **not** make `db_pool` affect the 200/503 decision: the queue's
`check_connection()` is the database liveness signal, and a pool with zero
connections is a healthy idle process, not a degraded one.

- [ ] **Step 5: Run the tests**

```bash
cd services/pipeline
uv run pytest -q
uv run ruff check .
```
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/main.py services/pipeline/src/pipeline/health.py \
        services/pipeline/tests/test_health.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(pipeline): close pools on shutdown; /health serves a db_pool block (M3-B)"
```

---

### Task 5: DB-gated proof — one backend, and the GUCs are on it

**Files:**
- Create: `services/pipeline/tests/test_integration_pool.py`

**Interfaces:**
- Consumes: `pipeline.db.pool.get_async_pool` / `close_pools` (Task 2); the pooled `_connect` in `PgIngestRepo` (Task 3); `PGSTAC_SESSION_SQL` (M3-A).
- Produces: nothing.

This is the test that actually proves the slice: unit tests can only show what
the registry was *asked* for. Only a live backend shows one PID serving two
calls and `pgstac.use_queue` set on it.

- [ ] **Step 1: Write the failing test**

Create `services/pipeline/tests/test_integration_pool.py`:

```python
"""Connection-pool integration — auto-skips unless DATABASE_URL is set.

Run against the compose Postgres:

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_pool.py

Proves the two things M3-B claims and unit tests cannot: consecutive checkouts
land on the SAME backend (no fork per statement), and the pgstac session GUCs
from spec §4.2 are live on a pooled connection because the pool's `configure`
hook set them when the connection was created.
"""

import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping DB integration tests"
)


@pytest.fixture(autouse=True)
async def clean_pools():
    from pipeline.db.pool import close_pools

    await close_pools()
    yield
    await close_pools()


async def _backend_pid(pool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_backend_pid()")
        row = await cur.fetchone()
    return int(row[0])


async def test_sequential_checkouts_reuse_one_backend():
    from pipeline.db.pool import get_async_pool

    pool = await get_async_pool(DATABASE_URL)
    pids = {await _backend_pid(pool) for _ in range(5)}
    # min_size is 2, so a checkout may land on either warm connection — but
    # five sequential checkouts must never fork five backends.
    assert len(pids) <= 2, pids


async def test_pooled_connection_carries_the_pgstac_session_gucs():
    from pipeline.db.pgstac_session import PGSTAC_SESSION_SQL
    from pipeline.db.pool import get_async_pool

    assert PGSTAC_SESSION_SQL == (
        "SET pgstac.use_queue TO TRUE",
        "SET pgstac.update_collection_extent TO TRUE",
    )

    pool = await get_async_pool(DATABASE_URL)
    async with pool.connection() as conn:
        cur = await conn.execute("SHOW pgstac.use_queue")
        assert (await cur.fetchone())[0] == "true"
        cur = await conn.execute("SHOW pgstac.update_collection_extent")
        assert (await cur.fetchone())[0] == "true"


async def test_a_repo_call_does_not_open_a_new_session():
    """The end-to-end shape: two real repo calls, zero new backends.

    `pg_stat_database.sessions` is cumulative per database, so its delta over
    two repo calls is exactly the number of connections those calls forked.
    Before M3-B the delta was 2; it must now be 0.
    """
    from pipeline.db.pool import get_async_pool
    from pipeline.ingest.repo import PgIngestRepo

    pool = await get_async_pool(DATABASE_URL)  # warm the pool first

    async def sessions() -> int:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sessions FROM pg_stat_database WHERE datname = current_database()"
            )
            return int((await cur.fetchone())[0])

    repo = PgIngestRepo(DATABASE_URL)
    before = await sessions()
    await repo.list_enabled_ingest_associations()
    await repo.list_enabled_ingest_associations()
    after = await sessions()

    assert after - before == 0, f"{after - before} new backend sessions for two repo calls"


async def test_closing_the_pools_lets_a_later_call_reopen():
    from pipeline.db.pool import close_pools, get_async_pool, pool_stats

    pool = await get_async_pool(DATABASE_URL)
    assert pool_stats()[pool.name]["pool_size"] >= 1

    await close_pools()
    assert pool_stats() == {}

    reopened = await get_async_pool(DATABASE_URL)
    assert reopened is not pool
    assert await _backend_pid(reopened) > 0
```

- [ ] **Step 2: Run it to verify it fails (and that it skips without a DSN)**

```bash
cd services/pipeline
uv run pytest tests/test_integration_pool.py -q
```
Expected: `4 skipped` — the gate works.

Then, with the compose stack up (`docker compose up -d --wait` from the repo
root), check out the pre-pool state to see the test earn its keep:

```bash
git stash push -- src/pipeline/ingest/repo.py
DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
  uv run pytest tests/test_integration_pool.py -q
git stash pop
```
Expected: `test_a_repo_call_does_not_open_a_new_session` FAILS with
`2 new backend sessions for two repo calls`. That failure is the measurement.

- [ ] **Step 3: Run it against the pooled code**

```bash
cd services/pipeline
DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
  uv run pytest tests/test_integration_pool.py -q
```
Expected: 4 passed.

If `test_pooled_connection_carries_the_pgstac_session_gucs` fails with
`unrecognized configuration parameter "pgstac.use_queue"`, the pool's
`configure` hook did not run — check that `configure=configure_pgstac_session_async`
survived Task 2 and that M3-A's `SET` statements use `TO TRUE` (a `SET` of an
unknown-to-postgres custom GUC is legal only under a prefixed name, which
`pgstac.*` is).

- [ ] **Step 4: Run the whole gated suite**

```bash
cd services/pipeline
DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest -q
uv run ruff check .
```
Expected: PASS, including `test_integration_db.py` and
`test_integration_itemize.py` (nothing there changed, but they now share a
database with a process that holds pooled connections — this confirms no
interference).

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/tests/test_integration_pool.py
git commit -m "test(pipeline): DB-gated proof that repo calls reuse one backend with the pgstac GUCs (M3-B)"
```

---

### Task 6: Docs and compose

**Files:**
- Modify: `services/pipeline/README.md` — the "Environment contract" table (append at its **end**, after the `PROCESS_RUNTIME_IMAGE_STACTOOLS` row; M3-A inserts its `PGSTAC_QUEUE_*` rows in the middle, after `GC_BATCH_ITEMS`, so there is no conflict), the "Health endpoint" JSON sample (pre-M3-A lines 347-359; M3-A does not touch it), and a new section immediately before "## Docker"
- Modify: `docker-compose.yml` — the pipeline service's `environment:` list, after `- HEALTH_PORT=8083` (pre-M3-A line 261; M3-A inserts its `PGSTAC_QUEUE_*` variables further down, after `CATALOG_HREF_BASE` — no conflict)
- Verify only: `docs/backend.md` (app-scoped env — must stay untouched)

M3-A also edits the README's "Jobs" table and the "Telemetry (M2-H)" list.
This slice touches neither: no new job, no new metric.

**Interfaces:**
- Consumes: `DB_POOL_MIN` / `DB_POOL_MAX` (Task 1), the `/health` `db_pool` block (Task 4).
- Produces: nothing code-facing.

- [ ] **Step 1: Confirm `docs/backend.md` is out of scope**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher/.claude/worktrees/m3-b-pool
grep -nE "PROCESS_RUNTIME_IMAGE|EGRESS_ALLOW_HOSTS|STAGING_BUCKET|GC_BATCH_ITEMS" docs/backend.md
```
Expected: **no output** — `docs/backend.md`'s env table is app-scoped (its
`DATABASE_URL` row is the app's connection string). Pipeline env belongs in
`services/pipeline/README.md`. Leave `docs/backend.md` alone. If this grep
*does* hit, the file has grown a pipeline env section since this plan was
written — add the two rows there too, in the same shape.

- [ ] **Step 2: Add the two env-table rows**

In `services/pipeline/README.md`, append to the **end** of the "Environment
contract" table (after the `PROCESS_RUNTIME_IMAGE_STACTOOLS` row — pre-M3-A
line 65, the last row):

```markdown
| `DB_POOL_MIN` | `2` | Connections the process-wide async pool keeps warm (M3-B). The pool grows on demand and trims back after `max_idle` (600 s), so a mostly-idle deployment holds two backends, not `DB_POOL_MAX`. |
| `DB_POOL_MAX` | `16` | Ceiling on concurrent checkouts. **Size it as `WORKER_CONCURRENCY + 4`** — the worker's job concurrency (M3-D default 12) plus the periodic ticks that can overlap a job (dispatch poll, flow monitor, history sweep, GC). Too small does not error immediately: a caller waits `pool.timeout` (30 s) and then raises `psycopg_pool.PoolTimeout`, which surfaces as a failed job with a queue retry. Watch `requests_waiting` on `/health` — persistently non-zero means the pool is undersized. |
```

- [ ] **Step 3: Document the health block and the pool itself**

In `services/pipeline/README.md`, replace the "Health endpoint" JSON sample
(currently lines 352-359) with:

````markdown
```json
{
  "service": "pipeline",
  "status": "ok",
  "queue": { "backend": "procrastinate", "reachable": true, "error": null },
  "heartbeat": { "count": 3, "last_run_at": "2026-07-14T12:00:00+00:00" },
  "db_pool": {
    "database:5432/postgis": {
      "pool_min": 2, "pool_max": 16, "pool_size": 4,
      "pool_available": 3, "requests_waiting": 0
    }
  }
}
```

`db_pool` (M3-B) reports `psycopg_pool` stats per database, keyed by a
redacted DSN identity (`host:port/dbname` — never the user or password). It is
`{}` until the first pooled connection is opened, and it does **not** affect
the 200/503 decision: the queue's own `check_connection()` is the database
liveness signal, and an idle process with no pool is healthy. Cumulative
counters (`connections_num`, `requests_num`, `requests_queued`, `usage_ms`)
appear only once they are non-zero.
````

Then add a section immediately before "## Docker" (currently line 361):

```markdown
## Connection pooling (M3-B)

Every repo statement checks out of a process-wide `psycopg_pool.AsyncConnectionPool`
(`src/pipeline/db/pool.py`), keyed by DSN and opened lazily on first use.
Before M3-B each repo method forked its own backend — 4.19 ms measured, ~14
per ingested item, ~420 connections/s at the M3 budget (scoping notes M3-S-D).

- Each pooled connection runs the two pgstac session GUCs once, when it is
  created (`configure=configure_pgstac_session_async`, spec §4.2). They are
  harmless on the non-pgstac statements the repos issue.
- `pool.connection()` commits on success and rolls back on exception, exactly
  as a directly-opened connection does — transaction boundaries are unchanged.
- **Not pooled, deliberately:** Procrastinate's `PsycopgConnector` (it owns
  its own pool); the dispatch listener's dedicated autocommit LISTEN
  connection; `ProcrastinateQueue.setup()` / `check_connection()` — the first
  runs before anything else exists, the second *is* the health probe; and the
  pgstac queue drainer (`stac/query_queue.py`), because `CALL
  pgstac.run_queued_queries()` COMMITs inside itself and Postgres refuses that
  inside a transaction block, so it needs an autocommit connection.
- The pgstac UPSERT path keeps a separate **sync** pool (M3-A,
  `writer_pool()` / `WRITER_POOL_MAX = 4` in `stac/pgstac_writer.py`):
  pypgstac is synchronous, runs inside `asyncio.to_thread`, and sets
  `autocommit=True` on its checkouts. Both pools carry the same GUCs through
  the same `db/pgstac_session.py` hook.
- `main.run()` closes both pools in its `finally`, before `queue.aclose()`.
```

- [ ] **Step 4: Add the compose variables**

In `docker-compose.yml`, in the `pipeline` service's `environment:` list,
immediately after `- HEALTH_PORT=8083` (line 261):

```yaml
      # M3-B: the process-wide async connection pool the repos check out of.
      # DB_POOL_MAX must exceed the worker's job concurrency plus the periodic
      # ticks that overlap it (WORKER_CONCURRENCY + 4); undersizing it does not
      # error, it makes callers wait 30 s and then raise PoolTimeout. Watch
      # `requests_waiting` in GET :8083/health.
      - DB_POOL_MIN=${DB_POOL_MIN:-2}
      - DB_POOL_MAX=${DB_POOL_MAX:-16}
```

- [ ] **Step 5: Verify compose still parses**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher/.claude/worktrees/m3-b-pool
docker compose config --quiet
```
Expected: no output, exit 0. (If Docker is unavailable in this environment,
say so and rely on the lead's Task 7 run to catch it.)

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/README.md docker-compose.yml
git commit -m "docs(pipeline): DB_POOL_MIN/MAX env rows, /health db_pool block, pooling section (M3-B)"
```

---

### Task 7: Measure (lead-only, Docker), record, merge

**Files:**
- Modify: `TODO.md:101-107` (the M3-B checkbox + its slice text), `TODO.md:13` (the M3 queue-table row)
- Modify: `docs/FEATURES.md` (one line under the pipeline entry)
- Modify: `services/pipeline/README.md` (the M3-B section: the measured numbers)

**Interfaces:** none — this task produces evidence and records it.

**This task is lead/human only.** It runs Docker, the singleton stack on :5433
and the load harness. A teammate agent must stop after Task 6 and report.

- [ ] **Step 1: Full gates on the branch**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher/.claude/worktrees/m3-b-pool
npm run verify
cd services/pipeline && uv run pytest -q && uv run ruff check .
```
Expected: all green. `npm run verify` touches no pipeline code but is the CI
gate and must pass before merge.

- [ ] **Step 2: Bring the stack up on the pre-pool build and take the baseline**

The baseline branch is `ai/main` **with M3-A already merged** — that is the
point: this measurement must isolate M3-B's connection churn, not re-measure
M3-A's 7–10× pgstac win on top of it. Both runs therefore have `use_queue` on
and the drainer ticking; only the pooling differs.

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher
git checkout ai/main                      # M3-A merged, M3-B not yet
docker compose up -d --build --wait
set -a; . .env; set +a                    # CREDENTIALS_MASTER_KEY for the harness
cd services/pipeline
uv run python -m pipeline.loadgen --label m3b setup --mode copy --metadata defaults_only
```

Read the cumulative session counter **before** the feed:

```bash
docker compose -f ../../docker-compose.yml exec -T database \
  psql -U username -d postgis -At -c \
  "SELECT sessions, sessions_abandoned FROM pg_stat_database WHERE datname = current_database()"
```
Record both numbers as `before_baseline`.

```bash
uv run python -m pipeline.loadgen --label m3b feed --rate 30 --count 900
sleep 60      # DISCOVER polls every 60 s
uv run python -m pipeline.loadgen --label m3b watch --seconds 120 --interval 20
```
Then read the counter again → `after_baseline`. Compute
`(after − before) / items_catalogued`. Expected: **≈14 new sessions per item**
(scoping notes M3-S-D). Record the `watch` throughput line too — including
M3-A's `query_queue` depth column, which must be draining rather than growing
in **both** runs; a stalled drainer would make the two runs incomparable.

```bash
uv run python -m pipeline.loadgen --label m3b teardown
```

- [ ] **Step 3: Repeat on the pooled build**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher
git checkout ai/m3-b-pool
docker compose up -d --build --wait
cd services/pipeline
uv run python -m pipeline.loadgen --label m3b setup --mode copy --metadata defaults_only
# read pg_stat_database → before_pooled
uv run python -m pipeline.loadgen --label m3b feed --rate 30 --count 900
sleep 60
uv run python -m pipeline.loadgen --label m3b watch --seconds 120 --interval 20
# read pg_stat_database → after_pooled
curl -s localhost:8083/health | python3 -m json.tool | sed -n '/db_pool/,$p'
uv run python -m pipeline.loadgen --label m3b teardown
```

Expected:
- `(after_pooled − before_pooled) / items` ≈ **0** new sessions per item (a
  handful total: `DB_POOL_MIN` warm connections plus whatever the pool grew to,
  once, not per item).
- `/health`'s `db_pool` shows a non-zero `pool_size` and `requests_waiting: 0`.
  A persistently non-zero `requests_waiting` at concurrency 1 would mean the
  pool is being held longer than a statement — investigate before merging.
- Throughput is unchanged or slightly better. M3-B is a *precondition* for
  M3-D, not a throughput slice: at concurrency 1 the pgstac call still
  dominates, so a flat items/s here is the expected result, not a failure.

- [ ] **Step 4: Record the numbers**

In `services/pipeline/README.md`, at the end of the "Connection pooling (M3-B)"
section added in Task 6, append the measured line (substitute the real
figures — do not leave the bracketed text):

```markdown
Measured on the compose stack, `loadgen --label m3b`, 900 items at 30/s,
`--mode copy --metadata defaults_only`, `pg_stat_database.sessions` delta per
catalogued item: **<BEFORE> → <AFTER> new backend sessions per item**
(<DATE>, laptop numbers — they rank the fix, they are not platform capacity).
```

In `TODO.md`, tick the M3-B checkbox (line 101, `- [ ]` → `- [x]`) and append
one sentence to the slice text after "…where M3-A's two GUCs belong.":

```
      **Merged <DATE>:** `src/pipeline/db/pool.py`; twelve repos + two one-offs
      pooled; `/health` gained a `db_pool` block; `DB_POOL_MIN`/`DB_POOL_MAX`
      (2/16, size as `WORKER_CONCURRENCY + 4`). Measured
      `pg_stat_database.sessions` delta: <BEFORE> → <AFTER> per item.
```

Update the M3 row of the queue table (`TODO.md:13`) so its status reads
`M3-A + M3-B merged <DATE>; M3-C next`.

In `docs/FEATURES.md`, find the pipeline/M3 entry and append one sentence:
"Repo database access runs on a process-wide `psycopg_pool.AsyncConnectionPool`
(M3-B, <DATE>) whose `configure` hook carries the pgstac session GUCs; pool
stats are on `GET :8083/health`."

Append any follow-up discovered during the run to `TODO.md`'s follow-ups block
and, if it is a limitation rather than a task, to `docs/ISSUES.md`.

```bash
git add TODO.md docs/FEATURES.md services/pipeline/README.md
git commit -m "docs: M3-B done — connection pool, measured session-delta evidence"
```

- [ ] **Step 5: Merge**

```bash
cd /Users/caesterlein/Projects/TechTraverse/stac-higher
git checkout ai/main
git merge ai/m3-b-pool --no-ff -m "Merge ai/m3-b-pool: M3-B connection pool across the pipeline repos"
npm run verify
cd services/pipeline && uv run pytest -q && uv run ruff check .
cd /Users/caesterlein/Projects/TechTraverse/stac-higher
git worktree remove .claude/worktrees/m3-b-pool
git branch -d ai/m3-b-pool
```

No e2e run: this slice touches no UI flow. **Do not push `ai/main`** — it stays
local by the lead's standing instruction; the human promotes it via PR.

---

## Self-review

**Spec coverage.**

- Spec §3, M3-B row ("Connection pool across the pipeline repos", "a
  *precondition* for M3-C, not an optimisation") → Tasks 2–4; the precondition
  framing is stated in the Goal, in the `pool.py` docstring and again in
  Task 7 Step 3's "flat items/s here is the expected result". ✓
- Spec §3 dependency spine (`M3-B → M3-C → M3-D`; `M3-A first`) → Task 0 hard-
  gates on M3-A's module; `DB_POOL_MAX`'s default and its documented formula
  are sized against M3-D's concurrency of 12. ✓
- Spec §4.2 "the two `SET`s belong in the pool's `on_connect` hook" →
  `configure=configure_pgstac_session_async` in Task 2, asserted in
  `test_pool_configures_the_pgstac_session_guc_hook` (unit) and
  `test_pooled_connection_carries_the_pgstac_session_gucs` (live). ✓
- Spec §4.2's other half — the sync `writer_pool()` stays M3-A's — is stated as
  a Global Constraint and repeated in the `get_collection_bbox` comment, so an
  executor reading Task 3 alone does not "unify" the two pools. ✓
- Spec §5 "observability is the mitigation, not optional garnish" → the
  `/health` `db_pool` block (Task 4) and the README's `requests_waiting`
  guidance (Task 6). The spec's Prometheus instruments are M3-A's `query_queue`
  gauge, not this slice's — no Gauge added here, per the settled YAGNI call. ✓
- Spec §7 decision 2 (concurrency default 12) → the sizing formula, not a
  concurrency change: M3-D owns the raise. ✓
- Scoping notes M3-S-D: the 4.19 ms / ~14-per-item / ~420-per-second figures are
  quoted in the Goal, the module docstring and the README section; the
  write-amplification table is what Task 7's "≈14 → ≈0 sessions per item"
  delta measures. ✓
- `TODO.md:101-107` — every clause of the slice text has a task: the fresh
  `AsyncConnection` per method (Task 3), the GUC home (Task 2), the M3-D
  precondition framing (constraints + sizing), the measurement (Task 7). ✓

**M3-A alignment** (checked against `docs/superpowers/plans/2026-09-07-m3-a-pgstac-write-path.md`).

- `pyproject.toml` — M3-A Task 1 Step 1 already adds `psycopg[binary]>=3.2,<4`
  and `psycopg-pool>=3.2,<4`. This plan's Task 1 Step 4 is now *verify present,
  add only if missing, do not re-pin*, and Task 0 Step 2 greps for them. ✓
- `db/__init__.py` + `db/pgstac_session.py` — created by M3-A Task 1; Task 2
  Step 3 confirms rather than writes. The hooks' **COMMIT after the two `SET`s**
  is why this plan can leave `reset` at its default, and that dependency is now
  written into the design notes and re-proved live in Task 5. ✓
- `writer_pool()` / `close_writer_pools()` / `WRITER_POOL_MAX = 4` — M3-A
  Task 2. Task 4's `finally` puts `await close_pools()` **beside**
  `close_writer_pools()` (not instead of it), the ordering is asserted, and the
  "don't merge the two pools — pypgstac checks out `autocommit=True`" rule is a
  Global Constraint. ✓
- `stac/query_queue.py` `PgPgstacQueueRepo` (M3-A Task 4) — a thirteenth
  `_connect` helper that **stays direct and autocommit**, because `CALL
  pgstac.run_queued_queries()` COMMITs inside itself. It is exemption 4 in the
  Global Constraints, excluded from Task 3's file list, in the adoption test's
  `ALLOWED`, pinned by its own test (`test_the_exempt_drainer_is_still_autocommit`),
  and named in the README section. Its job is `jobs/pgstac_drain.py`
  (`pipeline.pgstac_queue_drain`). ✓
- `metrics.py` `Gauge` (M3-A Task 4) — noted as already imported, and
  explicitly not used here: `/health` only. ✓
- Line numbers — M3-A inserts into `config.py`, `main.py`, `metrics.py` and the
  README. Every reference in this plan now names its anchor symbol/text as well
  as the pre-M3-A number, and each of this plan's three `config.py` insertion
  points is at the *end* of its region, where M3-A does not write. ✓
- Task 7's baseline branch is `ai/main` **with M3-A merged**, so the
  measurement isolates connection churn instead of re-measuring M3-A. ✓

**Placeholder scan.** Every code step carries real, runnable code. The only
bracketed text in the plan is in Task 7 Step 4 (`<BEFORE>`, `<AFTER>`,
`<DATE>`), which are *measurements that do not exist until the run happens* and
are explicitly instructed to be substituted — plus the commit-trailer
`<MODEL>` / session URL, which belong to the executing session by repo policy.
No "TBD", no "add error handling", no "similar to Task N", no test described
rather than written.

**Type consistency.** `get_async_pool(database_url: str) -> AsyncConnectionPool`
in Task 2 is what Task 3's twelve helpers and two one-offs call, and what
Task 5's integration test imports. `close_pools() -> None` is async and awaited
by `main.run()` (Task 4) and by Task 5's fixture; M3-A's `close_writer_pools()`
is **sync** and is called, not awaited, in the same `finally` — the two are
distinct names with distinct call shapes and the plan says so at both sites. `pool_stats() -> dict[str,
dict[str, int]]` keyed by `pool_name()`'s `host:port/dbname` is what Task 4's
health test and Task 5's `pool_stats()[pool.name]` assert against — the key is
`pool.name` in both, because Task 2 passes `name=pool_name(database_url)` to
the pool constructor, so `pool.name` and `pool_name(dsn)` are the same string
by construction. `Settings.db_pool_min` / `db_pool_max` (Task 1) are the exact
attribute names Task 2 reads. The repo helper keeps its name `_connect` and its
`await`-then-`async with` contract, which is what makes "no call site changes"
true rather than aspirational.

**One ambiguity resolved, flagged for the reviewer.** The brief specified
`pool_stats() -> dict[str, dict]` from a DSN-keyed registry. Keying the
`/health` payload by the raw DSN would publish the database password on an
unauthenticated endpoint, so the registry stays DSN-keyed while the *stats* are
keyed by `pool_name(dsn)` — `host:port/dbname`, no user, no password — which is
also the pool's `name`, so it shows up identically in the `db pool opened` log
line. Two unit tests lock the redaction.
