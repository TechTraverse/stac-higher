# M3-A · pgstac Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The pipeline's pgstac writer defers partition statistics through `pgstac.query_queue` (session GUCs `use_queue` + `update_collection_extent`, set on the writer's own connections), a periodic pipeline job drains that queue where no database-side drainer exists, and the queue's depth and age are visible on `/metrics` — taking the measured write ceiling from 2–3.5 to ~22 items/s on the real pipeline.

**Architecture:** Two pgstac session GUCs are issued by a `configure` hook on a small synchronous `psycopg_pool.ConnectionPool` that `pypgstac`'s `PgstacDB` checks its connections out of (the hook is shared code that M3-B's async pool reuses). A new periodic job `pipeline.pgstac_queue_drain` samples `pgstac.query_queue` every minute, `CALL`s `pgstac.run_queued_queries()` on an autocommit connection when this deployment owns the drain (`PGSTAC_QUEUE_DRAINER=pipeline`, the default; `database` means pg_cron owns it and the job only samples), prunes `query_queue_history`, and publishes two gauges + one counter. Nothing in deployment configuration changes for the setting to take effect; the loadgen harness measures the result.

**Tech Stack:** Python 3.12, psycopg 3.3 + psycopg_pool 3.3, pypgstac 0.9.11 (`PgstacDB(pool=…, use_queue=True)`), Procrastinate periodic tasks, prometheus-client, pytest (DB-gated tests skip without `DATABASE_URL`), ruff.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §4 (all of it), §5 (M3-A risk: the drainer that silently stops, the deployment that never sets `use_queue`), §7 decisions 1 and 3. Evidence: `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-A §1. Slice text: `TODO.md` M3-A.

## Global Constraints

- Worktree: `git worktree add .claude/worktrees/m3-a-pgstac-queue -b ai/m3-a-pgstac-queue ai/main`. Pipeline-only slice: gates are `cd services/pipeline && uv run pytest && uv run ruff check .` after every task, and `npm run verify` from the repo root before merge (the CI gate; it does not touch the pipeline but must stay green).
- **The two settings are SESSION GUCs set by the writer's connections** (spec §7.1, §7.3). Nothing writes `pgstac.pgstac_settings`; nothing in `docker-compose.yml` or the app sets them. Do not relitigate.
- `pypgstac.db.PgstacDB` issues its own `SET pgstac.use_queue TO TRUE` **only** when it checks a connection out of a pool it was given (`connect()` skips the `SET` for a handed-in `connection`). The writer therefore hands it a **pool**, never a connection, and the pool's `configure` hook sets both GUCs regardless.
- **`SET` is transactional in Postgres.** A `configure` callback runs inside the pool's fresh (non-autocommit) connection; it MUST `commit()` or the pool's reset rolls the `SET`s back. Both hooks in Task 1 commit. The DB-gated test in Task 7 proves the GUCs survive checkout.
- **`CALL pgstac.run_queued_queries()` contains `COMMIT`** — it must run on an **autocommit** connection (`psycopg.AsyncConnection.connect(url, autocommit=True)`); inside a transaction block Postgres raises `invalid transaction termination`. It is a PROCEDURE: `CALL`, never `SELECT`.
- pgstac's `queue_timeout` setting (default `10 minutes`) bounds one `CALL`; Procrastinate's `queueing_lock` (already applied by `register_periodic`) skips a tick whose predecessor is still running, so a long drain never stacks.
- Schema ownership (ADR 0001): the pipeline never runs DDL. `query_queue` / `query_queue_history` are pgstac's own tables; the job reads and deletes ROWS from them only.
- Pipeline logging: data in `extra={...}`, never interpolated into the message. `filename` is a reserved LogRecord key — never use it in `extra`.
- No new cross-runtime contract shapes (spec §5) — no fixture changes.
- The pool used by the writer is used by the writer ONLY. `pypgstac` sets `autocommit = True` on every connection it checks out and `psycopg_pool` does not reset that on return, so a repo statement on one of these connections would silently run outside a transaction. M3-B's async repo pool is a separate object.
- Commit messages end with the executing session's attribution trailer:
  ```
  Co-Authored-By: Claude <model name> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 1: The pgstac session hook (shared by both pools)

**Files:**
- Create: `services/pipeline/src/pipeline/db/__init__.py`
- Create: `services/pipeline/src/pipeline/db/pgstac_session.py`
- Modify: `services/pipeline/pyproject.toml:8-24` (dependencies)
- Test: `services/pipeline/tests/test_pgstac_session.py` (new)

**Interfaces:**
- Produces:
  ```python
  PGSTAC_SESSION_SQL: tuple[str, ...]  # ("SET pgstac.use_queue TO TRUE", "SET pgstac.update_collection_extent TO TRUE")
  def configure_pgstac_session(conn: psycopg.Connection) -> None        # executes both, then conn.commit()
  async def configure_pgstac_session_async(conn: psycopg.AsyncConnection) -> None  # same, awaited
  ```
  Task 2 passes the sync hook to the writer's pool; M3-B passes the async hook to the repo pool.

- [ ] **Step 1: Make `psycopg_pool` an explicit dependency**

It is already installed transitively through `pypgstac[psycopg]`, but the pipeline now imports it directly. In `services/pipeline/pyproject.toml` add two lines to `dependencies` after the `pypgstac` pin (the lock already resolves these exact packages, so `uv lock` changes only the direct-dependency markers):

```toml
    "pypgstac[psycopg]==0.9.11",
    "psycopg[binary]>=3.2,<4",
    "psycopg-pool>=3.2,<4",
```

Run: `cd services/pipeline && uv lock && uv sync --extra dev`
Expected: lock updated without version changes (`psycopg 3.3.4`, `psycopg-pool 3.3.1`).

- [ ] **Step 2: Write the failing test**

`services/pipeline/tests/test_pgstac_session.py`:

```python
"""The two pgstac session GUCs the writer's connections carry (M3-A, spec §4.2).

Unit-level: the hook issues exactly the two SETs and COMMITs — a SET inside a
transaction the pool later rolls back would silently disappear. The DB-gated
proof that the GUCs survive a pool checkout is in
tests/test_integration_pgstac_queue.py.
"""

from __future__ import annotations

from pipeline.db.pgstac_session import (
    PGSTAC_SESSION_SQL,
    configure_pgstac_session,
    configure_pgstac_session_async,
)


class _SyncConn:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def commit(self):
        self.commits += 1


class _AsyncConn:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    async def execute(self, sql, params=None):
        self.executed.append(sql)

    async def commit(self):
        self.commits += 1


def test_session_sql_names_both_settings():
    assert PGSTAC_SESSION_SQL == (
        "SET pgstac.use_queue TO TRUE",
        "SET pgstac.update_collection_extent TO TRUE",
    )


def test_sync_hook_sets_both_and_commits():
    conn = _SyncConn()
    configure_pgstac_session(conn)
    assert conn.executed == list(PGSTAC_SESSION_SQL)
    # SET is transactional: without the commit the pool's reset would undo it.
    assert conn.commits == 1


async def test_async_hook_sets_both_and_commits():
    conn = _AsyncConn()
    await configure_pgstac_session_async(conn)
    assert conn.executed == list(PGSTAC_SESSION_SQL)
    assert conn.commits == 1
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.db'`.

- [ ] **Step 4: Implement the hook**

`services/pipeline/src/pipeline/db/__init__.py`:

```python
"""Database plumbing shared across the pipeline's repos and the pgstac writer.

M3-A adds the pgstac session hook; M3-B adds the async connection pool.
"""
```

`services/pipeline/src/pipeline/db/pgstac_session.py`:

```python
"""pgstac session settings the pipeline's writers carry (M3-A, spec §4.2).

`pgstac.get_setting` resolves a setting as conf → session GUC → the
`pgstac_settings` table, so a `SET` on the writer's own connection overrides
the table for that session and nothing else: the app's BFF writes, `psql`,
search and e2e all keep pgstac's inline statistics. This is the library's own
seam for bulk loaders — pypgstac issues the first of these SETs itself when
`PgstacDB(use_queue=True)` checks a connection out of a pool — and it ships
with the release rather than as a deployment step that can be forgotten.

Both hooks COMMIT: `SET` is transactional, and a pool's reset would roll back
an uncommitted one on the connection's first return.
"""

from __future__ import annotations

from typing import Protocol

#: `use_queue` routes `update_partition_stats` (the O(partition) scan +
#: ANALYZE on every item write) into `pgstac.query_queue`;
#: `update_collection_extent` makes the queued statement also refresh the
#: collection's advertised extent (spec §4.4) — cheap only once stats are
#: queued, which is why the two travel together.
PGSTAC_SESSION_SQL: tuple[str, ...] = (
    "SET pgstac.use_queue TO TRUE",
    "SET pgstac.update_collection_extent TO TRUE",
)


class _SyncConnection(Protocol):
    def execute(self, query: str, params: object = None) -> object: ...
    def commit(self) -> None: ...


class _AsyncConnection(Protocol):
    async def execute(self, query: str, params: object = None) -> object: ...
    async def commit(self) -> None: ...


def configure_pgstac_session(conn: _SyncConnection) -> None:
    """`psycopg_pool.ConnectionPool(configure=...)` hook — runs once per new connection."""
    for statement in PGSTAC_SESSION_SQL:
        conn.execute(statement)
    conn.commit()


async def configure_pgstac_session_async(conn: _AsyncConnection) -> None:
    """`psycopg_pool.AsyncConnectionPool(configure=...)` hook — the async twin."""
    for statement in PGSTAC_SESSION_SQL:
        await conn.execute(statement)
    await conn.commit()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_session.py -v && uv run ruff check .`
Expected: 3 passed; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/pyproject.toml services/pipeline/uv.lock services/pipeline/src/pipeline/db services/pipeline/tests/test_pgstac_session.py
git commit -m "feat(pipeline): pgstac session hook — use_queue + update_collection_extent as session GUCs (M3-A)"
```

---

### Task 2: The writer checks connections out of a configured pool

**Files:**
- Modify: `services/pipeline/src/pipeline/stac/pgstac_writer.py:41-60`
- Test: `services/pipeline/tests/test_pgstac_writer.py` (extend)

**Interfaces:**
- Consumes: `configure_pgstac_session` (Task 1).
- Produces:
  ```python
  WRITER_POOL_MAX: int = 4
  def writer_pool(dsn: str) -> psycopg_pool.ConnectionPool   # process-wide, one per DSN, opened on first use
  def close_writer_pools() -> None                           # tests + shutdown
  PgPgstacWriter._open_pgstac(self) -> pypgstac.db.PgstacDB  # the seam tests patch
  ```

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_pgstac_writer.py`:

```python
import pypgstac.db
import pypgstac.load

from pipeline.stac import pgstac_writer as writer_mod


class _FakePool:
    closed = False

    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.closed = True


def test_writer_pool_is_shared_per_dsn_and_configured(monkeypatch):
    created: list[dict] = []

    def _fake_connection_pool(conninfo, **kwargs):
        created.append({"conninfo": conninfo, **kwargs})
        return _FakePool()

    monkeypatch.setattr(writer_mod, "ConnectionPool", _fake_connection_pool)
    writer_mod.close_writer_pools()

    a = writer_mod.writer_pool("postgresql://one")
    b = writer_mod.writer_pool("postgresql://one")
    c = writer_mod.writer_pool("postgresql://two")

    assert a is b and a is not c
    assert len(created) == 2
    # The hook that sets both GUCs is the pool's configure callback (spec §4.2);
    # a bounded pool caps concurrent upserts once M3-D raises concurrency.
    assert created[0]["configure"] is writer_mod.configure_pgstac_session
    assert created[0]["max_size"] == writer_mod.WRITER_POOL_MAX
    assert created[0]["open"] is True

    writer_mod.close_writer_pools()
    assert a.close_calls == 1 and c.close_calls == 1
    # A closed pool is replaced, not reused.
    d = writer_mod.writer_pool("postgresql://one")
    assert d is not a
    writer_mod.close_writer_pools()


def test_upsert_opens_pgstac_with_the_pool_and_use_queue(monkeypatch):
    sentinel_pool = _FakePool()
    monkeypatch.setattr(writer_mod, "writer_pool", lambda dsn: sentinel_pool)

    opened: list[dict] = []
    loaded: list[tuple] = []

    class _FakeDB:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeLoader:
        def __init__(self, db):
            self.db = db

        def load_items(self, items, insert_mode):
            loaded.append((list(items), insert_mode))

    monkeypatch.setattr(pypgstac.db, "PgstacDB", _FakeDB)
    monkeypatch.setattr(pypgstac.load, "Loader", _FakeLoader)

    writer = PgPgstacWriter(dsn="postgresql://ignored")
    writer._upsert_sync([{"id": "x", "collection": "c"}])

    # pypgstac only issues its own SET when it checks a connection out of a
    # POOL — a handed-in `connection` skips it — so the pool is what it gets.
    assert opened == [{"pool": sentinel_pool, "use_queue": True}]
    assert loaded == [([{"id": "x", "collection": "c"}], pypgstac.load.Methods.upsert)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_writer.py -v`
Expected: the two new tests FAIL (`AttributeError: module 'pipeline.stac.pgstac_writer' has no attribute 'ConnectionPool'` / `writer_pool`); the three existing tests still pass.

- [ ] **Step 3: Implement the pool and the seam**

Replace lines 41–60 of `services/pipeline/src/pipeline/stac/pgstac_writer.py` (the `PgPgstacWriter` dataclass header through `_upsert_sync`) with:

```python
#: How many upserts may run at once against pgstac. Upserts run in
#: `asyncio.to_thread`, so this — not the worker's concurrency — is the cap.
#: Measured cost is ~4 ms per single-item upsert with `use_queue` on (S-A §1),
#: so four is ample headroom for M3-D's concurrency of 12; raise it with
#: evidence, not by default.
WRITER_POOL_MAX = 4

_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


def writer_pool(dsn: str) -> ConnectionPool:
    """The process-wide pool the pgstac writer draws on, one per DSN.

    Every connection it opens carries the two pgstac session GUCs (M3-A, spec
    §4.2) through the `configure` hook. Opened on first use — construction of a
    `PgPgstacWriter` stays connection-free, as it always has been.

    Writer-only: pypgstac sets `autocommit = True` on every connection it
    checks out and the pool does not reset that, so nothing else may borrow
    from this pool.
    """
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=WRITER_POOL_MAX,
                configure=configure_pgstac_session,
                open=True,
                name="pgstac-writer",
            )
            _POOLS[dsn] = pool
        return pool


def close_writer_pools() -> None:
    """Close every writer pool (service shutdown; test isolation)."""
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


@dataclass
class PgPgstacWriter(PgstacWriter):
    dsn: str

    async def upsert_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        try:
            await asyncio.to_thread(self._upsert_sync, list(items))
        except CollectionMissing:
            raise
        except Exception as exc:
            if "is not present in the database" in str(exc):
                raise CollectionMissing(str(exc)) from exc
            raise

    def _upsert_sync(self, items: list[Mapping[str, Any]]) -> None:
        from pypgstac.load import Loader, Methods

        with self._open_pgstac() as db:
            Loader(db=db).load_items(items, insert_mode=Methods.upsert)

    def _open_pgstac(self):
        """A `PgstacDB` over the writer pool.

        `use_queue=True` is belt and braces: the pool's `configure` hook has
        already set both GUCs on the connection pypgstac is about to check out.
        pypgstac returns the connection to the pool on `__exit__`.
        """
        from pypgstac.db import PgstacDB

        return PgstacDB(pool=writer_pool(self.dsn), use_queue=True)
```

Add to the imports at the top of the file (after `from typing import Any`):

```python
import threading

from psycopg_pool import ConnectionPool

from pipeline.db.pgstac_session import configure_pgstac_session
```

Update the module docstring's first paragraph to say where the settings live:

```python
"""pgstac data-access seam (ROADMAP §6.1 ITEMIZE).

`PgstacWriter` is the ABC ITEMIZE depends on (so it unit-tests against a fake).
`PgPgstacWriter` implements both operations ITEMIZE needs from pgstac: the item
upsert, and the collection-extent read backing the ISSUE I-27 geometry
fallback. Upsert wraps pypgstac's synchronous `Loader.load_items(...,
Methods.upsert)` in `asyncio.to_thread`, over a small process-wide pool whose
connections carry `pgstac.use_queue` + `pgstac.update_collection_extent` as
SESSION GUCs (M3-A, spec §4.2) — partition statistics are queued rather than
recomputed inline on every write; `pipeline.pgstac_queue_drain` runs the queue.
ADR 0001: upsert writes item DATA only (temp `ON COMMIT DROP` staging tables +
pgstac's own `upsert_item` functions — no DDL, no migrations). A missing
collection is a permanent error surfaced as `CollectionMissing` (→ group
failed); anything else propagates so the job retries.
"""
```

`get_collection_bbox` (the raw psycopg read) is unchanged — M3-B pools it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_writer.py -v && uv run ruff check .`
Expected: 5 passed; ruff clean (check the import order it wants — `threading` with stdlib, `psycopg_pool` third-party, `pipeline.db` first-party).

- [ ] **Step 5: Close the pools at service shutdown**

In `services/pipeline/src/pipeline/main.py`, the `run()` coroutine's `finally` (line 102–103) becomes:

```python
    finally:
        close_writer_pools()
        await queue.aclose()
```

with the import added beside the others:

```python
from pipeline.stac.pgstac_writer import close_writer_pools
```

Run: `cd services/pipeline && uv run pytest tests/test_main_jobs.py -v`
Expected: PASS (constructing the queue still opens no connections).

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/stac/pgstac_writer.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_pgstac_writer.py
git commit -m "feat(pipeline): pgstac writer draws on a configured pool — use_queue on by construction (M3-A)"
```

---

### Task 3: Settings for the drainer

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py` (docstring lines 1–30; constants after line 85; `Settings` fields after `gc_batch_items` ~line 205; `from_env` after `gc_batch_items=` ~line 302)
- Test: `services/pipeline/tests/test_config.py` (extend)

**Interfaces:**
- Produces on `Settings`:
  ```python
  pgstac_queue_drainer: str            # "pipeline" (default) | "database"
  pgstac_queue_stale_seconds: int      # default 300
  pgstac_queue_history_days: int       # default 7
  ```
  env keys `PGSTAC_QUEUE_DRAINER`, `PGSTAC_QUEUE_STALE_SECONDS`, `PGSTAC_QUEUE_HISTORY_DAYS`; constants `DEFAULT_PGSTAC_QUEUE_DRAINER`, `DEFAULT_PGSTAC_QUEUE_STALE_SECONDS`, `DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS`, `PGSTAC_QUEUE_DRAINERS = ("pipeline", "database")`.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_config.py`:

```python
import pytest

from pipeline.config import (
    DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS,
    DEFAULT_PGSTAC_QUEUE_STALE_SECONDS,
)


def test_pgstac_queue_defaults():
    settings = Settings.from_env(env={})
    # Local/self-hosted default: the pipeline drains (pg_cron is not in the
    # pgstac image — spec §4.3). Cloud sets "database" once pg_cron owns it.
    assert settings.pgstac_queue_drainer == "pipeline"
    assert settings.pgstac_queue_stale_seconds == DEFAULT_PGSTAC_QUEUE_STALE_SECONDS == 300
    assert settings.pgstac_queue_history_days == DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS == 7


def test_pgstac_queue_env_overrides():
    settings = Settings.from_env(
        env={
            "PGSTAC_QUEUE_DRAINER": " Database ",
            "PGSTAC_QUEUE_STALE_SECONDS": "120",
            "PGSTAC_QUEUE_HISTORY_DAYS": "30",
        }
    )
    assert settings.pgstac_queue_drainer == "database"
    assert settings.pgstac_queue_stale_seconds == 120
    assert settings.pgstac_queue_history_days == 30


def test_pgstac_queue_drainer_rejects_unknown_value():
    with pytest.raises(ValueError, match="PGSTAC_QUEUE_DRAINER"):
        Settings.from_env(env={"PGSTAC_QUEUE_DRAINER": "cron"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_config.py -v`
Expected: the three new tests FAIL (`ImportError` on the constants).

- [ ] **Step 3: Add the settings**

In `services/pipeline/src/pipeline/config.py`:

(a) Docstring — append to the env contract list at the top (after the `ASSET_HREF_BASE` bullet):

```python
- ``PGSTAC_QUEUE_DRAINER`` — who runs ``pgstac.query_queue`` (M3-A): ``pipeline``
  (default; the ``pipeline.pgstac_queue_drain`` tick CALLs
  ``pgstac.run_queued_queries()`` every minute) or ``database`` (pg_cron owns
  the drain; the tick only samples depth/age so the two never fight).
- ``PGSTAC_QUEUE_STALE_SECONDS`` — the queue's oldest entry older than this
  logs a WARNING (the staleness bound on partition statistics, spec §4.6).
- ``PGSTAC_QUEUE_HISTORY_DAYS`` — ``pgstac.query_queue_history`` rows older
  than this are pruned by the same tick (pgstac never prunes it).
```

(b) Constants — after `DEFAULT_GC_BATCH_ITEMS = 500` (line 85):

```python
# pgstac query queue (M3-A, spec §4.3/§4.6). `use_queue` is a SESSION GUC on
# the writer's connections (pipeline/db/pgstac_session.py); the queue itself
# is global and something must drain it. pg_cron is not in the pgstac image,
# so locally the pipeline drains; a cloud deployment with pg_cron sets
# `database` and the tick becomes a sampler.
PGSTAC_QUEUE_DRAINERS = ("pipeline", "database")
DEFAULT_PGSTAC_QUEUE_DRAINER = "pipeline"
#: Drain cadence is one minute; twice that plus slack is "the drainer stopped".
DEFAULT_PGSTAC_QUEUE_STALE_SECONDS = 300
DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS = 7
```

(c) Parser — after `_parse_allow_hosts` (line ~166):

```python
def _parse_pgstac_queue_drainer(raw: str | None) -> str:
    value = (raw or DEFAULT_PGSTAC_QUEUE_DRAINER).strip().lower()
    if value not in PGSTAC_QUEUE_DRAINERS:
        raise ValueError(
            f"PGSTAC_QUEUE_DRAINER must be one of {PGSTAC_QUEUE_DRAINERS}, got {raw!r}"
        )
    return value
```

(d) Fields — after `gc_batch_items: int = DEFAULT_GC_BATCH_ITEMS`:

```python
    #: pgstac query-queue drain (M3-A) — see the DEFAULT_PGSTAC_QUEUE_* constants.
    pgstac_queue_drainer: str = DEFAULT_PGSTAC_QUEUE_DRAINER
    pgstac_queue_stale_seconds: int = DEFAULT_PGSTAC_QUEUE_STALE_SECONDS
    pgstac_queue_history_days: int = DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS
```

(e) `from_env` — after the `gc_batch_items=int(...)` line:

```python
            pgstac_queue_drainer=_parse_pgstac_queue_drainer(env.get("PGSTAC_QUEUE_DRAINER")),
            pgstac_queue_stale_seconds=int(
                env.get("PGSTAC_QUEUE_STALE_SECONDS", str(DEFAULT_PGSTAC_QUEUE_STALE_SECONDS))
            ),
            pgstac_queue_history_days=int(
                env.get("PGSTAC_QUEUE_HISTORY_DAYS", str(DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS))
            ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_config.py -v && uv run ruff check .`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/tests/test_config.py
git commit -m "feat(pipeline): PGSTAC_QUEUE_DRAINER / _STALE_SECONDS / _HISTORY_DAYS settings (M3-A)"
```

---

### Task 4: Queue repo seam + the drain tick (pure logic)

**Files:**
- Create: `services/pipeline/src/pipeline/stac/query_queue.py`
- Modify: `services/pipeline/src/pipeline/metrics.py:31-36` (imports), `:38-54` (`__all__`), after line 161 (new metrics)
- Test: `services/pipeline/tests/test_pgstac_queue_drain.py` (new)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True) class QueueSample: depth: int; oldest_age_seconds: float | None
  @dataclass(frozen=True) class DrainOutcome: executed: int; errors: int
  @dataclass(frozen=True) class TickResult: mode: str; before: QueueSample; after: QueueSample | None; drained: DrainOutcome | None; pruned: int; stale: bool
  class PgstacQueueRepo(abc.ABC): sample() -> QueueSample; drain() -> DrainOutcome; prune_history(older_than_days: int) -> int
  async def drain_tick(repo, *, mode: str, stale_after_seconds: int, history_days: int) -> TickResult
  ```
  Metrics (module `pipeline.metrics`): `PGSTAC_QUEUE_DEPTH` (Gauge `pipeline_pgstac_query_queue_depth`), `PGSTAC_QUEUE_OLDEST_SECONDS` (Gauge `pipeline_pgstac_query_queue_oldest_seconds`), `PGSTAC_QUEUE_QUERIES` (Counter `pipeline_pgstac_queue_queries_total{outcome}` — `ok` | `error`).

- [ ] **Step 1: Write the failing tests**

`services/pipeline/tests/test_pgstac_queue_drain.py`:

```python
"""The pgstac query-queue drain tick (M3-A, spec §4.3 / §4.6 / §5).

Against a fake repo: the tick drains only when this deployment owns the drain,
always samples (the metric is the one outside-the-process proof the session
GUCs are in effect), prunes history in both modes, and flags a stale queue —
a drainer that silently stopped is otherwise invisible.
"""

from __future__ import annotations

import logging

from pipeline import metrics
from pipeline.stac.query_queue import (
    DrainOutcome,
    PgstacQueueRepo,
    QueueSample,
    drain_tick,
)


class FakeQueueRepo(PgstacQueueRepo):
    def __init__(self, samples: list[QueueSample], drained: DrainOutcome | None = None):
        self.samples = list(samples)
        self.drained = drained or DrainOutcome(executed=0, errors=0)
        self.drain_calls = 0
        self.prune_calls: list[int] = []

    async def sample(self) -> QueueSample:
        return self.samples.pop(0)

    async def drain(self) -> DrainOutcome:
        self.drain_calls += 1
        return self.drained

    async def prune_history(self, older_than_days: int) -> int:
        self.prune_calls.append(older_than_days)
        return 3


def _gauge(g) -> float:
    return g._value.get()


def _counter(c, **labels) -> float:
    return c.labels(**labels)._value.get()


async def test_pipeline_mode_drains_samples_twice_and_reports():
    repo = FakeQueueRepo(
        samples=[QueueSample(depth=5, oldest_age_seconds=42.0), QueueSample(0, None)],
        drained=DrainOutcome(executed=5, errors=1),
    )
    ok_before = _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="ok")
    err_before = _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error")

    result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)

    assert repo.drain_calls == 1
    assert result.before == QueueSample(5, 42.0)
    assert result.after == QueueSample(0, None)
    assert result.drained == DrainOutcome(executed=5, errors=1)
    assert result.pruned == 3 and repo.prune_calls == [7]
    assert result.stale is False
    # Gauges reflect the AFTER sample — what is left for the next tick.
    assert _gauge(metrics.PGSTAC_QUEUE_DEPTH) == 0
    assert _gauge(metrics.PGSTAC_QUEUE_OLDEST_SECONDS) == 0
    assert _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="ok") == ok_before + 4
    assert _counter(metrics.PGSTAC_QUEUE_QUERIES, outcome="error") == err_before + 1


async def test_database_mode_never_drains_but_still_samples_and_prunes():
    repo = FakeQueueRepo(samples=[QueueSample(depth=2, oldest_age_seconds=10.0)])

    result = await drain_tick(repo, mode="database", stale_after_seconds=300, history_days=7)

    assert repo.drain_calls == 0
    assert result.after is None and result.drained is None
    assert result.pruned == 3
    assert _gauge(metrics.PGSTAC_QUEUE_DEPTH) == 2
    assert _gauge(metrics.PGSTAC_QUEUE_OLDEST_SECONDS) == 10.0


async def test_stale_queue_is_flagged_and_logged(caplog):
    # Pipeline mode, but the drain left the oldest entry behind (an error kept
    # it queued? no — errors are recorded and removed; this models a CALL that
    # hit queue_timeout with work left) → oldest survives past the bound.
    repo = FakeQueueRepo(
        samples=[QueueSample(depth=40, oldest_age_seconds=900.0), QueueSample(30, 700.0)],
        drained=DrainOutcome(executed=10, errors=0),
    )
    with caplog.at_level(logging.WARNING, logger="pipeline.stac.query_queue"):
        result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)

    assert result.stale is True
    record = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert record.oldest_age_seconds == 700.0
    assert record.stale_after_seconds == 300
    assert record.mode == "pipeline"


async def test_database_mode_stale_means_the_external_drainer_stopped(caplog):
    repo = FakeQueueRepo(samples=[QueueSample(depth=1, oldest_age_seconds=301.0)])
    with caplog.at_level(logging.WARNING, logger="pipeline.stac.query_queue"):
        result = await drain_tick(repo, mode="database", stale_after_seconds=300, history_days=7)
    assert result.stale is True
    assert any(r.mode == "database" for r in caplog.records if r.levelno == logging.WARNING)


async def test_empty_queue_is_not_stale():
    repo = FakeQueueRepo(samples=[QueueSample(0, None), QueueSample(0, None)])
    result = await drain_tick(repo, mode="pipeline", stale_after_seconds=300, history_days=7)
    assert result.stale is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_queue_drain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.stac.query_queue'`.

- [ ] **Step 3: Add the metrics**

In `services/pipeline/src/pipeline/metrics.py`:

Import `Gauge` (lines 31–36):

```python
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
```

Extend `__all__` (keep it sorted) with `"PGSTAC_QUEUE_DEPTH"`, `"PGSTAC_QUEUE_OLDEST_SECONDS"`, `"PGSTAC_QUEUE_QUERIES"`.

After the `ALERTS` counter (line 161) add:

```python
# --- M3-A pgstac query queue --------------------------------------------------
# `use_queue` is a SESSION GUC on the writer's connections, so nothing outside
# the process can see it is in effect — except the queue it feeds. Depth and
# age together are that evidence, and a drainer that silently stops shows up
# here as a rising age long before search planning degrades.
PGSTAC_QUEUE_DEPTH = Gauge(
    "pipeline_pgstac_query_queue_depth",
    "Statements waiting in pgstac.query_queue after the last drain tick",
    registry=REGISTRY,
)
PGSTAC_QUEUE_OLDEST_SECONDS = Gauge(
    "pipeline_pgstac_query_queue_oldest_seconds",
    "Age of the oldest statement in pgstac.query_queue after the last drain tick (0 when empty)",
    registry=REGISTRY,
)
PGSTAC_QUEUE_QUERIES = Counter(
    "pipeline_pgstac_queue_queries_total",
    "Queued pgstac statements executed by the pipeline's drain tick",
    ["outcome"],  # ok | error (pgstac records the error in query_queue_history)
    registry=REGISTRY,
)
```

- [ ] **Step 4: Implement the repo seam and the tick**

`services/pipeline/src/pipeline/stac/query_queue.py`:

```python
"""pgstac's query queue: the drain the pipeline owns locally (M3-A, spec §4.3).

With `pgstac.use_queue` on (a session GUC on the writer's connections —
pipeline/db/pgstac_session.py), every item write enqueues its partition's
`update_partition_stats` into `pgstac.query_queue` instead of running it
inline. The queue dedupes by query text, so its depth is bounded by the number
of distinct partitions written, not by write volume — and the drain cadence is
what bounds how stale a partition's statistics may get (spec §4.6).

pgstac's intended drainer is pg_cron calling `run_queued_queries()`. pg_cron is
not in the pgstac image, so locally (and on any Postgres without it) this
module's tick is the drainer; a deployment where the database drains sets
`PGSTAC_QUEUE_DRAINER=database` and the tick only samples, so the two cannot
fight. Either way the sample is the one outside-the-process proof the setting
is in effect, and a rising age is the signal that whichever drainer is
configured has stopped.

`run_queued_queries()` is a PROCEDURE that COMMITs per statement: it needs
`CALL` on an AUTOCOMMIT connection (`SELECT` errors; a transaction block errors
with "invalid transaction termination"). pgstac appends every executed
statement to `query_queue_history` and never prunes it; the tick does.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass

from pipeline import metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueSample:
    depth: int
    #: Seconds since the oldest queued statement was added; None when empty.
    oldest_age_seconds: float | None


@dataclass(frozen=True)
class DrainOutcome:
    #: Statements pgstac executed during this CALL (from query_queue_history).
    executed: int
    #: Of those, how many pgstac recorded an error for. The row is still
    #: removed from the queue — the next write to that partition re-queues it.
    errors: int


@dataclass(frozen=True)
class TickResult:
    mode: str
    before: QueueSample
    after: QueueSample | None
    drained: DrainOutcome | None
    pruned: int
    stale: bool


class PgstacQueueRepo(abc.ABC):
    """The three statements the tick needs; unit tests use a fake."""

    @abc.abstractmethod
    async def sample(self) -> QueueSample: ...

    @abc.abstractmethod
    async def drain(self) -> DrainOutcome: ...

    @abc.abstractmethod
    async def prune_history(self, older_than_days: int) -> int: ...


@dataclass
class PgPgstacQueueRepo(PgstacQueueRepo):
    """psycopg-backed repo. One short-lived AUTOCOMMIT connection per call —
    the CALL commits inside itself and cannot run in a transaction block.
    (M3-B: keep this off the transactional repo pool for that reason.)"""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url, autocommit=True)

    async def sample(self) -> QueueSample:  # pragma: no cover - DB integration suite
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT count(*), EXTRACT(EPOCH FROM (now() - min(added)))"
                " FROM pgstac.query_queue"
            )
            row = await cur.fetchone()
        depth = int(row[0]) if row else 0
        age = float(row[1]) if row and row[1] is not None else None
        return QueueSample(depth=depth, oldest_age_seconds=age)

    async def drain(self) -> DrainOutcome:  # pragma: no cover - DB integration suite
        async with await self._connect() as conn:
            cur = await conn.execute("SELECT clock_timestamp()")
            started = (await cur.fetchone())[0]
            # PROCEDURE with COMMIT inside: CALL, autocommit connection.
            await conn.execute("CALL pgstac.run_queued_queries()")
            cur = await conn.execute(
                "SELECT count(*), count(error) FROM pgstac.query_queue_history"
                " WHERE finished >= %s",
                (started,),
            )
            row = await cur.fetchone()
        return DrainOutcome(executed=int(row[0]), errors=int(row[1]))

    async def prune_history(self, older_than_days: int) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "DELETE FROM pgstac.query_queue_history"
                " WHERE finished < now() - make_interval(days => %s)",
                (older_than_days,),
            )
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


async def drain_tick(
    repo: PgstacQueueRepo,
    *,
    mode: str,
    stale_after_seconds: int,
    history_days: int,
) -> TickResult:
    """One tick: sample, drain if this deployment owns it, sample again, prune.

    Gauges carry the LAST sample — what is left for the next tick. The stale
    flag reads the last sample too: in `pipeline` mode it means a CALL hit
    `queue_timeout` with work left (or is failing outright); in `database`
    mode it means pg_cron has stopped. Both are WARNINGs with the numbers.
    """
    before = await repo.sample()
    after: QueueSample | None = None
    drained: DrainOutcome | None = None

    if mode == "pipeline":
        drained = await repo.drain()
        metrics.PGSTAC_QUEUE_QUERIES.labels(outcome="ok").inc(drained.executed - drained.errors)
        metrics.PGSTAC_QUEUE_QUERIES.labels(outcome="error").inc(drained.errors)
        after = await repo.sample()

    pruned = await repo.prune_history(history_days)

    last = after if after is not None else before
    metrics.PGSTAC_QUEUE_DEPTH.set(last.depth)
    metrics.PGSTAC_QUEUE_OLDEST_SECONDS.set(last.oldest_age_seconds or 0.0)

    stale = last.oldest_age_seconds is not None and last.oldest_age_seconds > stale_after_seconds
    if stale:
        logger.warning(
            "pgstac query queue is stale — the configured drainer is behind or stopped",
            extra={
                "mode": mode,
                "depth": last.depth,
                "oldest_age_seconds": last.oldest_age_seconds,
                "stale_after_seconds": stale_after_seconds,
            },
        )

    return TickResult(
        mode=mode, before=before, after=after, drained=drained, pruned=pruned, stale=stale
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_queue_drain.py tests/test_metrics.py -v && uv run ruff check .`
Expected: PASS; ruff clean. (If `test_metrics.py` has an assertion enumerating `__all__` or the families, extend it with the three new names.)

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/stac/query_queue.py services/pipeline/src/pipeline/metrics.py services/pipeline/tests/test_pgstac_queue_drain.py
git commit -m "feat(pipeline): pgstac query-queue repo seam, drain tick and depth/age metrics (M3-A)"
```

---

### Task 5: The periodic job, registered

**Files:**
- Create: `services/pipeline/src/pipeline/jobs/pgstac_drain.py`
- Modify: `services/pipeline/src/pipeline/main.py:18-32` (imports), `:72` (register)
- Test: `services/pipeline/tests/test_main_jobs.py` (extend), `services/pipeline/tests/test_pgstac_drain_job.py` (new)

**Interfaces:**
- Consumes: `drain_tick`, `PgPgstacQueueRepo` (Task 4); `Settings.pgstac_queue_*` (Task 3).
- Produces: `JOB_NAME = "pipeline.pgstac_queue_drain"`, `CRON = "* * * * *"`, `register(queue, settings)`.

- [ ] **Step 1: Write the failing tests**

`services/pipeline/tests/test_pgstac_drain_job.py`:

```python
"""Job wiring for the pgstac queue drain (M3-A): registered as a one-minute
periodic, the tick built from settings, the outcome logged in structured fields."""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.jobs import pgstac_drain
from pipeline.queue.memory import InMemoryQueue
from pipeline.stac.query_queue import DrainOutcome, QueueSample, TickResult


def test_registers_a_one_minute_periodic():
    queue = InMemoryQueue()
    pgstac_drain.register(queue, Settings.from_env(env={}))
    assert pgstac_drain.JOB_NAME in queue.periodic
    assert queue.periodic[pgstac_drain.JOB_NAME].cron == "* * * * *"


async def test_tick_uses_settings_and_logs_the_result(monkeypatch, caplog):
    captured: dict = {}

    async def _fake_tick(repo, *, mode, stale_after_seconds, history_days):
        captured.update(
            repo_url=repo.database_url,
            mode=mode,
            stale_after_seconds=stale_after_seconds,
            history_days=history_days,
        )
        return TickResult(
            mode=mode,
            before=QueueSample(3, 12.0),
            after=QueueSample(0, None),
            drained=DrainOutcome(executed=3, errors=0),
            pruned=0,
            stale=False,
        )

    monkeypatch.setattr(pgstac_drain, "drain_tick", _fake_tick)
    queue = InMemoryQueue()
    settings = Settings.from_env(
        env={
            "DATABASE_URL": "postgresql://x",
            "PGSTAC_QUEUE_DRAINER": "database",
            "PGSTAC_QUEUE_STALE_SECONDS": "60",
            "PGSTAC_QUEUE_HISTORY_DAYS": "2",
        }
    )
    pgstac_drain.register(queue, settings)

    with caplog.at_level(logging.INFO, logger="pipeline.jobs.pgstac_drain"):
        await queue.periodic[pgstac_drain.JOB_NAME].func(timestamp=123)

    assert captured == {
        "repo_url": "postgresql://x",
        "mode": "database",
        "stale_after_seconds": 60,
        "history_days": 2,
    }
    record = next(r for r in caplog.records if r.levelno == logging.INFO)
    assert record.depth_before == 3
    assert record.depth_after == 0
    assert record.executed == 3
    assert record.scheduled_timestamp == 123


async def test_idle_tick_is_quiet(monkeypatch, caplog):
    async def _fake_tick(repo, **kwargs):
        return TickResult(
            mode="pipeline",
            before=QueueSample(0, None),
            after=QueueSample(0, None),
            drained=DrainOutcome(0, 0),
            pruned=0,
            stale=False,
        )

    monkeypatch.setattr(pgstac_drain, "drain_tick", _fake_tick)
    queue = InMemoryQueue()
    pgstac_drain.register(queue, Settings.from_env(env={}))
    with caplog.at_level(logging.INFO, logger="pipeline.jobs.pgstac_drain"):
        await queue.periodic[pgstac_drain.JOB_NAME].func(timestamp=1)
    # An empty queue every minute is the normal state; don't log it.
    assert not [r for r in caplog.records if r.levelno == logging.INFO]
```

And in `services/pipeline/tests/test_main_jobs.py` add the import and assertion:

```python
from pipeline.jobs.pgstac_drain import JOB_NAME as PGSTAC_DRAIN_JOB
```

```python
    # M3-A: the pgstac query-queue drain (a sampler when pg_cron owns the drain).
    assert PGSTAC_DRAIN_JOB in registered
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_drain_job.py tests/test_main_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.jobs.pgstac_drain'`.

- [ ] **Step 3: Implement the job**

`services/pipeline/src/pipeline/jobs/pgstac_drain.py`:

```python
"""pgstac query-queue drain wiring (M3-A, spec §4.3): a one-minute tick.

Locally the pipeline is the drainer (`PGSTAC_QUEUE_DRAINER=pipeline`); where
pg_cron owns `run_queued_queries` the tick samples only. The cadence IS the
staleness bound on partition statistics (spec §4.6) — one minute is the
scheduler's granularity, and the two `REFRESH MATERIALIZED VIEW`s inside a
drain scale with partition count, not write rate, so the cost to watch is
`pipeline_job_seconds{job="pipeline.pgstac_queue_drain"}` against
`SELECT count(*) FROM pgstac.partitions`.
"""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.queue.interface import QueueBackend
from pipeline.stac.query_queue import PgPgstacQueueRepo, drain_tick

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.pgstac_queue_drain"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def pgstac_queue_drain(timestamp: int) -> None:
        repo = PgPgstacQueueRepo(settings.database_url)
        result = await drain_tick(
            repo,
            mode=settings.pgstac_queue_drainer,
            stale_after_seconds=settings.pgstac_queue_stale_seconds,
            history_days=settings.pgstac_queue_history_days,
        )
        touched = result.before.depth or (result.drained and result.drained.executed) or result.pruned
        if touched:
            logger.info(
                "pgstac query queue tick",
                extra={
                    "mode": result.mode,
                    "depth_before": result.before.depth,
                    "depth_after": result.after.depth if result.after else None,
                    "oldest_age_seconds": (result.after or result.before).oldest_age_seconds,
                    "executed": result.drained.executed if result.drained else 0,
                    "errors": result.drained.errors if result.drained else 0,
                    "pruned_history_rows": result.pruned,
                    "stale": result.stale,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(pgstac_queue_drain, name=JOB_NAME, cron=CRON)
```

In `services/pipeline/src/pipeline/main.py`, add `pgstac_drain` to the `from pipeline.jobs import (...)` block (alphabetical, after `notify`), and after `process.register(queue, settings)` (line 72):

```python
    # M3-A: drain pgstac.query_queue (partition stats deferred by the writer's
    # `use_queue` session GUC) — or only sample it where pg_cron drains.
    pgstac_drain.register(queue, settings)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_pgstac_drain_job.py tests/test_main_jobs.py -v && uv run ruff check .`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/pgstac_drain.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_pgstac_drain_job.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(pipeline): pipeline.pgstac_queue_drain periodic job (M3-A)"
```

---

### Task 6: The harness sees the queue

**Files:**
- Modify: `services/pipeline/src/pipeline/loadgen/sample.py:75-120` (`TABLE_QUERIES`)
- Modify: `services/pipeline/src/pipeline/loadgen/report.py:22-39` (`HEADLINE`)
- Modify: `services/pipeline/src/pipeline/loadgen/README.md` (one paragraph)
- Test: `services/pipeline/tests/test_loadgen.py` (extend)

**Interfaces:**
- Produces: `TABLE_QUERIES["pgstac_query_queue"]`, `TABLE_QUERIES["pgstac_partitions"]`; a `("BACKLOG pgstac queue", "pgstac_query_queue")` headline row.

- [ ] **Step 1: Write the failing test**

Append to `services/pipeline/tests/test_loadgen.py` (beside the existing `TABLE_QUERIES` assertions near line 286):

```python
def test_sampler_watches_the_pgstac_query_queue():
    # M3-A: with use_queue on, the queue's depth is the only outside-the-process
    # evidence the session GUC is in effect, and its drain cost scales with
    # partition count — so both are sampled alongside the ledger counts.
    assert TABLE_QUERIES["pgstac_query_queue"] == "SELECT count(*) FROM pgstac.query_queue"
    assert TABLE_QUERIES["pgstac_partitions"] == "SELECT count(*) FROM pgstac.partitions"
    from pipeline.loadgen.report import HEADLINE

    assert ("BACKLOG pgstac queue", "pgstac_query_queue") in HEADLINE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_loadgen.py -k pgstac_query_queue -v`
Expected: FAIL — `KeyError: 'pgstac_query_queue'`.

- [ ] **Step 3: Add the series**

In `services/pipeline/src/pipeline/loadgen/sample.py`, after the `"pgstac_items"` entry of `TABLE_QUERIES`:

```python
    "pgstac_query_queue": (
        # M3-A: partition-stats statements deferred by the writer's use_queue
        # GUC and waiting for the drain tick. Bounded by partitions written,
        # not by items; a steadily rising count means the drainer stopped.
        "SELECT count(*) FROM pgstac.query_queue"
    ),
    "pgstac_partitions": (
        # The drain's cost (two REFRESH MATERIALIZED VIEWs) scales with this,
        # not with write rate — spec §4.5.
        "SELECT count(*) FROM pgstac.partitions"
    ),
```

In `services/pipeline/src/pipeline/loadgen/report.py`, add to `HEADLINE` after `("catalog items", "pgstac_items")`:

```python
    ("BACKLOG pgstac queue", "pgstac_query_queue"),
```

In `services/pipeline/src/pipeline/loadgen/README.md`, add under "## Isolating stages" (or a new "## What to read after M3-A" heading above it):

```markdown
## Reading the pgstac queue (M3-A)

With `use_queue` on, `catalog items` climbs at the write rate while `BACKLOG
pgstac queue` stays flat and small — it is bounded by the number of partitions
written since the last drain, not by items. Two things to record per run: the
drain tick's mean seconds (`pipeline.pgstac_queue_drain` in the per-job table)
against `pgstac_partitions` in the end-of-window counts, because that cost
scales with partition count and is what sets the drain cadence; and that the
queue returns to 0 within a tick of the feed ending. A queue that only grows
means the drainer is not running — check `PGSTAC_QUEUE_DRAINER` and the
`pipeline_pgstac_query_queue_oldest_seconds` gauge on `/metrics`.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_loadgen.py -v && uv run ruff check .`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/loadgen
git commit -m "feat(loadgen): sample pgstac.query_queue depth and partition count (M3-A)"
```

---

### Task 7: DB-gated proof (runs against the compose Postgres)

**Files:**
- Create: `services/pipeline/tests/test_integration_pgstac_queue.py`

**Interfaces:**
- Consumes: `writer_pool`, `close_writer_pools`, `PgPgstacWriter` (Task 2); `PgPgstacQueueRepo` (Task 4).

- [ ] **Step 1: Write the test**

```python
"""pgstac session GUCs + queue drain against a real pgstac (M3-A).

Auto-skips unless DATABASE_URL is set. Proves what the unit tests cannot:
that the GUCs survive a pool checkout (SET is transactional — the configure
hook must commit), that a writer upsert lands its partition-stats statement in
`pgstac.query_queue` instead of running it inline, and that the drain CALL runs
it and records it in `query_queue_history`.

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_pgstac_queue.py

Shares the database with whatever the stack is doing (the standing GOES demo
enqueues too), so the assertions are "at least" and "no longer present", never
exact counts.
"""

from __future__ import annotations

import json
import os

import psycopg
import pytest

from pipeline.stac.pgstac_writer import PgPgstacWriter, close_writer_pools, writer_pool
from pipeline.stac.query_queue import PgPgstacQueueRepo

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

COLLECTION = "m3a-itest"


def _item(item_id: str, dtstr: str) -> dict:
    return {
        "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
        "id": item_id, "collection": COLLECTION,
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "bbox": [0, 0, 1, 1],
        "properties": {"datetime": dtstr}, "assets": {}, "links": [],
    }


@pytest.fixture
async def collection():
    coll = {
        "type": "Collection", "stac_version": "1.0.0", "id": COLLECTION,
        "description": "m3a itest", "license": "proprietary",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]]},
                   "temporal": {"interval": [[None, None]]}}, "links": [],
    }
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute("SELECT pgstac.create_collection(%s::jsonb)", (json.dumps(coll),))
    yield COLLECTION
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute("SELECT pgstac.delete_collection(%s)", (COLLECTION,))
    close_writer_pools()


def test_pooled_connection_carries_both_gucs():
    pool = writer_pool(DATABASE_URL)
    with pool.connection() as conn:
        use_queue = conn.execute("SELECT pgstac.get_setting('use_queue')").fetchone()[0]
        extent = conn.execute(
            "SELECT pgstac.get_setting_bool('update_collection_extent')"
        ).fetchone()[0]
    assert use_queue == "true"
    assert extent is True
    # And the TABLE is untouched — the setting is session-scoped by design.
    with psycopg.connect(DATABASE_URL) as plain:
        table = plain.execute(
            "SELECT value FROM pgstac.pgstac_settings WHERE name = 'use_queue'"
        ).fetchone()
    assert table is None or table[0] == "false"
    close_writer_pools()


async def test_upsert_queues_partition_stats_and_the_drain_runs_them(collection):
    writer = PgPgstacWriter(DATABASE_URL)
    repo = PgPgstacQueueRepo(DATABASE_URL)

    # Start from a drained queue so the assertion below is about OUR write.
    await repo.drain()

    await writer.upsert_items([_item("m3a-1", "2026-09-07T00:00:00Z")])

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT query FROM pgstac.query_queue WHERE query ILIKE %s",
            (f"%{COLLECTION}%",),
        )
        queued = [r[0] for r in await cur.fetchall()]
    assert queued, "the upsert should have QUEUED update_partition_stats, not run it inline"
    assert all("update_partition_stats" in q for q in queued)

    before = await repo.sample()
    assert before.depth >= 1 and before.oldest_age_seconds is not None

    outcome = await repo.drain()
    assert outcome.executed >= 1
    assert outcome.errors == 0

    after = await repo.sample()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pgstac.query_queue WHERE query ILIKE %s",
            (f"%{COLLECTION}%",),
        )
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT count(*) FROM pgstac.query_queue_history"
            " WHERE query ILIKE %s AND error IS NULL",
            (f"%{COLLECTION}%",),
        )
        assert (await cur.fetchone())[0] >= 1
        # The queued statement also refreshed the collection's extent (§4.4):
        # it is no longer the declared world bbox.
        cur = await conn.execute(
            "SELECT content->'extent'->'spatial'->'bbox'->0 FROM pgstac.collections WHERE id = %s",
            (COLLECTION,),
        )
        bbox = (await cur.fetchone())[0]
    assert [float(v) for v in bbox] == [0.0, 0.0, 1.0, 1.0]
    assert after.depth <= before.depth

    pruned = await repo.prune_history(0)
    assert pruned >= 1
```

- [ ] **Step 2: Run it against the compose stack**

Preconditions: `docker compose up -d --wait` (repo root); the stack's pgstac is exposed on `:5433`.

Run: `cd services/pipeline && DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest tests/test_integration_pgstac_queue.py -v`
Expected: 2 passed. If `test_pooled_connection_carries_both_gucs` fails with `use_queue == 'false'`, the configure hook did not commit — that is exactly the failure Task 1's design prevents; fix the hook, not the test. If the extent assertion fails, check `SELECT pgstac.get_setting_bool('update_collection_extent')` on the pooled connection first (the GUC), then that `update_partition_stats` ran (`query_queue_history.error`).

Without Docker (a teammate): `uv run pytest tests/test_integration_pgstac_queue.py -v` → 2 skipped. The lead runs it for real in Task 9.

- [ ] **Step 3: Commit**

```bash
git add services/pipeline/tests/test_integration_pgstac_queue.py
git commit -m "test(pipeline): DB-gated proof of the pgstac session GUCs and the queue drain (M3-A)"
```

---

### Task 8: Docs and compose

**Files:**
- Modify: `services/pipeline/README.md` — "Environment contract" table (after the `GC_BATCH_ITEMS` row), "Jobs" table (lines 92–96), "Telemetry (M2-H)" list (lines 369–386)
- Modify: `docker-compose.yml:282` (pipeline env, after `CATALOG_HREF_BASE`)
- Modify: `docs/FEATURES.md` — a new section before "## Phase 8 — Not started"
- Modify: `docs/ISSUES.md` — no new issue; nothing to change unless the live check finds one

- [ ] **Step 1: README — env rows**

Add after the `GC_BATCH_ITEMS` row:

```markdown
| `PGSTAC_QUEUE_DRAINER` | `pipeline` | Who runs `pgstac.query_queue` (M3-A). `pipeline`: the `pipeline.pgstac_queue_drain` tick `CALL`s `pgstac.run_queued_queries()` every minute. `database`: pg_cron owns the drain (RDS/Aurora — not in the local pgstac image) and the tick only samples depth/age, so the two never fight. The writer's `use_queue` + `update_collection_extent` are SESSION GUCs on its own connections and need no configuration anywhere. |
| `PGSTAC_QUEUE_STALE_SECONDS` | `300` | The queue's oldest entry older than this logs a WARNING — the staleness bound on partition statistics; a rising `pipeline_pgstac_query_queue_oldest_seconds` means whichever drainer is configured has stopped. |
| `PGSTAC_QUEUE_HISTORY_DAYS` | `7` | `pgstac.query_queue_history` rows older than this are deleted by the same tick (pgstac never prunes that table). |
```

- [ ] **Step 2: README — jobs row + telemetry bullet**

In the "Jobs" table add:

```markdown
| `pipeline.pgstac_queue_drain` | `* * * * *` | M3-A. Samples `pgstac.query_queue` (depth + oldest age → `/metrics`), `CALL`s `pgstac.run_queued_queries()` when `PGSTAC_QUEUE_DRAINER=pipeline`, prunes `query_queue_history`. The queue holds the partition-statistics refreshes the writer defers through its `use_queue` session GUC; its depth is bounded by partitions written, not items. |
```

In "Telemetry (M2-H)" add a bullet:

```markdown
- `pipeline_pgstac_query_queue_depth`, `pipeline_pgstac_query_queue_oldest_seconds`
  (gauges, set by the drain tick) and `pipeline_pgstac_queue_queries_total{outcome}`
  — the only outside-the-process evidence the writer's session-scoped
  `use_queue` is in effect, and the alarm for a drainer that stopped (M3-A).
```

- [ ] **Step 3: compose env**

After the `CATALOG_HREF_BASE` line in the pipeline service:

```yaml
      # M3-A: pgstac partition statistics are queued by the writer (a session
      # GUC — nothing to configure) and drained by the pipeline's one-minute
      # tick. A database with pg_cron draining sets `database` so the tick
      # only samples. See services/pipeline/README.md.
      - PGSTAC_QUEUE_DRAINER=${PGSTAC_QUEUE_DRAINER:-pipeline}
      - PGSTAC_QUEUE_STALE_SECONDS=${PGSTAC_QUEUE_STALE_SECONDS:-300}
      - PGSTAC_QUEUE_HISTORY_DAYS=${PGSTAC_QUEUE_HISTORY_DAYS:-7}
```

- [ ] **Step 4: FEATURES**

Insert before `## Phase 8 — Not started ⬜`:

```markdown
## NOAA-scale readiness (M3 queue, 2026-09-01) 🔄

Spec: `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md`; evidence
`2026-08-31-m3-scoping-notes.md`; slices M3-A…M3-I in `TODO.md`.

| Feature | Status | Notes |
|---|---|---|
| M3-A · pgstac write path | ✅ | `pgstac.use_queue` + `pgstac.update_collection_extent` are SESSION GUCs on the writer's own pooled connections (`pipeline/db/pgstac_session.py`, `stac/pgstac_writer.py` — pypgstac's `PgstacDB(pool=…, use_queue=True)` seam; nothing in deployment config), so every item write queues its partition's statistics refresh instead of scanning the partition inline (measured 2–3.5 → ~22 items/s, S-A §1) and collection extents are maintained for the first time since Phase 4. `pipeline.pgstac_queue_drain` (`stac/query_queue.py`, `jobs/pgstac_drain.py`) drains the queue every minute locally (`PGSTAC_QUEUE_DRAINER=pipeline`) or only samples it where pg_cron does (`database`); depth + oldest age are gauges on `/metrics`, a stale queue is a WARNING, `query_queue_history` is pruned. Loadgen samples the queue and the partition count. Live numbers: `TODO.md` follow-ups. |
```

- [ ] **Step 5: Gate and commit**

Run: `cd services/pipeline && uv run pytest && uv run ruff check . && cd ../.. && npm run verify`
Expected: all green.

```bash
git add services/pipeline/README.md docker-compose.yml docs/FEATURES.md
git commit -m "docs(pipeline): M3-A env, job, telemetry and feature entries"
```

---

### Task 9: Measure, record, merge (LEAD — Docker + the shared stack)

**Files:**
- Modify: `TODO.md` (M3-A checkbox; queue-table state; a "M3-A landed" follow-ups entry with the numbers)

The standing GOES demo shares this stack and keeps running; the probe is namespaced by `--label` and torn down. The demo's own ingest is the live canary that the new writer works on real data.

- [ ] **Step 1: Deploy the slice to the stack**

```bash
cd <repo root>
set -a; source .env; set +a
docker compose build pipeline && docker compose up -d pipeline
docker compose logs -f pipeline   # until "pipeline service starting"; Ctrl-C
```

Expected in the logs within two minutes: no `pgstac_queue_drain` errors; `curl -s localhost:8083/metrics | grep pipeline_pgstac_query_queue` shows both gauges present (depth may be 0).

- [ ] **Step 2: The DB-gated test, for real**

`cd services/pipeline && DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest tests/test_integration_pgstac_queue.py -v` → 2 passed.

- [ ] **Step 3: Canary — the standing demo still ingests**

Watch one poll cycle of the `goes-abi-mcmipc` association: `docker compose logs --since 3m pipeline | grep -E "ingest_(discover|itemize)|pgstac"`. Expected: new granules itemize as before; the next drain tick logs `pgstac query queue tick` with `depth_before ≥ 1`, `executed ≥ 1`, `errors 0`.

- [ ] **Step 4: The measurement (spec §4.5 — "measure and record")**

```bash
cd services/pipeline
uv run python -m pipeline.loadgen --label m3a setup --mode copy --metadata defaults_only
uv run python -m pipeline.loadgen --label m3a feed --rate 0 --count 2000 --asset-bytes 65536
sleep 60
uv run python -m pipeline.loadgen --label m3a watch --seconds 300 --interval 20
uv run python -m pipeline.loadgen --label m3a teardown
```

Record from the report: sustained `itemized` items/s over the window (S-A measured ~22 with the setting; before it, 2–3.5 and decaying); `BACKLOG pgstac queue` per interval (expected: flat and small, back to 0 within a tick of the feed ending); the per-job mean seconds for `pipeline.pgstac_queue_drain`; `pgstac_partitions` from the end-of-window counts. That pair — drain seconds against partition count — is the §4.5 number.

- [ ] **Step 5: (Optional, one-off) backfill the stale collection extents**

Every pre-M3-A collection still advertises the extent it was created with (`TODO.md` follow-ups: "Collection extents have been stale since Phase 4"). pgstac ships the backfill:

```bash
docker compose exec database psql -U username -d postgis -c "SELECT pgstac.update_collection_extents();"
```

It rewrites every collection's `extent` from the partition statistics the drain now maintains. Do it once, after Step 4 has drained; check `GET /collections/goes-geocolor` afterwards shows a CONUS bbox rather than the world. If skipped, say so in the record.

- [ ] **Step 6: Record**

`TODO.md`: tick M3-A (`- [x]`, appending `(merged <date>)`); queue table M3 row → `M3-A merged <date>; M3-B next`; under "Discovered follow-ups" append:

```markdown
- **M3-A landed <date> (`ai/m3-a-pgstac-queue`).** Session GUCs via the writer's
  pool `configure` hook (pypgstac's `PgstacDB(pool=…, use_queue=True)` seam —
  a handed-in `connection` would have skipped pypgstac's own SET, so the pool
  is what it gets; and `SET` is transactional, so the hook commits). Measured
  on the shared stack, `--rate 0 --count 2000`: itemized <N> items/s sustained
  (was 2–3.5, S-A); `BACKLOG pgstac queue` peaked at <n> and returned to 0
  within one tick; drain tick mean <s> s at <p> partitions (§4.5 — the number
  to re-measure when partition count grows); collection extents backfilled
  with `pgstac.update_collection_extents()` <or: not run>. pgstac's
  `query_queue_history` is pruned at 7 days by the tick (pgstac never prunes
  it). Not built: an alert on a stale queue — WARNING + gauges only; M3-F's
  open-mark alert is the pattern if one is wanted.
```

```bash
git add TODO.md
git commit -m "docs: M3-A done — pgstac write path, measured"
```

- [ ] **Step 7: Merge**

```bash
cd <repo root>
git checkout ai/main
git merge ai/m3-a-pgstac-queue --no-ff -m "Merge ai/m3-a-pgstac-queue: M3-A pgstac write path (use_queue session GUCs + drain + metrics)"
npm run verify && (cd services/pipeline && uv run pytest && uv run ruff check .)
git worktree remove .claude/worktrees/m3-a-pgstac-queue
git branch -d ai/m3-a-pgstac-queue
```

Do not push `ai/main`.

---

## Self-review

**Spec coverage (§4, §5, §7.1, §7.3; TODO M3-A):**
- Both settings as session GUCs on the writer's connection, via `PgstacDB(use_queue=True)` + the same connection → Task 1 (hook) + Task 2 (pool with `configure`, `PgstacDB(pool=…)`); pypgstac's handed-in-connection gap and the transactional-`SET` gap are both closed by construction and proven in Task 7. ✓
- Deployment configuration sets nothing → no compose/app/table change for the GUCs (Task 8 adds only the drainer knobs). ✓
- Drainer: pg_cron in cloud, a pipeline job locally, the job no-ops (samples only) when a database-side drainer is configured → Tasks 3, 4, 5 (`PGSTAC_QUEUE_DRAINER`). ✓
- `run_queued_queries()` is a PROCEDURE — `CALL`, autocommit → Task 4 (`PgPgstacQueueRepo.drain`), Global Constraints. ✓
- `query_queue` depth metric → Task 4 (two gauges + a counter), surfaced in Task 8 docs; the harness reads the queue → Task 6. ✓
- Measure and record the drain cost against partition count (§4.5) → Task 6 (`pgstac_partitions` sampled) + Task 9 Step 4/6. ✓
- Verify against the harness, not by inspection → Task 9. ✓
- Staleness bound stated and monitored (§4.6) → `PGSTAC_QUEUE_STALE_SECONDS` + the WARNING + the age gauge (Tasks 3–4). ✓
- §5's "startup assert-and-warn on the setting" was written for the pre-reversal draft (a deployment-config setting); with the GUC issued by the pipeline itself there is nothing to assert at startup — the Task 7 test and the gauges are the equivalent. Noted here so a reviewer does not look for it. ✓
- Collection extents stale since Phase 4 (TODO follow-up) → Task 9 Step 5 offers pgstac's own one-off backfill. ✓

**Placeholders:** none — every step carries its code or command; Task 9's `<N>`/`<n>`/`<s>`/`<p>` are the measurement slots the lead fills.

**Type consistency:** `configure_pgstac_session` (Task 1) is the `configure=` callback in Task 2 and the assertion in its test; `writer_pool` / `close_writer_pools` / `WRITER_POOL_MAX` named identically in Task 2's code, test and `main.py`; `QueueSample` / `DrainOutcome` / `TickResult` / `drain_tick(repo, *, mode, stale_after_seconds, history_days)` identical across Task 4's module, Task 4's tests and Task 5's job + tests; `Settings.pgstac_queue_drainer|stale_seconds|history_days` (Task 3) are the fields Task 5 reads; metric names in Task 4 match Task 8's docs and Task 6's README paragraph.
