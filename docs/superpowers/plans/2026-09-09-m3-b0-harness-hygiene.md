# M3-B0 · Harness hygiene + ADR 0020 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pipeline.loadgen teardown` stops stranding `pgstac.query_queue` rows for the partition it drops, and ADR 0020 records the opposite pgstac GUC pairings so no later slice "fixes" them.

**Architecture:** One new pure module `pipeline/loadgen/pgstac_hygiene.py` owns the drop sequence (resolve the partition name while the collection still exists → delete that partition's queued and historical statements → `pgstac.delete_collection`); `cmd_teardown` calls it instead of calling `delete_collection` inline. It is unit-tested with a recording fake cursor, so the ordering and the quoted `ILIKE` pattern are pinned without a database. The ADR is a docs task: the record, its index row, its invariant bullet, and one pointer from each of the two code sites and the FEATURES entry.

**Tech Stack:** Python 3.12, psycopg 3 (sync, in the loadgen), pytest, ruff, uv. Markdown for the ADR.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §4.2 and §4.4 (both carry the 2026-09-08 correction), `TODO.md` follow-ups "M3-A landed 2026-09-08" and "Loadgen teardown is not queue-aware", commit `2f81348`.

## Global Constraints

- **Worktree:** `.claude/worktrees/m3-b0-harness-hygiene`, branch `ai/m3-b0-harness-hygiene` off `ai/main` (already created by the lead). No `npm install` needed — pipeline + docs only.
- **Gates before merge:** from `services/pipeline/`: `uv run pytest -q` and `uv run ruff check .`; from the worktree root `npm run verify` (the lead runs it after merge; the teammate runs pytest + ruff only). No e2e, no dev server, no Docker for the teammate.
- **ADR 0001:** the pipeline never runs DDL. `pgstac.delete_collection(...)` is a pgstac function call the harness already makes; the new code adds `DELETE FROM pgstac.query_queue` / `query_queue_history` row deletions only.
- **The GUCs do not move.** This slice documents the writer's pairing (`use_queue` ON + `update_collection_extent` ON in `pipeline/db/pgstac_session.py`) and the drainer's pairing (`update_collection_extent` ON + `use_queue` explicitly FALSE in `pipeline/stac/query_queue.py`'s `DRAIN_CONNECTION_SQL`). It changes no SQL in either file — comments only.
- **Quoted pattern.** The queue-row match is `%'<partition>'%` (with the single quotes), so `_items_3` cannot match `_items_34`. Same shape as `tests/test_integration_pgstac_queue.py` and `tests/test_integration_itemize.py`.
- **Structured logging:** the loadgen prints JSON to stdout, as it does today; no `logging` calls are added.
- **ADRs are immutable once accepted.** ADR 0020 is a NEW record; do not edit 0001–0019.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 1: Queue-aware teardown — `pipeline.loadgen.pgstac_hygiene`

**Files:**
- Create: `services/pipeline/src/pipeline/loadgen/pgstac_hygiene.py`
- Modify: `services/pipeline/src/pipeline/loadgen/__main__.py` — `cmd_teardown` (anchor: the `if not args.keep_items:` block that calls `SELECT pgstac.delete_collection(%s)`), plus the final `print(json.dumps({...}))`
- Modify: `services/pipeline/src/pipeline/loadgen/README.md` — the teardown paragraph (anchor: the word `teardown`; append one sentence)
- Test: `services/pipeline/tests/test_loadgen.py` (append a new section)

**Interfaces:**
- Produces:
  ```python
  # pipeline/loadgen/pgstac_hygiene.py
  def partition_queue_pattern(partition: str) -> str        # "%'_items_34'%"
  def resolve_partition(cur, collection_id: str) -> str | None   # "_items_<key>" or None when the collection row is gone
  def clear_partition_queue(cur, partition: str) -> int     # rows deleted from query_queue (history rows are deleted too but not counted)
  def drop_probe_collection(cur, collection_id: str) -> int # resolve → clear → SELECT pgstac.delete_collection(id); returns the cleared count
  ```
  `cur` is any object with `execute(sql, params)`, `fetchone()` and `rowcount` — a `psycopg.Cursor` in production, a fake in tests.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_loadgen.py`:

```python
# --------------------------------------------------------------------------- #
# teardown is queue-aware (M3-B0)
# --------------------------------------------------------------------------- #

from pipeline.loadgen.pgstac_hygiene import (  # noqa: E402
    clear_partition_queue,
    drop_probe_collection,
    partition_queue_pattern,
    resolve_partition,
)


class _FakeCursor:
    """Records every statement; answers `fetchone` from a scripted queue."""

    def __init__(self, key: int | None, queue_rows: int = 0):
        self.key = key
        self.queue_rows = queue_rows
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((" ".join(sql.split()), params))
        if "FROM pgstac.query_queue " in sql or sql.rstrip().endswith("pgstac.query_queue WHERE query ILIKE %s"):
            self.rowcount = self.queue_rows
        else:
            self.rowcount = 0
        return self

    def fetchone(self):
        return (self.key,) if self.key is not None else None


def test_the_queue_pattern_is_quoted_so_a_short_key_cannot_match_a_longer_one():
    assert partition_queue_pattern("_items_3") == "%'_items_3'%"
    assert "_items_34" not in partition_queue_pattern("_items_3").replace("'", "")[0:0]
    # `%'_items_3'%` matches `...('_items_3', 't')` and not `...('_items_34', 't')`
    assert "'_items_3'" in partition_queue_pattern("_items_3")


def test_resolve_partition_reads_the_key_while_the_collection_exists():
    cur = _FakeCursor(key=34)
    assert resolve_partition(cur, "m3-load-x") == "_items_34"
    sql, params = cur.calls[0]
    assert sql == "SELECT key FROM pgstac.collections WHERE id = %s"
    assert params == ("m3-load-x",)


def test_resolve_partition_is_none_for_a_collection_that_is_already_gone():
    assert resolve_partition(_FakeCursor(key=None), "m3-load-x") is None


def test_clear_partition_queue_deletes_queue_then_history_with_the_quoted_pattern():
    cur = _FakeCursor(key=34, queue_rows=2)
    assert clear_partition_queue(cur, "_items_34") == 2
    assert [c[0] for c in cur.calls] == [
        "DELETE FROM pgstac.query_queue WHERE query ILIKE %s",
        "DELETE FROM pgstac.query_queue_history WHERE query ILIKE %s",
    ]
    assert all(c[1] == ("%'_items_34'%",) for c in cur.calls)


def test_drop_probe_collection_clears_the_partition_queue_BEFORE_dropping():
    cur = _FakeCursor(key=34, queue_rows=1)
    assert drop_probe_collection(cur, "m3-load-x") == 1
    statements = [c[0] for c in cur.calls]
    assert statements == [
        "SELECT key FROM pgstac.collections WHERE id = %s",
        "DELETE FROM pgstac.query_queue WHERE query ILIKE %s",
        "DELETE FROM pgstac.query_queue_history WHERE query ILIKE %s",
        "SELECT pgstac.delete_collection(%s)",
    ]
    assert cur.calls[-1][1] == ("m3-load-x",)


def test_drop_probe_collection_skips_the_queue_when_the_collection_is_already_gone():
    cur = _FakeCursor(key=None)
    assert drop_probe_collection(cur, "m3-load-x") == 0
    statements = [c[0] for c in cur.calls]
    assert statements == [
        "SELECT key FROM pgstac.collections WHERE id = %s",
        "SELECT pgstac.delete_collection(%s)",
    ]
```

Then simplify the first test: replace its body with the two meaningful assertions only —

```python
def test_the_queue_pattern_is_quoted_so_a_short_key_cannot_match_a_longer_one():
    pattern = partition_queue_pattern("_items_3")
    assert pattern == "%'_items_3'%"
    # An ILIKE against the queued text: the quotes are what keep `_items_3`
    # from matching `update_partition_stats('_items_34', 't')`.
    import fnmatch
    like = pattern.replace("%", "*")
    assert fnmatch.fnmatch("SELECT update_partition_stats('_items_3', 't')", like)
    assert not fnmatch.fnmatch("SELECT update_partition_stats('_items_34', 't')", like)
```

(Write the simplified version; the first draft above is shown only so the intent is unambiguous. Do not keep the `[0:0]` line.)

Also simplify `_FakeCursor.execute`'s rowcount rule to exactly:

```python
    def execute(self, sql: str, params: tuple = ()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        self.rowcount = self.queue_rows if normalized.startswith("DELETE FROM pgstac.query_queue WHERE") else 0
        return self
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `services/pipeline/`: `uv run pytest tests/test_loadgen.py -q -k "queue or partition or drop_probe"`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'pipeline.loadgen.pgstac_hygiene'`.

- [ ] **Step 3: Write the module**

Create `services/pipeline/src/pipeline/loadgen/pgstac_hygiene.py`:

```python
"""Drop a probe's pgstac collection without stranding its queued statistics.

Since M3-A the pipeline's writer runs with `pgstac.use_queue` ON, so every
item write QUEUES `update_partition_stats('_items_<key>')` into
`pgstac.query_queue` instead of running it inline. `pgstac.delete_collection`
drops the partition but not those rows; the next drain tick
(`pipeline.pgstac_queue_drain`, every minute) then executes each one, gets
`relation "_items_<key>" does not exist`, records an error row in
`query_queue_history` and bumps
`pipeline_pgstac_query_queue_queries_total{outcome="error"}` — permanent noise
on a shared database (seen live after the 2026-09-08 `m3a` teardown:
`executed: 2, errors: 2`). Hygiene, not correctness: the queue self-heals.

pgstac names the queued statement after the PARTITION (`_items_<key>`, where
`<key>` is `pgstac.collections.key`, a serial), never after the collection
id, so the name must be resolved WHILE the collection row still exists.
Same shape as `tests/test_integration_pgstac_queue.py`. ADR 0020 has the
GUC pairings this cleanup exists because of.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Cursor(Protocol):
    rowcount: int

    def execute(self, query: str, params: Any = ...) -> Any: ...
    def fetchone(self) -> Any: ...


def partition_queue_pattern(partition: str) -> str:
    """ILIKE pattern for the queued statements of one partition.

    Quoted, so `_items_3` cannot match `_items_34`: the queued text reads
    `SELECT update_partition_stats('_items_3', 't')`."""
    return f"%'{partition}'%"


def resolve_partition(cur: _Cursor, collection_id: str) -> str | None:
    """`_items_<key>` for the collection, or None once its row is gone."""
    cur.execute("SELECT key FROM pgstac.collections WHERE id = %s", (collection_id,))
    row = cur.fetchone()
    return None if row is None else f"_items_{row[0]}"


def clear_partition_queue(cur: _Cursor, partition: str) -> int:
    """Delete the partition's queued and historical statements; returns the
    number of QUEUED rows removed (the ones that would have errored)."""
    pattern = partition_queue_pattern(partition)
    cur.execute("DELETE FROM pgstac.query_queue WHERE query ILIKE %s", (pattern,))
    cleared = cur.rowcount
    cur.execute("DELETE FROM pgstac.query_queue_history WHERE query ILIKE %s", (pattern,))
    return cleared


def drop_probe_collection(cur: _Cursor, collection_id: str) -> int:
    """Resolve → clear → drop, in that order. Returns the cleared queue rows."""
    partition = resolve_partition(cur, collection_id)
    cleared = clear_partition_queue(cur, partition) if partition is not None else 0
    cur.execute("SELECT pgstac.delete_collection(%s)", (collection_id,))
    return cleared
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loadgen.py -q -k "queue or partition or drop_probe"`
Expected: 6 passed.

- [ ] **Step 5: Wire `cmd_teardown` to it**

In `services/pipeline/src/pipeline/loadgen/__main__.py`, add to the imports (after `from pipeline.loadgen.fixtures import (...)` block, keeping alphabetical order):

```python
from pipeline.loadgen.pgstac_hygiene import drop_probe_collection
```

Replace, in `cmd_teardown`:

```python
            if not args.keep_items:
                cur.execute("SELECT pgstac.delete_collection(%s)", (n["collection"],))
        conn.commit()
    print(json.dumps({"objects_removed": removed, "label": args.label}, indent=2))
    return 0
```

with:

```python
            # M3-A made the writer queue `update_partition_stats` per
            # partition; dropping the collection without clearing those rows
            # leaves statements the next drain tick can only error on.
            # `drop_probe_collection` resolves the partition name first, then
            # clears queue + history, then drops (pgstac_hygiene.py).
            queue_rows_cleared = (
                0 if args.keep_items else drop_probe_collection(cur, n["collection"])
            )
        conn.commit()
    print(
        json.dumps(
            {
                "objects_removed": removed,
                "queue_rows_cleared": queue_rows_cleared,
                "label": args.label,
            },
            indent=2,
        )
    )
    return 0
```

- [ ] **Step 6: README sentence**

In `services/pipeline/src/pipeline/loadgen/README.md`, find the paragraph describing `teardown` and append this sentence to it:

```
Since M3-B0 teardown is queue-aware: it resolves the probe collection's
partition name, deletes that partition's rows from `pgstac.query_queue` and
`query_queue_history`, and only then calls `pgstac.delete_collection`, so a
drain tick after a teardown never errors on a dropped partition (the JSON
output reports `queue_rows_cleared`).
```

- [ ] **Step 7: Full gates and commit**

Run from `services/pipeline/`: `uv run pytest -q && uv run ruff check .`
Expected: all green (1118 passed or more, 6 skipped; ruff "All checks passed!").

```bash
git add services/pipeline/src/pipeline/loadgen/pgstac_hygiene.py \
        services/pipeline/src/pipeline/loadgen/__main__.py \
        services/pipeline/src/pipeline/loadgen/README.md \
        services/pipeline/tests/test_loadgen.py
git commit -m "feat(loadgen): queue-aware teardown — clear the partition's pgstac.query_queue rows before dropping the collection

Since M3-A the writer queues update_partition_stats per partition; dropping
the probe collection stranded those rows and the next drain tick errored on
them (TODO.md follow-up 'Loadgen teardown is not queue-aware')."
```

---

### Task 2: ADR 0020 — the pgstac GUC pairings

**Files:**
- Create: `docs/decisions/0020-pgstac-session-guc-pairings.md`
- Modify: `docs/decisions/README.md` — the index table (append a row after 0019), the "Key invariants" list (append a **0020** bullet after **0019**), and "Adding an ADR" step 2 (`next: \`0020\`` → `next: \`0021\``)
- Modify: `services/pipeline/src/pipeline/db/pgstac_session.py` — the module docstring's first paragraph (append one sentence pointing at ADR 0020); the `PGSTAC_SESSION_SQL` comment block's last sentence (append "(ADR 0020)")
- Modify: `services/pipeline/src/pipeline/stac/query_queue.py` — the module docstring's last paragraph (append one sentence pointing at ADR 0020)
- Modify: `docs/FEATURES.md` — the `| M3-A · pgstac write path |` row (append "ADR 0020 records the pairing." before the closing `|` of its last cell)

**Interfaces:** none (docs + comments).

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0020-pgstac-session-guc-pairings.md` with exactly this content:

```markdown
# ADR 0020 — pgstac session GUC pairings: the writer and the drainer carry OPPOSITE settings

- **Status:** accepted (2026-09-09 — records what M3-A shipped 2026-09-08 and
  the spec correction in `2f81348`; implemented in
  `services/pipeline/src/pipeline/db/pgstac_session.py` and
  `services/pipeline/src/pipeline/stac/query_queue.py`)
- **Related:** ADR 0001 (the pipeline never runs DDL — these are `SET`s on
  the pipeline's own sessions, not schema), ADR 0006 (the pinned
  `pypgstac[psycopg]==0.9.11` whose `PgstacDB(use_queue=True)` seam this
  uses), M3 design spec
  `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §4.2 / §4.4
  (with the 2026-09-08 corrections), `TODO.md` follow-up "M3-A landed
  2026-09-08".

## Context

pgstac 0.9.11 has two settings the bulk write path cares about, and
`pgstac.get_setting` resolves each one as `conf jsonb → session GUC
(current_setting) → pgstac_settings table`, so a session-level `SET`
overrides the table for that session only:

- **`pgstac.use_queue`** — read by `run_or_queue` **in the calling
  session**. When ON, the item trigger's `update_partition_stats_q` queues
  `SELECT update_partition_stats('_items_<key>', …)` into
  `pgstac.query_queue` instead of running it inline. This is the whole M3-A
  win: 2–3.5 → ~22 items/s measured.
- **`pgstac.update_collection_extent`** — read **inside**
  `update_partition_stats` (`IF get_setting_bool('update_collection_extent')`),
  which under the queue no longer runs in the writer's session at all. It
  runs later, in whichever session executes `CALL
  pgstac.run_queued_queries()`. Its extent-refresh branch calls
  `run_or_queue` **again**, so it also re-reads `use_queue` — in the
  draining session.

The lead's original instruction (spec §4.2, 2026-09-01) was "whatever sets
`use_queue` should also set `update_collection_extent`", on one connection.
That is a reasonable reading of the pgstac docs and turned out not to be
implementable: the two settings are consumed in different sessions. Set only
on the writer, `update_collection_extent` was a silent no-op — nothing
errored, the queue drained, throughput improved, and collection extents
stayed at whatever they were created with (locally, `[-180,-90,180,90]`),
which is the gap §4.4 exists to close.

## Decision

Two connection kinds, two **opposite** pairings, each self-enforcing:

| connection | `pgstac.use_queue` | `pgstac.update_collection_extent` | where |
|---|---|---|---|
| the pgstac **writer's** pool (`psycopg_pool.ConnectionPool`, sync, `pypgstac`'s `PgstacDB(pool=…, use_queue=True)` seam) | **ON** | ON (inert here — never read in this session; kept because it is harmless and the spec's record) | `pipeline/db/pgstac_session.py`, `PGSTAC_SESSION_SQL`, applied by the pool's `configure` hook, which COMMITs (a `SET` is transactional; the pool's reset would undo an uncommitted one) |
| the queue **drainer's** short-lived AUTOCOMMIT connection (`CALL pgstac.run_queued_queries()` is a PROCEDURE that COMMITs inside itself and cannot run in a transaction block) | **explicitly FALSE** | **ON** | `pipeline/stac/query_queue.py`, `DRAIN_CONNECTION_SQL`, applied per connection before the CALL |
| M3-B's async repo pool (`pipeline/db/pool.py`, transactional, serves the `stac_higher.*` repos) | carries the writer pairing through the same `configure_pgstac_session_async` hook — harmless, because no repo statement fires the pgstac item trigger | — | the drainer's repo stays OFF this pool |

Two details are load-bearing and look like mistakes to a fresh reader:

1. **`use_queue` is explicitly `FALSE` on the drainer, not merely unset.**
   `get_setting` COALESCEs an unset GUC through the `pgstac_settings` table
   (or an `ALTER DATABASE` / `ALTER ROLE SET`). An operator enabling the queue
   globally would otherwise silently re-arm the bug in 2. An explicit FALSE
   makes the drainer's pairing independent of that default.
2. **The drainer must never run with `use_queue` ON**, because the extent
   branch re-enters `run_or_queue`. A drain session with the queue on would
   re-queue the extent `UPDATE` instead of executing it — one hop further on
   every tick, forever, with no error and a queue that never empties.

The settings are session GUCs shipped with the release — never a
`pgstac_settings` row, never a deployment step — so the "a deployment
silently runs 10× slower" failure mode does not exist and every session
that is not the bulk writer (search, app BFF writes, `psql`, e2e) keeps
pgstac's inline statistics.

## Consequences

- **Invariant:** the writer's connections carry `use_queue` ON; the drain
  connection carries `update_collection_extent` ON **and** `use_queue`
  explicitly FALSE. Any future pool, hook or "cleanup" that unifies the two
  pairings reintroduces one of the two silent failures above. Both call
  sites comment this and point here.
- Collection extents are refreshed by the drain tick and by nothing else —
  at most one tick stale, indefinitely stale if the drainer stops, which is
  why the stale-queue WARNING (`pipeline_pgstac_query_queue_oldest_age_seconds`)
  matters twice.
- Any test or tool that creates and drops a pgstac collection must clear
  that partition's `query_queue` / `query_queue_history` rows before
  `delete_collection` (the writer queued them; the drain would error on the
  dropped partition). `tests/test_integration_pgstac_queue.py`,
  `tests/test_integration_itemize.py` and `pipeline.loadgen teardown`
  (M3-B0) do.
- Proven, not argued: `tests/test_integration_pgstac_queue.py` (DB-gated)
  asserts the extent is still the world bbox before the drain and the
  item's bbox after.

## Revisit

If a pgstac release moves the `update_collection_extent` check out of
`update_partition_stats` (so it is read in the writer's session), or makes
`run_or_queue` non-reentrant, the drainer's explicit-FALSE stops being
load-bearing. Re-test with the DB-gated queue test on any pgstac pin bump
(ADR 0006 already requires the upsert path be re-tested).
```

- [ ] **Step 2: Register it in the index**

In `docs/decisions/README.md`:

1. Append to the index table, after the 0019 row:
```markdown
| [0020](0020-pgstac-session-guc-pairings.md) | pgstac session GUC pairings: the writer and the drainer carry opposite settings | accepted (2026-09-09; records M3-A as shipped 2026-09-08) | M3 (M3-A) |
```
2. Append to "Key invariants these establish", after the **0019** bullet:
```markdown
- **0020** — The pgstac bulk-write settings are SESSION GUCs the pipeline sets on its own connections, never a `pgstac_settings` row or a deploy step. The **writer's** pool carries `pgstac.use_queue` ON; the **drainer's** autocommit connection carries `pgstac.update_collection_extent` ON **and** `pgstac.use_queue` explicitly FALSE (not unset — `get_setting` falls through to the table). The pairings are opposite by design: the extent refresh runs in the draining session and re-enters `run_or_queue`. Anything that creates and drops a pgstac collection clears that partition's `query_queue` rows first.
```
3. Change `2. Number sequentially (next: \`0020\`).` to `2. Number sequentially (next: \`0021\`).`

- [ ] **Step 3: Point the two code sites and FEATURES at it**

`services/pipeline/src/pipeline/db/pgstac_session.py` — in the module docstring, after the sentence ending "…an uncommitted one on the connection's first return." append a new paragraph:
```
The drainer carries the OPPOSITE pairing (`pipeline/stac/query_queue.py`);
ADR 0020 (`docs/decisions/0020-pgstac-session-guc-pairings.md`) records why
the two must never be unified.
```
and in the `PGSTAC_SESSION_SQL` comment block, change the final `#: it would re-queue the extent refresh instead of running it).` line to `#: it would re-queue the extent refresh instead of running it). ADR 0020.`

`services/pipeline/src/pipeline/stac/query_queue.py` — in the module docstring, after "…deferring it one hop further on every drain, forever." append:
```
ADR 0020 (docs/decisions/0020-pgstac-session-guc-pairings.md) is the record.
```

`docs/FEATURES.md` — in the `| M3-A · pgstac write path |` row, before the final `Live numbers: \`TODO.md\` follow-ups. |`, insert `ADR 0020 records the two pairings as an invariant. `

- [ ] **Step 4: Gates and commit**

Run from `services/pipeline/`: `uv run pytest -q && uv run ruff check .` (the comment edits must not break the module docstrings — ruff checks line length).
Expected: green.

```bash
git add docs/decisions/0020-pgstac-session-guc-pairings.md docs/decisions/README.md \
        services/pipeline/src/pipeline/db/pgstac_session.py \
        services/pipeline/src/pipeline/stac/query_queue.py docs/FEATURES.md
git commit -m "docs(adr): ADR 0020 — pgstac session GUC pairings are opposite on the writer and the drainer"
```

---

### Task 3: Record, merge (lead only)

**Files:** `TODO.md` (queue table M3 row, a "M3-B0 landed" follow-up), `docs/FEATURES.md` (loadgen line, if any, mentions teardown is queue-aware).

- [ ] **Step 1:** From the worktree root `npm run verify`; from `services/pipeline/` pytest + ruff. All green.
- [ ] **Step 2:** `git checkout ai/main && git merge ai/m3-b0-harness-hygiene --no-ff`, re-run the gates on `ai/main`.
- [ ] **Step 3:** `docker compose build pipeline && docker compose up -d pipeline`; canary check on the standing demo.
- [ ] **Step 4:** Append the landed note to `TODO.md` follow-ups; remove the worktree and branch.

## Self-review

**Spec coverage.** Orchestrator brief M3-B0 (1) queue-aware teardown, tested → Task 1 (6 unit tests on a recording cursor; the DB-gated integration tests already exercise the identical SQL shape). (2) ADR 0020 with the writer/drainer pairing and the two load-bearing reasons, sources named, registered in the README index, code comments and FEATURES pointed at it → Task 2. ✓

**Placeholder scan.** Every step carries its code or its exact text. The one "first draft shown for intent" in Task 1 Step 1 is explicitly superseded by the simplified version in the same step.

**Type consistency.** `drop_probe_collection(cur, collection_id) -> int` is what `cmd_teardown` calls and what the tests assert; `_FakeCursor` implements `execute` / `fetchone` / `rowcount`, the `_Cursor` protocol's three members. `partition_queue_pattern` returns the same `%'…'%` string the two integration tests build inline.
