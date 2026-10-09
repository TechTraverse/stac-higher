# Z-6 · `cube_maintain`: expiry, GC, ledger prune and size readout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #92 (queue Z, epic #83) · **Branch:** `feat/z6-cube-maintain` (worktree `.claude/worktrees/z6-cube-maintain`, off `main` at 12c2cc4)

**Goal:** An hourly maintenance job per cube sink. It prunes old ledger rows, trims a stopped source's stale window, expires and garbage-collects Icechunk snapshots on windowed sinks, republishes the cube asset, and records per-kind repository sizes against a warning threshold.

**Architecture:** The cron job `pipeline.cube_maintain` (`23 * * * *`) fans out one `pipeline.cube_maintain_sink {cube_sink_id}` per enabled sink. Each job runs under the append's Procrastinate `lock = cube:{id}`, so it never runs alongside that sink's writer. The async orchestration lives in `cubes/maintain.py::run_cube_maintain`. It does the DB work through `CubeRepo` and runs two blocking steps in `asyncio.to_thread`: one repository pass (`maintain_repository`: trim → expire → GC → read the tip) and one prefix listing. It writes a JSON summary to `cube_sinks.last_maintenance`. Everything new hangs off seams Z-4 already built: `CubeRepo` (+ `FakeCubeRepo`), `icerepo.cube_storage`, `steps.trim_count`, `write.py`'s trim, and the `AfterBatch` hook that Z-5 fills.

**Tech Stack:** Python 3.12, icechunk 2.3.0 (`expire_snapshots`, `garbage_collect` → `GCSummary`), zarr 3, xarray, boto3 (`list_objects_v2` paginator), Procrastinate (via `QueueBackend`), psycopg 3, pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §10, plus §4.2 (ledger pruning) and §14.4 (the lock decision). Background: ADR 0022 (`docs/decisions/0022-virtual-cube-sink.md`) and I-143 in `docs/ISSUES.md`.

**Pre-validated:** the code in this plan was written and run on this branch before the plan was committed, then removed so the plan executes from a clean branch. Results: the full pipeline suite passed (`2038 passed, 46 skipped`), `ruff check .` was clean, and the 4 new DB-gated repo tests passed against a throwaway Postgres holding migration 032's tables. Icechunk behaviour the plan relies on, measured on 2.3.0:
- A repository opened **without** virtual-chunk credentials can still `shift_array`, `resize`, commit, `expire_snapshots` and `garbage_collect`.
- `expire_snapshots` never expires the root snapshot or the `main` tip. GC then deletes the expired snapshots and manifests, and also the snapshots a `reset_to_root` orphaned.
- GC reports `transaction_logs_deleted: 0` and never removes anything under `overwritten/` (I-143).
- The on-disk layout is `repo` (a top-level object), `snapshots/`, `manifests/`, `transactions/` and `overwritten/`, plus `chunks/` once a native chunk is too big to inline.
- A zero-step trim (`shift_array` by 0, then `resize` to the same shape) still produces a commit. The plan guards `k > 0`.

## Global Constraints

- Cron `pipeline.cube_maintain` = **`23 * * * *`**. Per-sink job `pipeline.cube_maintain_sink {cube_sink_id}` with **`lock = cube:{id}`** and **no `queueing_lock`** (spec §10).
- **GC only under `assets/{c}/_cube/`, and only on sinks with a `window`** (the ADR 0022 invariant in `docs/decisions/README.md`). A sink without a window is never trimmed, expired or garbage-collected.
- `expire_snapshots(older_than = now − retention)`, then `garbage_collect(same cutoff)`. Retention defaults to **1 h** (`3600` s).
- Prune **terminal** ledger rows older than **7 days** (spec §4.2). `pending` rows are never pruned.
- Per-kind readout names, verbatim: **`transactions`, `overwritten`, `manifests`, `snapshots`, `chunks`** (spec §10). Anything else counts as `other`.
- WARNING plus status "attention" above **`CUBE_REPO_WARN_BYTES`**, default **1 GiB** (`1073741824`).
- Every open of a cube repository uses `num_updates_per_repo_info_file = 100` (I-143); `icerepo._config()` sets it.
- **No platform-side deletion of Icechunk internals and no periodic rebuild** (I-143, spec §10).
- The pipeline **never writes `cube_sinks.updated_at`**. That column is the app's optimistic-lock version (#98).
- Maintenance **never creates** a repository and never saves its config. Only the writer does either.
- Gates: `uv run pytest` + `uv run ruff check .` in `services/pipeline/`, and `npm run verify` at the repo root. This slice needs no e2e, dev server, Docker or live stack, and has no lead-only steps.

## Decisions taken in this plan (stand unless the lead overturns them)

1. **Fan out to every enabled sink, not only windowed ones.** Spec §10 says "per enabled sink that has a window". This plan keeps the ADR's actual rule instead: GC only with a window. Trim, expiry and GC are gated on `config.window` inside the job; ledger pruning, republishing and the size readout run for every enabled sink. Gating the fan-out itself would break two things:
   - A windowless sink's ledger (~288 rows a day) would never be pruned, contradicting §4.2's unconditional "terminal rows older than 7 days are pruned".
   - Its repository is the one most likely to grow past the warning, and nothing would measure it.

   Disabled sinks are not maintained at all.
2. **Env var `CUBE_SNAPSHOT_RETENTION_SECONDS`**, not the spec's `CUBE_SNAPSHOT_RETENTION`, because every pipeline duration is named `*_SECONDS` / `*_DAYS` (`config.py`). Floor **300 s**, so a mistyped `0` cannot expire a snapshot a reader opened seconds ago. `CUBE_REPO_WARN_BYTES` keeps the spec's name, with a floor of 1.
3. **"Attention" lives in `last_maintenance`**: `status: "ok" | "attention" | "failed"` plus `attention: [reasons]`. No new column, no migration. Z-8's sink card reads it, and the shape is documented in `docs/monitoring.md`. The threshold is **inclusive** (`total_bytes >= warn_bytes`), because the issue's acceptance criterion says "the warning fires at the threshold".
4. **GC delete failures are a second attention reason** (`gc_delete_failures`, when `GCSummary.objects_failed_to_delete > 0`). They log a WARNING; the run still succeeds.
5. **Age trim conditions.** The trim runs only when all of these hold:
   - the sink has a window;
   - it has a recorded snapshot (it is not provisional, #98);
   - it has **no pending** ledger row (the next append trims anyway);
   - the branch tip **equals** `last_snapshot_id`. If the tip is ahead, an append committed and died before recording, and that tip is the next append's to record.

   `trim_count` is reused as-is, so `max_steps` also applies (a window shrunk by a config change). The trim commit is recorded with a compare-and-swap, `record_snapshot(..., from_snapshot_id=)`. It never touches `last_appended_at`, `last_error` or `updated_at`.
6. **Republish on every run** (agreed with the Z-5 session). After the repository pass, if the cube is initialised and the recorded snapshot equals the tip, the job calls `after_batch(sink, config, BatchResult(outcomes={}, snapshot_id=tip, committed=trimmed>0, values=tip_values, trimmed=k, initialised=True))`. Z-5's writer publishes only the **recorded** tip, so the trim is recorded **before** the call. This publishes a trim, and also heals a publish that a crashed append missed. Z-5's writer short-circuits an unchanged document.

   **The republish must always carry the grid** (`statics`, `spatial_dims`), never just the time values. This comes from peer review of the Z-5 plan on 2026-10-09, verified on main. `formToStacCollection` (`app/src/components/collections/CollectionForm.tsx:57`) rebuilds the document from the form fields only, so any form save drops `cube:dimensions`. For a source that has stopped, this republish is the only one left. If it sends no grid, Z-5's fallback keeps the (now missing) x/y entries, and x/y never come back. So `RepoPass` carries the tip's whole `CubeState`, and whoever wires `after_batch` into maintenance passes `statics=rp.state.statics, spatial_dims=rp.state.spatial_dims`.
7. **Failure semantics.**
   - The per-sink job has **no retry**; the next hour retries.
   - Any error records `last_maintenance` with `status: "failed"` and `error`, keeps `last_maintained_at` at the **last success**, and re-raises so Procrastinate shows the failure.
   - The ledger prune runs **first**, so a store outage does not stop DB hygiene.
   - An invalid sink config fails the run after the prune. The append records `last_error` for it too.
8. **`cube_kick` recovers stalled maintenance jobs too** (`retry_stalled(JOB_CUBE_MAINTAIN_SINK)`). A maintenance job holds the same `cube:{id}` lock as the append, so a SIGKILLed one would otherwise wedge the sink's appends forever (the Z-3 lesson).
9. **Maintenance opens the repository read-config-only.** `icerepo.open_existing(storage)` returns `None` when the repository is absent. Otherwise it opens with `_config()` and without virtual-chunk credentials, and never saves config.
10. **Expiry spares every snapshot that was the tip within the retention** (added during execution, from Task 3's review). Icechunk ages a snapshot by when it was **written**, but a reader holds the snapshot that was the **tip** when it last looked. A trim commit, or an append after a quiet spell, would otherwise let the same pass expire the snapshot readers were on: the review reproduced `StorageError: object not found`. `maintain.expiry_cutoff` therefore lowers the cutoff to the write time of the newest ancestor written at or before `now − retention`. Provisional repositories keep the plain cutoff: nothing published them, and their reset moved `main` backwards. Pinned by `test_a_trim_keeps_the_snapshot_readers_were_on`. The cost is one extra snapshot and its manifests kept per sink.
11. **Files touched beyond the issue's list** (all small seams):
    - `storage/platform.py`: `list_sizes`, next to `list_objects`.
    - `cubes/icerepo.py`: `open_existing`.
    - `cubes/write.py`: rename `_trim` → `trim_steps`, now shared.
    - `cubes/repo.py` + `tests/_cube_fake.py`: four repo methods.
    - `services/pipeline/README.md`: env vars + jobs.
    - `docs/FEATURES.md`: the Z-6 row.

## Coordination with Z-5 (#91, planned in parallel by another session)

Agreed with the Z-5 planning session on 2026-10-09:
- Z-5 keeps `AfterBatch = Callable[[CubeSink, CubeSinkConfig, BatchResult], Awaitable[None]]` (in `cubes/append.py`) unchanged. Its writer reads `sink.id`, `sink.cube_collection_id`, `config.asset_key`, `config.append_dim`, `result.snapshot_id` and `result.values`, and **never** `outcomes`, `committed` or `trimmed`.
- Z-5 exposes `pipeline.cubes.collection.production_after_batch(settings: Settings) -> AfterBatch`.
- Z-5 adds two **defaulted** `BatchResult` fields, `statics` and `spatial_dims`, plus `CubeState.spatial_dims`. If they are left empty, its writer keeps the document's existing x/y `cube:dimensions`. That fallback is **not** safe for maintenance, because a form save drops `cube:dimensions` (Decision 6). Maintenance must therefore pass both fields whenever `after_batch` is wired.
- Z-5's writer publishes only when `cube_sinks.last_snapshot_id == result.snapshot_id`, read under the collection row lock. Otherwise the call is a logged no-op (`superseded`). Decision 6 satisfies that.
- An empty `values` (a cube trimmed to 0 steps) is valid. The writer publishes `time_values: []` with null temporal extents.
- **Wiring: whichever slice merges second wires the other job, and the wiring is always these three edits together:**
  1. In `production_maintain_deps` (Task 4), add `after_batch=production_after_batch(settings)`, and in `test_production_maintain_deps_wire_the_real_seams` change `deps.after_batch is None` to `deps.after_batch is not None`.
  2. In `_maintain`'s `BatchResult(...)` (Task 3), add `statics=rp.state.statics, spatial_dims=rp.state.spatial_dims`. `RepoPass.state` is already the tip's full `CubeState`, so nothing else needs threading.
  3. In `test_the_recorded_tip_is_republished_on_every_run` (Task 3), add:
     ```python
     assert set(result.statics) == {"x", "y", "goes_imager_projection"}
     assert result.spatial_dims == ("y", "x")
     ```
  - **If Z-5 is already on `main` when you reach Task 4**, make all three edits in this PR.
  - **Otherwise**, leave `after_batch` unset (`None`). Production then publishes nothing from maintenance, so no time-only publish can happen. Say so in the PR body: Z-5's PR makes the three edits. Wiring the hook without edit 2 is a bug.

## Review Focus

These are the failure modes the spec implies that are most likely to bite a person. Each has a pinning test in the owning task:
1. **A source that stops publishing.** The window must still shrink hourly, and the collection must stop advertising aged-out steps, down to an empty cube. The republish must carry the grid, because a form save in between drops `cube:dimensions`. Task 3: `test_an_age_trim_commits_once_and_records_the_snapshot`, `test_a_trim_is_published_after_it_is_recorded`, `test_a_cube_trimmed_to_empty_is_published_with_no_values`, `test_the_repository_pass_carries_the_grid_for_the_writer`.
2. **A worker killed during maintenance.** It holds the same lock, so it must not wedge the sink's appends. Task 4: `test_kick_recovers_a_stranded_maintenance_job`.
3. **A sink without a window.** It must never lose a snapshot, yet still be measured and pruned. Task 3: `test_a_sink_without_a_window_is_never_expired_or_garbage_collected`, `test_terminal_ledger_rows_older_than_seven_days_are_pruned`.
4. **A repository the writer has not recorded** (provisional, or a crash between commit and record). It must not be trimmed or published over, while GC still collects the snapshots a provisional reset orphaned. Task 3: `test_no_age_trim_on_a_provisional_repository`, `test_no_age_trim_when_the_tip_is_not_the_recorded_snapshot`, `test_gc_collects_the_snapshots_a_provisional_reset_orphaned`.
5. **A platform-store or DB outage mid-run.** It must read as `failed` without erasing the last success time, and the next hour retries. Task 3: `test_a_failure_is_recorded_and_raised`. One more trap: the lister must use `…/_cube/` with the trailing slash, so a sibling `_cube…` prefix is never counted. Task 3: `test_the_platform_lister_lists_only_the_cube_prefix`.

---

## Before Task 1

The worktree already exists: `.claude/worktrees/z6-cube-maintain`, branch `feat/z6-cube-maintain`, with this plan as its only commit. Run once, from `services/pipeline/`:

```bash
uv sync --extra dev
uv run pytest -q -p no:warnings tests/test_cube_jobs.py   # baseline: green
```

Every `uv run …` below runs from `services/pipeline/`.

---

### Task 1: Settings and the platform size listing

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py`
- Modify: `services/pipeline/src/pipeline/storage/platform.py`
- Test: `services/pipeline/tests/test_config.py` (append), `services/pipeline/tests/test_platform_list_sizes.py` (create)

**Interfaces:**
- Produces:
  - `pipeline.config` constants: `DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS = 3600`, `MIN_CUBE_SNAPSHOT_RETENTION_SECONDS = 300`, `DEFAULT_CUBE_REPO_WARN_BYTES = 1024**3`.
  - `Settings.cube_snapshot_retention_seconds: int` and `Settings.cube_repo_warn_bytes: int`.
  - `pipeline.storage.platform.list_sizes(client: S3Like, bucket: str, prefix: str) -> list[tuple[str, int]]`, returning full keys with their sizes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (it already imports `pytest` and `Settings`):

```python


def test_cube_maintenance_defaults_and_overrides():
    from pipeline.config import (
        DEFAULT_CUBE_REPO_WARN_BYTES,
        DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS,
    )

    settings = Settings.from_env(env={})
    assert settings.cube_snapshot_retention_seconds == DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS
    assert DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS == 3600
    assert settings.cube_repo_warn_bytes == DEFAULT_CUBE_REPO_WARN_BYTES == 1024**3
    custom = Settings.from_env(
        env={"CUBE_SNAPSHOT_RETENTION_SECONDS": "7200", "CUBE_REPO_WARN_BYTES": "1000"}
    )
    assert (custom.cube_snapshot_retention_seconds, custom.cube_repo_warn_bytes) == (7200, 1000)
    blank = Settings.from_env(
        env={"CUBE_SNAPSHOT_RETENTION_SECONDS": "", "CUBE_REPO_WARN_BYTES": ""}
    )
    assert blank.cube_snapshot_retention_seconds == 3600


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CUBE_SNAPSHOT_RETENTION_SECONDS", "0"),
        ("CUBE_SNAPSHOT_RETENTION_SECONDS", "299"),
        ("CUBE_REPO_WARN_BYTES", "0"),
    ],
)
def test_cube_maintenance_settings_refuse_dangerous_values(name, value):
    with pytest.raises(ValueError, match=name):
        Settings.from_env(env={name: value})
```

Create `tests/test_platform_list_sizes.py`:

```python
"""list_sizes: every (key, Size) under a prefix, across pages (Z-6 size readout)."""

from pipeline.storage.platform import list_sizes


def test_list_sizes_reads_every_page():
    class Paginator:
        def paginate(self, Bucket, Prefix):
            assert (Bucket, Prefix) == ("b", "assets/c/_cube/")
            yield {"Contents": [{"Key": "assets/c/_cube/repo", "Size": 3}]}
            yield {}
            yield {"Contents": [{"Key": "assets/c/_cube/snapshots/S", "Size": 4}]}

    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

    assert list_sizes(Client(), "b", "assets/c/_cube/") == [
        ("assets/c/_cube/repo", 3),
        ("assets/c/_cube/snapshots/S", 4),
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_config.py tests/test_platform_list_sizes.py`
Expected: FAIL. `ImportError` on `DEFAULT_CUBE_REPO_WARN_BYTES`, and `list_sizes` cannot be imported.

- [ ] **Step 3: Implement**

In `src/pipeline/config.py`, make four edits.

(a) In the module docstring's env list, insert after the `PGSTAC_QUEUE_HISTORY_DAYS` entry (before `PROCESS_HARDWARE_PROFILES_FILE`):

```python
- ``CUBE_SNAPSHOT_RETENTION_SECONDS`` — ``pipeline.cube_maintain_sink`` expires
  cube snapshots older than this, then garbage-collects with the same cutoff
  (virtual cube spec §10; at least 300).
- ``CUBE_REPO_WARN_BYTES`` — a cube repository at or above this size logs a
  WARNING and its sink's ``last_maintenance.status`` reads ``attention`` (I-143).
```

(b) After `DEFAULT_PGSTAC_QUEUE_HISTORY_DAYS = 7`:

```python

# Virtual cube maintenance (Z-6, virtual cube spec §10, I-143). Expiry keeps an
# hour of snapshots by default, so a reader that opened a recent snapshot is
# not cut off mid-animation; the floor keeps a mistyped 0 from deleting the
# snapshot a reader is on. ~8 MB/day of bookkeeping reaches 1 GiB in ~4 months.
DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS = 3600
MIN_CUBE_SNAPSHOT_RETENTION_SECONDS = 300
DEFAULT_CUBE_REPO_WARN_BYTES = 1024**3
```

(c) Before `def _optional(raw: str | None) -> str | None:`:

```python
def _parse_cube_retention(raw: str | None) -> int:
    value = int(raw) if raw not in (None, "") else DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS
    if value < MIN_CUBE_SNAPSHOT_RETENTION_SECONDS:
        raise ValueError(
            "CUBE_SNAPSHOT_RETENTION_SECONDS must be >= "
            f"{MIN_CUBE_SNAPSHOT_RETENTION_SECONDS}, got {value}"
        )
    return value


def _parse_cube_warn_bytes(raw: str | None) -> int:
    value = int(raw) if raw not in (None, "") else DEFAULT_CUBE_REPO_WARN_BYTES
    if value < 1:
        raise ValueError(f"CUBE_REPO_WARN_BYTES must be >= 1, got {value}")
    return value


```

(d) In `class Settings`, after the `pgstac_queue_history_days` field:

```python
    #: Virtual cube maintenance (Z-6) — see the DEFAULT_CUBE_* constants.
    cube_snapshot_retention_seconds: int = DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS
    cube_repo_warn_bytes: int = DEFAULT_CUBE_REPO_WARN_BYTES
```

and in `Settings.from_env`, after the `pgstac_queue_history_days=int(...)` argument:

```python
            cube_snapshot_retention_seconds=_parse_cube_retention(
                env.get("CUBE_SNAPSHOT_RETENTION_SECONDS")
            ),
            cube_repo_warn_bytes=_parse_cube_warn_bytes(env.get("CUBE_REPO_WARN_BYTES")),
```

In `src/pipeline/storage/platform.py`, insert after `list_objects` (before `delete_keys`):

```python
def list_sizes(client: S3Like, bucket: str, prefix: str) -> list[tuple[str, int]]:
    """Every ``(key, Size)`` under ``prefix``, paginated (Z-6: a cube
    repository's per-kind size readout). Pure over an injected client;
    synchronous boto3, so wrap it in ``asyncio.to_thread``."""
    found: list[tuple[str, int]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        found.extend((obj["Key"], int(obj["Size"])) for obj in page.get("Contents", []))
    return found
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q tests/test_config.py tests/test_platform_list_sizes.py && uv run ruff check .`
Expected: PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/src/pipeline/storage/platform.py services/pipeline/tests/test_config.py services/pipeline/tests/test_platform_list_sizes.py
git commit -m "Z-6: cube maintenance settings and the platform size listing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `CubeRepo` maintenance methods (Pg + fake)

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/repo.py`
- Modify: `services/pipeline/tests/_cube_fake.py`
- Test: `services/pipeline/tests/test_cube_repo_fake.py` (append), `services/pipeline/tests/test_integration_cubes_repo.py` (append; DB-gated)

**Interfaces:**
- Consumes: the existing `CubeRepo` ABC, `PgCubeRepo._connect` and `FakeCubeRepo._sink`.
- Produces (both implementations, these exact signatures):
  - `async maintainable_sinks(self) -> list[str]`: the ids of enabled sinks, sorted.
  - `async record_snapshot(self, cube_sink_id: str, *, snapshot_id: str, from_snapshot_id: str) -> bool`: a compare-and-swap on `last_snapshot_id`; it touches nothing else.
  - `async prune_ledger(self, cube_sink_id: str, older_than_days: int) -> int`: deletes non-`pending` rows whose `updated_at` is older than the cutoff.
  - `async record_maintenance(self, cube_sink_id: str, *, summary: Mapping[str, Any], maintained_at: dt.datetime | None) -> None`: sets `last_maintenance`; sets `last_maintained_at` only when given.
  - New fake fields: `FakeSink.last_maintained_at: dt.datetime | None`, `FakeSink.last_maintenance: dict[str, Any] | None`, `FakeLedgerRow.updated_at: dt.datetime`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cube_repo_fake.py` (it already imports `dt`, `FakeCubeRepo`, `FakeSink`, `LedgerEntry` and `T0`):

```python


async def test_maintenance_methods():
    repo = FakeCubeRepo(
        sinks=[
            FakeSink("s2", "src", "c2"),
            FakeSink("s1", "src", "c1", last_snapshot_id="S1"),
            FakeSink("off", "src", "c3", enabled=False),
        ]
    )
    assert await repo.maintainable_sinks() == ["s1", "s2"]
    assert not await repo.record_snapshot("s1", snapshot_id="T1", from_snapshot_id="S0")
    assert await repo.record_snapshot("s1", snapshot_id="T1", from_snapshot_id="S1")
    assert (await repo.load_sink("s1")).last_snapshot_id == "T1"
    await repo.record_appends([LedgerEntry("s1", "a", T0, status="skipped", reason="late")])
    repo.ledger[("s1", "a")].updated_at -= dt.timedelta(days=8)
    await repo.record_appends([LedgerEntry("s1", "b", T0)])
    repo.ledger[("s1", "b")].updated_at -= dt.timedelta(days=8)
    assert await repo.prune_ledger("s1", 7) == 1
    assert [r.item_id for r in repo.rows("s1")] == ["b"]
    await repo.record_maintenance("s1", summary={"status": "ok"}, maintained_at=T0)
    await repo.record_maintenance("s1", summary={"status": "failed"}, maintained_at=None)
    assert (repo.sinks[1].last_maintenance, repo.sinks[1].last_maintained_at) == (
        {"status": "failed"},
        T0,
    )
```

Append to `tests/test_integration_cubes_repo.py` (`_source`, the `db` fixture, `T0` and `DATABASE_URL` already exist in that file):

```python


async def test_maintainable_sinks_lists_enabled_sinks_only(db):
    from pipeline.cubes.repo import PgCubeRepo

    _conn, make_sink = db
    on = await make_sink(_source())
    off = await make_sink(_source(), enabled=False)
    found = await PgCubeRepo(DATABASE_URL).maintainable_sinks()
    assert on in found
    assert off not in found
    assert found == sorted(found)


async def test_record_snapshot_moves_only_from_the_expected_tip(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    before = (await repo.load_sink(sink)).version
    kw = {"appended_at": T0, "source_prefixes": ["s3://b/"]}
    await repo.record_commit(sink, snapshot_id="S1", first_commit_version=None, **kw)
    assert not await repo.record_snapshot(sink, snapshot_id="T1", from_snapshot_id="S0")
    assert await repo.record_snapshot(sink, snapshot_id="T1", from_snapshot_id="S1")
    after = await repo.load_sink(sink)
    assert after.last_snapshot_id == "T1"
    assert after.version == before  # never updated_at
    cur = await conn.execute(
        "SELECT last_appended_at FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    assert (await cur.fetchone())[0] == T0  # a trim is not an append


async def test_prune_ledger_deletes_old_terminal_rows_of_that_sink_only(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink, other = await make_sink(_source()), await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [
            LedgerEntry(sink, "old-done", T0, status="skipped", reason="late"),
            LedgerEntry(sink, "old-pending", T0),
            LedgerEntry(sink, "new-done", T0, status="skipped", reason="late"),
            LedgerEntry(other, "old-done", T0, status="skipped", reason="late"),
        ]
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET updated_at = now() - interval '8 days'"
        " WHERE item_id IN ('old-done', 'old-pending')"
        "   AND cube_sink_id = ANY(%s::uuid[])",
        ([sink, other],),
    )
    assert await repo.prune_ledger(sink, 7) == 1
    cur = await conn.execute(
        "SELECT cube_sink_id::text, item_id FROM stac_higher.cube_appends"
        " WHERE cube_sink_id = ANY(%s::uuid[]) ORDER BY 1, 2",
        ([sink, other],),
    )
    assert sorted(await cur.fetchall()) == sorted(
        [(sink, "new-done"), (sink, "old-pending"), (other, "old-done")]
    )


async def test_record_maintenance_keeps_the_last_success_on_failure(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    before = (await repo.load_sink(sink)).version
    ok = {"status": "ok", "total_bytes": 5}
    await repo.record_maintenance(sink, summary=ok, maintained_at=T0)
    await repo.record_maintenance(sink, summary={"status": "failed"}, maintained_at=None)
    cur = await conn.execute(
        "SELECT last_maintenance, last_maintained_at FROM stac_higher.cube_sinks WHERE id = %s",
        (sink,),
    )
    assert await cur.fetchone() == ({"status": "failed"}, T0)
    assert (await repo.load_sink(sink)).version == before
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_cube_repo_fake.py`
Expected: FAIL with `AttributeError: 'FakeCubeRepo' object has no attribute 'maintainable_sinks'`.

- [ ] **Step 3: Implement**

In `src/pipeline/cubes/repo.py`, make four edits.

(a) In the module docstring, replace

```
``last_snapshot_id``, ``last_appended_at`` and ``last_error`` (Z-4). It
never writes ``cube_sinks.updated_at``: that column is the app's
```

with

```
``last_snapshot_id``, ``last_appended_at`` and ``last_error`` (Z-4), and
``last_maintained_at`` / ``last_maintenance`` (Z-6). It never writes
``cube_sinks.updated_at``: that column is the app's
```

(b) Imports: replace `from collections.abc import Sequence` with

```python
import json
from collections.abc import Mapping, Sequence
```

(`import json` goes after `import datetime as dt`.)

(c) In `class CubeRepo(abc.ABC)`, after the abstract `record_error`:

```python

    @abc.abstractmethod
    async def maintainable_sinks(self) -> list[str]:
        """Ids of every enabled sink, sorted: the hourly maintenance fan-out."""

    @abc.abstractmethod
    async def record_snapshot(
        self, cube_sink_id: str, *, snapshot_id: str, from_snapshot_id: str
    ) -> bool:
        """Move ``last_snapshot_id`` to a maintenance commit, only while it is
        still ``from_snapshot_id``. Leaves ``last_appended_at``, ``last_error``
        and ``updated_at`` alone. Returns whether the row changed."""

    @abc.abstractmethod
    async def prune_ledger(self, cube_sink_id: str, older_than_days: int) -> int:
        """Delete the sink's terminal ledger rows last updated more than
        ``older_than_days`` ago; ``pending`` rows are never pruned. Returns the
        rows deleted."""

    @abc.abstractmethod
    async def record_maintenance(
        self,
        cube_sink_id: str,
        *,
        summary: Mapping[str, Any],
        maintained_at: dt.datetime | None,
    ) -> None:
        """Set ``last_maintenance`` to ``summary``, and ``last_maintained_at``
        when given (a failed run passes ``None`` and keeps the last success)."""
```

(d) At the end of `class PgCubeRepo` (after `record_error`):

```python

    async def maintainable_sinks(self) -> list[str]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text FROM stac_higher.cube_sinks WHERE enabled ORDER BY 1"
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def record_snapshot(  # pragma: no cover
        self, cube_sink_id: str, *, snapshot_id: str, from_snapshot_id: str
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_sinks SET last_snapshot_id = %s"
                " WHERE id = %s AND last_snapshot_id = %s",
                (snapshot_id, cube_sink_id, from_snapshot_id),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed > 0

    async def prune_ledger(  # pragma: no cover
        self, cube_sink_id: str, older_than_days: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "DELETE FROM stac_higher.cube_appends"
                " WHERE cube_sink_id = %s AND status <> 'pending'"
                "   AND updated_at < now() - make_interval(days => %s)",
                (cube_sink_id, older_than_days),
            )
            deleted = cur.rowcount
            await conn.commit()
        return deleted

    async def record_maintenance(  # pragma: no cover
        self,
        cube_sink_id: str,
        *,
        summary: Mapping[str, Any],
        maintained_at: dt.datetime | None,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.cube_sinks"
                "   SET last_maintenance = %s::jsonb,"
                "       last_maintained_at = COALESCE(%s, last_maintained_at)"
                " WHERE id = %s",
                (json.dumps(summary), maintained_at, cube_sink_id),
            )
            await conn.commit()
```

In `tests/_cube_fake.py`, make five edits.

(a) Replace `from collections.abc import Callable, Sequence` with `from collections.abc import Callable, Mapping, Sequence`.

(b) In `FakeSink`, after `last_appended_at`:

```python
    last_maintained_at: dt.datetime | None = None
    last_maintenance: dict[str, Any] | None = None
```

(c) In `FakeLedgerRow`, after `snapshot_id`:

```python
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
```

(d) In `FakeCubeRepo.finish_rows`, mirror the SQL's `updated_at = now()`:

```python
            row.status, row.reason, row.snapshot_id = o.status, o.reason, o.snapshot_id
            row.updated_at = dt.datetime.now(dt.UTC)
            changed += 1
```

(e) In `FakeCubeRepo`, after `record_error` (before the `backdate` test helper):

```python
    async def maintainable_sinks(self) -> list[str]:
        return sorted(s.id for s in self.sinks if s.enabled)

    async def record_snapshot(
        self, cube_sink_id: str, *, snapshot_id: str, from_snapshot_id: str
    ) -> bool:
        s = self._sink(cube_sink_id)
        if s is None or s.last_snapshot_id != from_snapshot_id:
            return False
        s.last_snapshot_id = snapshot_id
        return True

    async def prune_ledger(self, cube_sink_id: str, older_than_days: int) -> int:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=older_than_days)
        doomed = [
            key
            for key, r in self.ledger.items()
            if r.cube_sink_id == cube_sink_id and r.status != "pending" and r.updated_at < cutoff
        ]
        for key in doomed:
            del self.ledger[key]
        return len(doomed)

    async def record_maintenance(
        self,
        cube_sink_id: str,
        *,
        summary: Mapping[str, Any],
        maintained_at: dt.datetime | None,
    ) -> None:
        s = self._sink(cube_sink_id)
        if s is not None:
            s.last_maintenance = dict(summary)
            if maintained_at is not None:
                s.last_maintained_at = maintained_at
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q tests/test_cube_repo_fake.py tests/test_cube_append.py tests/test_cube_jobs.py tests/test_dispatch_cubes.py && uv run ruff check .`
Expected: PASS. The Z-4 suites still pass with the extended fake.

The DB-gated tests are optional for the implementer; the PR records whether they ran. To run them, use a throwaway Postgres over **TCP**, because the scratchpad's unix-socket path exceeds 103 bytes. Migration 032's two tables are the SQL between `name: "032_cube_sinks"` and its closing backtick in `app/src/lib/db/migrate.ts`:

```bash
SP=<your scratchpad>; initdb -D $SP/pg -U postgres -A trust >/dev/null
pg_ctl -D $SP/pg -o "-p 5499 -c listen_addresses=localhost -k ''" -l $SP/pg.log start
( echo "CREATE SCHEMA stac_higher;"; sed -n '/name: "032_cube_sinks"/,/^    `,$/p' ../../app/src/lib/db/migrate.ts | sed '1,2d;$d' ) | psql -h localhost -p 5499 -U postgres -q
DATABASE_URL=postgresql://postgres@localhost:5499/postgres uv run pytest -q tests/test_integration_cubes_repo.py
pg_ctl -D $SP/pg stop -m fast
```

Expected: `14 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/cubes/repo.py services/pipeline/tests/_cube_fake.py services/pipeline/tests/test_cube_repo_fake.py services/pipeline/tests/test_integration_cubes_repo.py
git commit -m "Z-6: CubeRepo maintenance methods: fan-out, trim record, ledger prune, summary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `cubes/maintain.py`: the repository pass and the run

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/icerepo.py` (add `open_existing` before `class CubeState`)
- Modify: `services/pipeline/src/pipeline/cubes/write.py` (rename `_trim` → `trim_steps`, both its definition and its one call site in `_attempt`)
- Create: `services/pipeline/src/pipeline/cubes/maintain.py`
- Test: `services/pipeline/tests/test_cube_maintain.py` (create)

**Interfaces:**
- Consumes:
  - From Task 2: the `CubeRepo` methods `load_sink`, `has_pending`, `prune_ledger`, `record_snapshot` and `record_maintenance`.
  - From Task 1: `list_sizes` (plus the existing `build_platform_client`) and the `DEFAULT_CUBE_*` constants.
  - From Z-4: `icerepo.BRANCH`, `cube_prefix`, `read_state`, `_config`; `steps.trim_count`; `write.BatchResult`, `error_text`, `MAX_ERROR_CHARS`; `append.AfterBatch`; `config.parse_cube_sink_config`.
- Produces:
  - `icerepo.open_existing(storage: ic.Storage) -> ic.Repository | None`
  - `write.trim_steps(session: ic.Session, state: CubeState, k: int) -> None` (formerly `_trim`)
  - `maintain.MaintainDeps(repo, storage_for, list_objects, retention_seconds=3600, warn_bytes=1024**3, after_batch=None, now=_utcnow)` (a dataclass)
  - `maintain.ListObjects = Callable[[CubeSink], list[tuple[str, int]]]`, with keys relative to the repository prefix
  - `maintain.run_cube_maintain(cube_sink_id: str, deps: MaintainDeps) -> dict[str, Any] | None`
  - `maintain.maintain_repository(storage, config, now, *, retention_seconds, recorded_snapshot_id, may_trim) -> RepoPass`. `RepoPass.state: CubeState | None` is the tip's full state (time `values` plus the grid `statics`, and `spatial_dims` once Z-5 adds it); it is `None` until the cube's first step.
  - `maintain.platform_lister(settings: Settings) -> ListObjects`
  - `maintain.size_by_kind(objects) -> dict[str, dict[str, int]]`
  - Constants: `KINDS`, `LEDGER_RETENTION_DAYS = 7`, `STATUS_OK`/`STATUS_ATTENTION`/`STATUS_FAILED`, `ATTENTION_REPO_SIZE = "repo_size"`, `ATTENTION_GC_DELETE_FAILURES = "gc_delete_failures"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cube_maintain.py`. It drives a **real** Icechunk cube on local disk (`ic.local_filesystem_storage`), built from the Z-4 GOES-shaped fixtures in `tests/_cube_sources.py`, with `FakeCubeRepo` as the sink row. Timing notes:
- Icechunk stamps snapshots with the wall clock, so the expiry tests run at `LATER` (now + 2 h).
- The trim tests run at fixed 2026-10-03 times, so expiry is a no-op there.
- A step exactly **at** the age cutoff stays, because `trim_count` uses `searchsorted(side="left")`.

```python
"""cube_maintain_sink: age trim, expiry + GC, ledger prune, size readout (spec §10, I-143)."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import icechunk as ic
import pytest
import xarray as xr

from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository, reset_to_root
from pipeline.cubes.maintain import (
    ATTENTION_REPO_SIZE,
    KINDS,
    LEDGER_RETENTION_DAYS,
    MaintainDeps,
    maintain_repository,
    run_cube_maintain,
    size_by_kind,
)
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import BatchResult, ParsedStep, write_batch

#: Icechunk stamps snapshots and objects with the wall clock, so expiry tests
#: run "two hours from now" to put every snapshot past the 1 h retention.
LATER = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)


class Cube:
    """A real Icechunk cube on local disk plus a fake sink row."""

    def __init__(self, tmp: Path, window: dict | None) -> None:
        self.src = local_libs(tmp / "src")
        self.dir = tmp / "repo"
        raw = {**GOES_CONFIG, **({"window": window} if window else {})}
        self.config = parse_cube_sink_config(raw)
        self.repo = FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube1", config=raw)])
        self.published: list[tuple[str, BatchResult]] = []
        self.listed = 0

    def storage(self) -> ic.Storage:
        return ic.local_filesystem_storage(str(self.dir))

    def append(self, *ns: int, now: dt.datetime | None = None) -> str:
        """Append scans ``ns`` in one commit and record it on the sink."""
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        parsed = []
        for n in ns:
            name = f"f{n}.nc"
            write_goes_file(self.src.prefix.removeprefix("file://") + name, when=scan(n))
            step = parse_header(self.src.url(name), registry_for(self.src), self.config)
            parsed.append(ParsedStep(n, f"i{n}", step, step_value(step, "t"), SOURCE_LAST_MODIFIED))
        result = write_batch(repo, parsed, self.config, now or scan(100))
        self.sink.last_snapshot_id = result.snapshot_id
        return result.snapshot_id

    @property
    def sink(self) -> FakeSink:
        return self.repo.sinks[0]

    def tip(self) -> str:
        return ic.Repository.open(self.storage()).lookup_branch(BRANCH)

    def commits(self) -> int:
        return len(list(ic.Repository.open(self.storage()).ancestry(branch=BRANCH))) - 1

    def snapshot_files(self) -> int:
        return len(list((self.dir / "snapshots").iterdir()))

    def times(self) -> list:
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return list(ds["t"].values)

    def pixels(self) -> list[float]:
        repo = open_repository(self.storage(), [self.src], replace_containers=False)
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return [float(v) for v in ds["CMI"].isel(x=0, y=0).values]

    def list_objects(self, sink) -> list[tuple[str, int]]:
        self.listed += 1
        return [
            (p.relative_to(self.dir).as_posix(), p.stat().st_size)
            for p in sorted(self.dir.rglob("*"))
            if p.is_file()
        ]

    async def publish(self, sink, config, result: BatchResult) -> None:
        self.published.append((sink.id, result))

    def deps(self, *, now: dt.datetime = LATER, warn_bytes: int = 1024**3) -> MaintainDeps:
        return MaintainDeps(
            repo=self.repo,
            storage_for=lambda sink: self.storage(),
            list_objects=self.list_objects,
            retention_seconds=3600,
            warn_bytes=warn_bytes,
            after_batch=self.publish,
            now=lambda: now,
        )

    async def run(self, **kw) -> dict | None:
        return await run_cube_maintain("s1", self.deps(**kw))


def test_size_by_kind_buckets_by_top_level_directory():
    sizes = size_by_kind(
        [
            ("transactions/A", 10),
            ("transactions/B", 5),
            ("overwritten/X", 7),
            ("manifests/M", 3),
            ("snapshots/S", 2),
            ("chunks/C", 1),
            ("repo", 100),
            ("config.yaml", 4),
            ("transactions", 9),  # a top-level object named like a kind is "other"
        ]
    )
    assert sizes["transactions"] == {"objects": 2, "bytes": 15}
    assert sizes["overwritten"] == {"objects": 1, "bytes": 7}
    assert sizes["other"] == {"objects": 3, "bytes": 113}
    assert set(sizes) == {*KINDS, "other"}


async def test_a_sink_without_a_window_is_never_expired_or_garbage_collected(tmp_path):
    cube = Cube(tmp_path, window=None)
    for n in range(4):
        cube.append(n)
    before = cube.snapshot_files()

    summary = await cube.run()

    assert cube.snapshot_files() == before
    assert summary["window"] is False
    assert (summary["expired_snapshots"], summary["gc"], summary["trimmed"]) == (None, None, 0)
    assert summary["sizes"]["snapshots"]["objects"] == before
    assert summary["status"] == "ok"
    assert cube.sink.last_maintenance == summary
    assert cube.sink.last_maintained_at == LATER


async def test_a_windowed_sink_expires_and_collects_old_snapshots(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})
    for n in range(5):
        cube.append(n)
    before = cube.snapshot_files()

    summary = await cube.run()

    assert summary["expired_snapshots"] >= 4
    assert summary["gc"]["snapshots_deleted"] == before - 2  # the root and the tip stay
    assert cube.snapshot_files() == 2
    assert set(summary["durations_ms"]) >= {"expire", "gc", "list"}
    # The cube is still whole: its window reads, through the source.
    assert cube.times() == [as_ns(scan(n)) for n in (2, 3, 4)]
    assert cube.pixels() == [0.0, 0.0, 0.0]


async def test_gc_collects_the_snapshots_a_provisional_reset_orphaned(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})
    cube.append(0)
    cube.append(1)
    repo = open_repository(cube.storage(), [cube.src], replace_containers=False)
    reset_to_root(repo, from_snapshot_id=repo.lookup_branch(BRANCH))
    cube.sink.last_snapshot_id = None  # provisional, as Z-4 leaves it

    summary = await cube.run()

    assert summary["gc"]["snapshots_deleted"] == 2
    assert cube.snapshot_files() == 1  # the root
    assert cube.published == []  # nothing recorded, nothing to publish


async def test_an_age_trim_commits_once_and_records_the_snapshot(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, 4, 5, now=scan(5))
    before = cube.commits()
    now = scan(5) + dt.timedelta(minutes=20)  # cutoff = scan(3): scans 0-2 age out

    summary = await cube.run(now=now)

    assert summary["trimmed"] == 3
    assert cube.commits() == before + 1
    assert cube.sink.last_snapshot_id == cube.tip()
    assert cube.times() == [as_ns(scan(n)) for n in (3, 4, 5)]
    again = await cube.run(now=now)
    assert again["trimmed"] == 0
    assert cube.commits() == before + 1  # nothing left to trim: no empty commit


async def test_no_age_trim_while_an_append_is_pending(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, now=scan(2))
    cube.repo.ledger[("s1", "next")] = FakeLedgerRow("s1", "next", scan(9), "pending", None)
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before


async def test_no_age_trim_on_a_provisional_repository(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, now=scan(2))
    cube.sink.last_snapshot_id = None
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before


async def test_no_age_trim_when_the_tip_is_not_the_recorded_snapshot(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    recorded = cube.append(0, 1, now=scan(1))
    cube.append(2, now=scan(2))  # committed, then the job died before recording
    cube.sink.last_snapshot_id = recorded
    before = cube.commits()

    summary = await cube.run(now=scan(9))

    assert summary["trimmed"] == 0
    assert cube.commits() == before
    assert cube.published == []  # the unrecorded tip is the next append's to record


async def test_a_trim_is_published_after_it_is_recorded(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))
    order: list[str] = []
    record = cube.repo.record_snapshot

    async def tracking_record(*a, **kw):
        order.append("record")
        return await record(*a, **kw)

    async def tracking_publish(sink, config, result):
        order.append(f"publish:{cube.sink.last_snapshot_id == result.snapshot_id}")
        await cube.publish(sink, config, result)

    cube.repo.record_snapshot = tracking_record
    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))  # cutoff scan(2)
    deps.after_batch = tracking_publish

    await run_cube_maintain("s1", deps)

    assert order == ["record", "publish:True"]
    _, result = cube.published[0]
    assert (result.committed, result.trimmed, result.initialised) == (True, 2, True)
    assert list(result.values) == [as_ns(scan(2)), as_ns(scan(3))]


async def test_the_recorded_tip_is_republished_on_every_run(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 10})
    tip = cube.append(0, 1)

    summary = await cube.run(now=scan(1))

    assert summary["published"] is True
    _, result = cube.published[0]
    assert (result.snapshot_id, result.committed, result.trimmed) == (tip, False, 0)
    assert len(result.values) == 2


def test_the_repository_pass_carries_the_grid_for_the_writer(tmp_path):
    # A form save drops cube:dimensions, and for a stopped source this
    # republish is the only one: it must carry x/y and the projection.
    cube = Cube(tmp_path, window=None)
    cube.append(0, 1)

    rp = maintain_repository(
        cube.storage(),
        cube.config,
        LATER,
        retention_seconds=3600,
        recorded_snapshot_id=cube.sink.last_snapshot_id,
        may_trim=True,
    )

    assert set(rp.state.statics) == {"x", "y", "goes_imager_projection"}
    assert list(rp.state.values) == [as_ns(scan(0)), as_ns(scan(1))]


async def test_a_trim_the_sink_lost_is_not_published(tmp_path, caplog):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def lost(*a, **kw):
        return False  # another writer recorded a different tip meanwhile

    cube.repo.record_snapshot = lost
    with caplog.at_level(logging.WARNING, logger="pipeline.cubes.maintain"):
        summary = await cube.run(now=scan(3) + dt.timedelta(minutes=25))

    assert summary["trimmed"] == 2
    assert cube.published == []
    assert "the sink moved during the trim" in caplog.text


async def test_a_cube_trimmed_to_empty_is_published_with_no_values(tmp_path):
    cube = Cube(tmp_path, window={"max_age": "30m"})
    cube.append(0, 1, now=scan(1))

    summary = await cube.run(now=scan(20))

    assert summary["trimmed"] == 2
    _, result = cube.published[0]
    assert len(result.values) == 0
    assert cube.times() == []


async def test_the_size_warning_fires_at_the_threshold(tmp_path, caplog):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    total = (await cube.run())["total_bytes"]
    assert total > 0

    below = await cube.run(warn_bytes=total + 1)
    assert (below["status"], below["attention"]) == ("ok", [])
    with caplog.at_level(logging.WARNING, logger="pipeline.cubes.maintain"):
        at = await cube.run(warn_bytes=total)

    assert (at["status"], at["attention"]) == ("attention", [ATTENTION_REPO_SIZE])
    assert at["warn_bytes"] == total
    assert "at or above CUBE_REPO_WARN_BYTES" in caplog.text
    assert cube.sink.last_maintenance["status"] == "attention"


async def test_terminal_ledger_rows_older_than_seven_days_are_pruned(tmp_path):
    cube = Cube(tmp_path, window=None)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=LEDGER_RETENTION_DAYS, hours=1)
    rows = {
        "old-appended": ("appended", old),
        "old-skipped": ("skipped", old),
        "old-pending": ("pending", old),  # never pruned
        "new-failed": ("failed", dt.datetime.now(dt.UTC)),
    }
    for item, (status, updated) in rows.items():
        cube.repo.ledger[("s1", item)] = FakeLedgerRow(
            "s1", item, scan(0), status, None, updated_at=updated
        )

    summary = await cube.run()

    assert summary["ledger_pruned"] == 2
    assert sorted(r.item_id for r in cube.repo.rows("s1")) == ["new-failed", "old-pending"]


async def test_a_sink_with_no_repository_is_pruned_but_never_creates_one(tmp_path):
    cube = Cube(tmp_path, window={"max_steps": 3})

    summary = await cube.run()

    assert summary["repository"] is False
    assert summary["status"] == "ok"
    assert "sizes" not in summary
    assert cube.listed == 0
    assert not cube.dir.exists()
    assert cube.sink.last_maintained_at == LATER


async def test_a_failure_is_recorded_and_raised(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.append(0)
    cube.sink.last_maintained_at = scan(0)  # an earlier success
    deps = cube.deps()

    def boom(sink):
        raise ConnectionError("platform bucket unreachable")

    deps.list_objects = boom
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", deps)

    assert cube.sink.last_maintenance["status"] == "failed"
    assert "platform bucket unreachable" in cube.sink.last_maintenance["error"]
    assert cube.sink.last_maintained_at == scan(0)  # the last success stands


async def test_an_invalid_config_fails_after_the_ledger_prune(tmp_path):
    cube = Cube(tmp_path, window=None)
    cube.sink.config = {"parser": "grib"}
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    cube.repo.ledger[("s1", "a")] = FakeLedgerRow(
        "s1", "a", scan(0), "appended", None, updated_at=old
    )

    with pytest.raises(ValueError, match="parser"):
        await cube.run()

    assert cube.repo.rows("s1") == []
    assert cube.sink.last_maintenance["status"] == "failed"


@pytest.mark.parametrize("change", ["gone", "disabled"])
async def test_a_gone_or_disabled_sink_is_left_alone(tmp_path, change):
    cube = Cube(tmp_path, window={"max_steps": 3})
    if change == "gone":
        cube.repo.sinks.clear()
    else:
        cube.sink.enabled = False

    assert await cube.run() is None
    assert cube.listed == 0


async def test_the_platform_lister_lists_only_the_cube_prefix(monkeypatch):
    import pipeline.cubes.maintain as maintain_mod
    from pipeline.config import Settings
    from pipeline.cubes.maintain import platform_lister

    seen: list[tuple[str, str]] = []

    def fake_list_sizes(client, bucket, prefix):
        seen.append((bucket, prefix))
        return [(f"{prefix}repo", 3), (f"{prefix}snapshots/S", 4)]

    monkeypatch.setattr(maintain_mod, "build_platform_client", lambda settings: object())
    monkeypatch.setattr(maintain_mod, "list_sizes", fake_list_sizes)
    settings = Settings.from_env(env={"STAGING_BUCKET": "plat"})
    sink = await FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube1")]).load_sink("s1")

    assert platform_lister(settings)(sink) == [("repo", 3), ("snapshots/S", 4)]
    # The trailing slash: a sibling item "_cube2" must never be counted.
    assert seen == [("plat", "assets/cube1/_cube/")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_cube_maintain.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.maintain'`.

- [ ] **Step 3: Implement**

In `src/pipeline/cubes/icerepo.py`, insert before `@dataclass(frozen=True)` / `class CubeState:`:

```python
def open_existing(storage: ic.Storage) -> ic.Repository | None:
    """Open the repository for maintenance (Z-6), or ``None`` if it was never
    created. Never creates it and never saves its config: only the writer
    does either. No virtual chunk credentials: trimming, expiry and GC read
    manifests, never source bytes."""
    if not ic.Repository.exists(storage):
        return None
    return ic.Repository.open(storage, config=_config())


```

In `src/pipeline/cubes/write.py`, rename the private trim so maintenance can share it. Change `def _trim(session: ic.Session, state: CubeState, k: int) -> None:` to `def trim_steps(session: ic.Session, state: CubeState, k: int) -> None:`, and in `_attempt` change `_trim(session, after, trimmed)` to `trim_steps(session, after, trimmed)`. Nothing else calls it (`grep -rn "_trim(" src tests` comes back empty afterwards).

Create `src/pipeline/cubes/maintain.py`:

```python
"""``pipeline.cube_maintain_sink``: trim, expire, GC and measure one cube (spec §10; ADR 0022).

The job runs under the sink's ``lock`` (``cube:{id}``), so it never runs
alongside that sink's appends (spec §14.4). One run does six things:
1. Prune the sink's terminal ledger rows older than 7 days.
2. Open the repository if it exists. Maintenance never creates one.
3. Only for a sink with a ``window`` (ADR 0022: GC runs only there):
   a. Trim by age with no new data. If nothing is pending and the tip is the
      recorded snapshot, drop the steps the window no longer holds (§6.2
      step 6) in one commit, then record that commit.
   b. ``expire_snapshots(now - retention)``, then ``garbage_collect`` with
      the same cutoff.
4. Republish the recorded tip on the cube collection (``after_batch``, Z-5's
   idempotent writer). That covers a trim, and also a publish that an
   append's crash missed.
5. List the repository prefix and record object counts and bytes per kind.
   Icechunk never deletes ``transactions/`` or ``overwritten/`` (I-143). At or
   above ``CUBE_REPO_WARN_BYTES`` the run's status is ``attention``.
6. Write ``last_maintenance`` on every run, and ``last_maintained_at`` only
   on success.

A sink without a window gets steps 1, 2, 4, 5 and 6, never 3. Its ledger
still needs pruning, and its repository, which nothing trims, is the one
most likely to cross the size warning.

The Icechunk and boto3 parts block, so they run through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import icechunk as ic

from pipeline.config import (
    DEFAULT_CUBE_REPO_WARN_BYTES,
    DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS,
    Settings,
)
from pipeline.cubes.append import AfterBatch
from pipeline.cubes.config import CubeSinkConfig, parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, CubeState, cube_prefix, open_existing, read_state
from pipeline.cubes.repo import CubeRepo, CubeSink
from pipeline.cubes.steps import trim_count
from pipeline.cubes.write import MAX_ERROR_CHARS, BatchResult, error_text, trim_steps
from pipeline.storage.platform import build_platform_client, list_sizes

logger = logging.getLogger(__name__)

#: spec §4.2: terminal ledger rows older than this are pruned
LEDGER_RETENTION_DAYS = 7
#: the per-kind readout (spec §10); anything else (``repo``, ``config.yaml``) is ``other``
KINDS = ("transactions", "overwritten", "manifests", "snapshots", "chunks")
OTHER_KIND = "other"
#: the GCSummary counters recorded in ``last_maintenance.gc``
GC_COUNTS = (
    "snapshots_deleted",
    "manifests_deleted",
    "chunks_deleted",
    "transaction_logs_deleted",
    "attributes_deleted",
    "bytes_deleted",
    "objects_failed_to_delete",
)
MAX_DELETE_ERRORS = 5
STATUS_OK = "ok"
STATUS_ATTENTION = "attention"
STATUS_FAILED = "failed"
ATTENTION_REPO_SIZE = "repo_size"
ATTENTION_GC_DELETE_FAILURES = "gc_delete_failures"

#: every object in a cube repository: (key relative to its prefix, bytes)
ListObjects = Callable[[CubeSink], list[tuple[str, int]]]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


@dataclass
class MaintainDeps:
    repo: CubeRepo
    #: the Icechunk Storage of a sink's repository (``icerepo.cube_storage``)
    storage_for: Callable[[CubeSink], Any]
    list_objects: ListObjects
    retention_seconds: int = DEFAULT_CUBE_SNAPSHOT_RETENTION_SECONDS
    warn_bytes: int = DEFAULT_CUBE_REPO_WARN_BYTES
    #: Z-5's collection asset writer (the same hook ``cube_append`` calls)
    after_batch: AfterBatch | None = None
    now: Callable[[], dt.datetime] = _utcnow


@dataclass(frozen=True)
class RepoPass:
    """What the blocking repository pass did."""

    exists: bool
    #: the branch tip after the pass (the trim commit, if there was one)
    tip: str | None = None
    #: the tip's cube: its time values AND its grid (``statics``); ``None``
    #: until the cube has its first step. The grid goes to Z-5's writer:
    #: for a stopped source this republish is the only one, and a form save
    #: drops ``cube:dimensions``, so it must never publish time alone.
    state: CubeState | None = None
    trimmed: int = 0
    expired: int | None = None
    gc: dict[str, Any] | None = None
    durations_ms: dict[str, int] = field(default_factory=dict)


def size_by_kind(objects: Iterable[tuple[str, int]]) -> dict[str, dict[str, int]]:
    """Object counts and bytes per top-level directory of the repository."""
    sizes = {kind: {"objects": 0, "bytes": 0} for kind in (*KINDS, OTHER_KIND)}
    for key, size in objects:
        head, sep, _ = key.partition("/")
        kind = head if sep and head in KINDS else OTHER_KIND
        sizes[kind]["objects"] += 1
        sizes[kind]["bytes"] += size
    return sizes


def gc_counts(summary: ic.GCSummary) -> dict[str, Any]:
    counts: dict[str, Any] = {name: int(getattr(summary, name)) for name in GC_COUNTS}
    counts["delete_errors"] = [
        str(err)[:MAX_ERROR_CHARS] for err in list(summary.delete_errors)[:MAX_DELETE_ERRORS]
    ]
    return counts


def maintain_repository(
    storage: ic.Storage,
    config: CubeSinkConfig,
    now: dt.datetime,
    *,
    retention_seconds: int,
    recorded_snapshot_id: str | None,
    may_trim: bool,
) -> RepoPass:
    """Blocking: trim (windowed, recorded tip, nothing pending), expire and GC
    (windowed only), then read the tip's time values."""
    repo = open_existing(storage)
    if repo is None:
        return RepoPass(exists=False)
    durations: dict[str, int] = {}
    tip = repo.lookup_branch(BRANCH)
    trimmed = 0
    if config.window is not None and may_trim and tip == recorded_snapshot_id:
        started = time.monotonic()
        session = repo.writable_session(BRANCH)
        state = read_state(session, config.append_dim)
        trimmed = trim_count(state.values, config.window, now) if state.initialised else 0
        if trimmed:
            trim_steps(session, state, trimmed)
            tip = session.commit(f"trim {trimmed} steps (maintenance)")
        durations["trim"] = _ms(started)
    expired: int | None = None
    gc: dict[str, Any] | None = None
    if config.window is not None:
        cutoff = now - dt.timedelta(seconds=retention_seconds)
        started = time.monotonic()
        expired = len(repo.expire_snapshots(cutoff))
        durations["expire"] = _ms(started)
        started = time.monotonic()
        gc = gc_counts(repo.garbage_collect(cutoff))
        durations["gc"] = _ms(started)
    after = read_state(repo.readonly_session(BRANCH), config.append_dim)
    return RepoPass(
        exists=True,
        tip=tip,
        state=after if after.initialised else None,
        trimmed=trimmed,
        expired=expired,
        gc=gc,
        durations_ms=durations,
    )


def platform_lister(settings: Settings) -> ListObjects:
    """Production ``list_objects``: the cube prefix in the platform bucket.
    The trailing slash keeps ``_cube`` from matching a ``_cube…`` sibling."""

    def _list(sink: CubeSink) -> list[tuple[str, int]]:
        prefix = f"{cube_prefix(sink.cube_collection_id)}/"
        client = build_platform_client(settings)
        return [
            (key[len(prefix) :], size)
            for key, size in list_sizes(client, settings.staging_bucket, prefix)
        ]

    return _list


async def run_cube_maintain(cube_sink_id: str, deps: MaintainDeps) -> dict[str, Any] | None:
    """Maintain one sink and record the summary. ``None`` for a sink that is
    gone or disabled (nothing recorded). An error is recorded as status
    ``failed`` and then raised."""
    sink = await deps.repo.load_sink(cube_sink_id)
    if sink is None or not sink.enabled:
        return None
    now = deps.now()
    summary: dict[str, Any] = {
        "status": STATUS_OK,
        "started_at": now.isoformat(),
        "warn_bytes": deps.warn_bytes,
        "attention": [],
        "durations_ms": {},
    }
    try:
        await _maintain(sink, deps, now, summary)
    except Exception as exc:
        summary["status"] = STATUS_FAILED
        summary["error"] = error_text(exc)
        summary["finished_at"] = deps.now().isoformat()
        try:
            await deps.repo.record_maintenance(sink.id, summary=summary, maintained_at=None)
        except Exception:
            logger.exception(
                "cube_maintain: could not record the failure", extra={"cube_sink_id": sink.id}
            )
        raise
    finished = deps.now()
    summary["finished_at"] = finished.isoformat()
    await deps.repo.record_maintenance(sink.id, summary=summary, maintained_at=finished)
    logger.info(
        "cube_maintain finished",
        extra={
            "cube_sink_id": sink.id,
            "status": summary["status"],
            "trimmed": summary.get("trimmed", 0),
            "total_bytes": summary.get("total_bytes"),
            "ledger_pruned": summary["ledger_pruned"],
        },
    )
    return summary


async def _maintain(
    sink: CubeSink, deps: MaintainDeps, now: dt.datetime, summary: dict[str, Any]
) -> None:
    # The ledger first: a store outage must not stop DB hygiene.
    summary["ledger_pruned"] = await deps.repo.prune_ledger(sink.id, LEDGER_RETENTION_DAYS)
    config = parse_cube_sink_config(sink.config)
    summary["window"] = config.window is not None
    # A provisional repository (nothing recorded) belongs to the writer
    # (#98); a pending row means the next append trims anyway.
    may_trim = sink.last_snapshot_id is not None and not await deps.repo.has_pending(sink.id)
    rp = await asyncio.to_thread(
        lambda: maintain_repository(
            deps.storage_for(sink),
            config,
            now,
            retention_seconds=deps.retention_seconds,
            recorded_snapshot_id=sink.last_snapshot_id,
            may_trim=may_trim,
        )
    )
    summary["repository"] = rp.exists
    summary["durations_ms"].update(rp.durations_ms)
    if not rp.exists:
        return
    summary.update(trimmed=rp.trimmed, expired_snapshots=rp.expired, gc=rp.gc)
    recorded = sink.last_snapshot_id
    if rp.trimmed and recorded is not None and rp.tip is not None:
        if await deps.repo.record_snapshot(sink.id, snapshot_id=rp.tip, from_snapshot_id=recorded):
            recorded = rp.tip
        else:
            logger.warning(
                "cube_maintain: the sink moved during the trim; the next append records the tip",
                extra={"cube_sink_id": sink.id, "snapshot_id": rp.tip},
            )
    # Z-5's writer publishes only the recorded tip, so the trim is recorded first.
    summary["published"] = False
    if deps.after_batch is not None and rp.state is not None and recorded == rp.tip:
        # Whoever wires after_batch adds statics=rp.state.statics and
        # spatial_dims=rp.state.spatial_dims here (Z-5's BatchResult fields).
        await deps.after_batch(
            sink,
            config,
            BatchResult(
                outcomes={},
                snapshot_id=rp.tip,
                committed=rp.trimmed > 0,
                values=rp.state.values,
                trimmed=rp.trimmed,
                initialised=True,
            ),
        )
        summary["published"] = True
    if rp.gc is not None and rp.gc["objects_failed_to_delete"]:
        summary["attention"].append(ATTENTION_GC_DELETE_FAILURES)
        logger.warning(
            "cube GC could not delete some objects",
            extra={"cube_sink_id": sink.id, **rp.gc},
        )
    started = time.monotonic()
    sizes = size_by_kind(await asyncio.to_thread(deps.list_objects, sink))
    summary["durations_ms"]["list"] = _ms(started)
    total_bytes = sum(kind["bytes"] for kind in sizes.values())
    summary.update(
        sizes=sizes,
        total_objects=sum(kind["objects"] for kind in sizes.values()),
        total_bytes=total_bytes,
    )
    if total_bytes >= deps.warn_bytes:
        summary["attention"].append(ATTENTION_REPO_SIZE)
        logger.warning(
            "cube repository at or above CUBE_REPO_WARN_BYTES (I-143)",
            extra={
                "cube_sink_id": sink.id,
                "cube_collection_id": sink.cube_collection_id,
                "total_bytes": total_bytes,
                "warn_bytes": deps.warn_bytes,
                "transactions_bytes": sizes["transactions"]["bytes"],
                "overwritten_bytes": sizes["overwritten"]["bytes"],
            },
        )
    if summary["attention"]:
        summary["status"] = STATUS_ATTENTION
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:warnings tests/test_cube_maintain.py tests/test_cube_write.py tests/test_cube_icerepo.py && uv run ruff check .`
Expected: PASS (21 tests in `test_cube_maintain.py`); `All checks passed!`. Icechunk prints `WARN … LocalFileSystem storage is not safe for concurrent commits` on stderr; with local storage that is expected.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/cubes/icerepo.py services/pipeline/src/pipeline/cubes/write.py services/pipeline/src/pipeline/cubes/maintain.py services/pipeline/tests/test_cube_maintain.py
git commit -m "Z-6: cube_maintain_sink: age trim, expiry + GC, republish, per-kind sizes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Jobs: the hourly fan-out, the per-sink job and stalled recovery

**Files:**
- Modify: `services/pipeline/src/pipeline/jobs/cubes.py`
- Test: `services/pipeline/tests/test_cube_jobs.py` (imports, one changed assertion, new fixtures and tests)

**Interfaces:**
- Consumes: `MaintainDeps`, `platform_lister` and `run_cube_maintain` (Task 3); `CubeRepo.maintainable_sinks` (Task 2); `Settings.cube_*` (Task 1); the existing `cube_lock`, `cube_storage`, `QueueBackend.enqueue(..., lock=)` and `retry_stalled`.
- Produces:
  - Constants: `JOB_CUBE_MAINTAIN = "pipeline.cube_maintain"`, `JOB_CUBE_MAINTAIN_SINK = "pipeline.cube_maintain_sink"`, `MAINTAIN_CRON = "23 * * * *"`, `LOCKED_JOBS`.
  - `enqueue_cube_maintain(queue, cube_sink_id) -> Enqueued`
  - `schedule_maintenance(repo, queue) -> int`
  - `production_maintain_deps(settings, repo) -> MaintainDeps`
  - A new `register` parameter: `maintain_deps_factory: Callable[[], MaintainDeps] | None = None`.
  - `pipeline/main.py` needs no change, because it already calls `cubes.register(queue, settings)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cube_jobs.py`, make three edits.

(a) Imports. Add `from pipeline.cubes.maintain import MaintainDeps` after the `pipeline.cubes.append` import, and extend the `pipeline.jobs.cubes` import to:

```python
from pipeline.jobs.cubes import (
    CUBE_APPEND_RETRY,
    JOB_CUBE_APPEND,
    JOB_CUBE_KICK,
    JOB_CUBE_MAINTAIN,
    JOB_CUBE_MAINTAIN_SINK,
    KICK_CRON,
    KICK_STALE_SECONDS,
    MAINTAIN_CRON,
    cube_append_enqueuer,
    cube_lock,
    enqueue_cube_append,
    enqueue_cube_maintain,
    production_append_deps,
    production_maintain_deps,
    register,
)
```

(b) In `test_kick_still_kicks_stale_sinks_when_stalled_recovery_fails`, replace the final assertion

```python
    assert [r.getMessage() for r in caplog.records] == [
        "cube_kick: stalled-job recovery failed"
    ]
```

with

```python
    assert [(r.getMessage(), r.job_name) for r in caplog.records] == [
        ("cube_kick: stalled-job recovery failed", JOB_CUBE_APPEND),
        ("cube_kick: stalled-job recovery failed", JOB_CUBE_MAINTAIN_SINK),
    ]
```

This change is intended. The kick now recovers both locked job names, and a failure of either is logged without stopping the other or the stale-sink kick.

(c) Append at the end of the file:

```python


@pytest.fixture
def maintained(monkeypatch) -> list[str]:
    """Sink ids the maintenance handler ran run_cube_maintain for."""
    calls: list[str] = []

    async def fake_run(cube_sink_id: str, deps) -> None:
        assert deps is MAINTAIN_DEPS
        calls.append(cube_sink_id)

    monkeypatch.setattr(cubes_jobs, "run_cube_maintain", fake_run)
    return calls


MAINTAIN_DEPS = object()


@pytest.fixture
def mqueue(repo: FakeCubeRepo, maintained: list[str]) -> InMemoryQueue:
    q = InMemoryQueue()
    register(
        q,
        Settings.from_env(env={}),
        repo=repo,
        deps_factory=lambda: DEPS,
        maintain_deps_factory=lambda: MAINTAIN_DEPS,
    )
    return q


def test_maintenance_registration(mqueue: InMemoryQueue):
    assert mqueue.queues[JOB_CUBE_MAINTAIN_SINK] == QUEUE_DEFAULT
    assert mqueue.periodic[JOB_CUBE_MAINTAIN].cron == MAINTAIN_CRON == "23 * * * *"
    assert mqueue.retry_specs.get(JOB_CUBE_MAINTAIN_SINK) is None


async def test_maintenance_takes_the_sink_lock_and_no_queueing_lock(mqueue: InMemoryQueue):
    await enqueue_cube_maintain(mqueue, "s1")
    job = mqueue.jobs[0]
    assert (job.name, job.payload) == (JOB_CUBE_MAINTAIN_SINK, {"cube_sink_id": "s1"})
    assert (job.lock, job.queueing_lock) == ("cube:s1", None)


async def test_the_hourly_tick_enqueues_every_enabled_sink(mqueue: InMemoryQueue):
    await mqueue.run_periodic(JOB_CUBE_MAINTAIN, timestamp=1_700_000_000)
    assert [(j.name, j.payload["cube_sink_id"], j.lock) for j in mqueue.jobs] == [
        (JOB_CUBE_MAINTAIN_SINK, "s1", "cube:s1"),
        (JOB_CUBE_MAINTAIN_SINK, "s2", "cube:s2"),
    ]


async def test_maintenance_waits_behind_a_running_append(
    mqueue: InMemoryQueue, maintained: list[str], ran: list[str]
):
    append = await enqueue_cube_append(mqueue, "s1")
    mqueue.strand(append.job_id)  # running (holds cube:s1) for this test
    await enqueue_cube_maintain(mqueue, "s1")
    await mqueue.run_pending()
    assert maintained == []  # blocked by the append's lock

    await mqueue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)  # recovers it
    await mqueue.run_pending()
    assert ran == ["s1"]
    assert maintained == ["s1"]


async def test_kick_recovers_a_stranded_maintenance_job(mqueue: InMemoryQueue, ran: list[str]):
    stranded = await enqueue_cube_maintain(mqueue, "s1")
    mqueue.strand(stranded.job_id)  # its worker died: holds cube:s1 forever
    await enqueue_cube_append(mqueue, "s1")
    await mqueue.run_pending()
    assert ran == []  # wedged behind the dead maintenance

    await mqueue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    await mqueue.run_pending()
    assert ran == ["s1"]


async def test_cube_maintain_sink_runs_the_maintenance(
    mqueue: InMemoryQueue, maintained: list[str]
):
    await enqueue_cube_maintain(mqueue, "s2")
    await mqueue.run_pending()
    assert maintained == ["s2"]
    assert mqueue.jobs[0].status == "done"


async def test_production_maintain_deps_wire_the_real_seams(repo: FakeCubeRepo):
    settings = Settings.from_env(
        env={
            "EGRESS_ALLOW_HOSTS": "minio",
            "CUBE_SNAPSHOT_RETENTION_SECONDS": "7200",
            "CUBE_REPO_WARN_BYTES": "4096",
        }
    )
    deps = production_maintain_deps(settings, repo)
    assert isinstance(deps, MaintainDeps)
    assert (deps.retention_seconds, deps.warn_bytes) == (7200, 4096)
    assert deps.after_batch is None  # wired by whichever of Z-5 / Z-6 merges second
    sink = await repo.load_sink("s1")
    assert "prefix: assets/cube1/_cube" in repr(deps.storage_for(sink))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q tests/test_cube_jobs.py`
Expected: FAIL with `ImportError: cannot import name 'JOB_CUBE_MAINTAIN'`.

- [ ] **Step 3: Implement**

In `src/pipeline/jobs/cubes.py`, make seven edits.

(a) In the module docstring, add a bullet after the `pipeline.cube_append` bullet (before the closing `"""`):

```
- ``pipeline.cube_maintain`` (hourly, ``:23``) enqueues one
  ``pipeline.cube_maintain_sink`` per enabled sink with the same ``lock``
  (no ``queueing_lock``: that one is the append's), so maintenance never runs
  alongside the sink's appends (spec §10, §14.4). ``cube_kick`` recovers a
  stalled maintenance job too, since it would hold the same lock.
  ``cube_maintain_sink`` runs ``cubes.maintain.run_cube_maintain``: expiry and
  GC only on a sink with a window (ADR 0022). No retry: the next hour retries.
```

(b) Imports, after `from pipeline.cubes.icerepo import cube_storage`:

```python
from pipeline.cubes.maintain import MaintainDeps, platform_lister, run_cube_maintain
```

(c) Constants, after `KICK_CRON = "*/5 * * * *"`:

```python
JOB_CUBE_MAINTAIN = "pipeline.cube_maintain"
JOB_CUBE_MAINTAIN_SINK = "pipeline.cube_maintain_sink"
MAINTAIN_CRON = "23 * * * *"
#: jobs holding a sink's lock: a dead worker's copy of either wedges the sink
LOCKED_JOBS = (JOB_CUBE_APPEND, JOB_CUBE_MAINTAIN_SINK)
```

(d) Replace `kick_stale_sinks`'s docstring and its recovery block (everything before `cube_sink_ids = await repo.sinks_with_stale_pending(...)`), and add the two maintenance helpers just above it:

```python
async def enqueue_cube_maintain(queue: QueueBackend, cube_sink_id: str) -> Enqueued:
    """One sink's maintenance, under the append's ``lock`` (spec §10)."""
    return await queue.enqueue(
        JOB_CUBE_MAINTAIN_SINK, {"cube_sink_id": cube_sink_id}, lock=cube_lock(cube_sink_id)
    )


async def schedule_maintenance(repo: CubeRepo, queue: QueueBackend) -> int:
    """The hourly fan-out: one ``cube_maintain_sink`` per enabled sink."""
    cube_sink_ids = await repo.maintainable_sinks()
    for cube_sink_id in cube_sink_ids:
        await enqueue_cube_maintain(queue, cube_sink_id)
    return len(cube_sink_ids)


async def kick_stale_sinks(repo: CubeRepo, queue: QueueBackend) -> int:
    """§5.3 backstop: re-enqueue every enabled sink with a stale pending row.
    Returns how many sinks were kicked (coalesced or not).

    First, any ``cube_append`` or ``cube_maintain_sink`` a dead worker left
    running goes back to the queue (plan decision 14). Until then it holds
    its sink's lock, and the enqueue below would only coalesce into a job
    that can never start. A failed recovery is logged and the stale-sink kick
    still runs, so one bad query cannot disable the backstop."""
    for job_name in LOCKED_JOBS:
        try:
            await queue.retry_stalled(job_name)
        except Exception:
            logger.exception(
                "cube_kick: stalled-job recovery failed",
                extra={"job_name": job_name},
            )
    cube_sink_ids = await repo.sinks_with_stale_pending(KICK_STALE_SECONDS)
    await cube_append_enqueuer(queue)(cube_sink_ids)
    return len(cube_sink_ids)
```

(e) After `production_append_deps` (before `def register`):

```python
def production_maintain_deps(settings: Settings, repo: CubeRepo) -> MaintainDeps:
    """The real seams. No master key: maintenance never reads a source."""
    return MaintainDeps(
        repo=repo,
        storage_for=lambda sink: cube_storage(settings, sink.cube_collection_id),
        list_objects=platform_lister(settings),
        retention_seconds=settings.cube_snapshot_retention_seconds,
        warn_bytes=settings.cube_repo_warn_bytes,
    )
```

(f) Add `register`'s new keyword parameter after `deps_factory`:

```python
    maintain_deps_factory: Callable[[], MaintainDeps] | None = None,
```

(g) In `register`, after the `cube_kick` handler, add the two handlers, and register them after the existing two registrations:

```python
    async def cube_maintain(timestamp: int) -> None:
        scheduled = await schedule_maintenance(_repo(), queue)
        if scheduled:
            logger.info(
                "cube_maintain enqueued sink maintenance",
                extra={"sinks": scheduled, "scheduled_timestamp": timestamp},
            )

    async def cube_maintain_sink(cube_sink_id: str) -> None:
        deps = (
            maintain_deps_factory()
            if maintain_deps_factory is not None
            else production_maintain_deps(settings, _repo())
        )
        await run_cube_maintain(cube_sink_id, deps)

    # Default queue: the append reads headers, not bytes (spec §6).
    queue.register_task(cube_append, name=JOB_CUBE_APPEND, retry=CUBE_APPEND_RETRY)
    queue.register_periodic(cube_kick, name=JOB_CUBE_KICK, cron=KICK_CRON)
    # Maintenance lists and deletes small Icechunk objects: default queue too.
    queue.register_task(cube_maintain_sink, name=JOB_CUBE_MAINTAIN_SINK)
    queue.register_periodic(cube_maintain, name=JOB_CUBE_MAINTAIN, cron=MAINTAIN_CRON)
```

If Z-5 is on `main` by now, also make the three wiring edits in "Coordination with Z-5" above. All three are required, including the grid in `BatchResult(...)`.

- [ ] **Step 4: Run the whole pipeline suite**

Run: `uv run pytest -q -p no:warnings && uv run ruff check .`
Expected: all pass (`2038 passed, 46 skipped` at validation; main's count plus this slice's new tests); `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/cubes.py services/pipeline/tests/test_cube_jobs.py
git commit -m "Z-6: hourly cube_maintain fan-out under the sink lock; cube_kick recovers it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs and the gates

**Files:**
- Modify: `docs/monitoring.md` (new section before `## Table hygiene (M2-G, ADR 0012) & metrics (M2-H)`)
- Modify: `docs/backend.md` (an "App environment" row after the `STAGING_*` row)
- Modify: `services/pipeline/README.md` (two env-contract rows before `FINALIZE_STALE_SECONDS`; a paragraph in the cube-sinks bullet)
- Modify: `docs/FEATURES.md` (a Z-6 row after the Z-4 row, or after Z-5's if that merged first)

Docs carry no status and link no issue (the AGENTS.md link rule). `docs/decisions/README.md` already states the ADR 0022 GC invariant, and I-143 already describes this readout, so neither file changes.

- [ ] **Step 1: Apply the doc edits**

`docs/monitoring.md`: insert before `## Table hygiene (M2-G, ADR 0012) & metrics (M2-H)`:

```markdown
## Cube repositories (Z-6, ADR 0022)

A virtual cube's Icechunk repository at `assets/{cube}/_cube/` is the one
place bytes are deleted outside `asset_gc`. The deleter is Icechunk's own GC,
and it runs only on a sink with a `window`. `pipeline.cube_maintain`
(`23 * * * *`) enqueues one `pipeline.cube_maintain_sink` per **enabled**
sink. Each job takes the sink's `lock` (`cube:{id}`), so it never runs
alongside that sink's appends. A dead worker's maintenance job is recovered
by `cube_kick`, like an append. One run (`pipeline/cubes/maintain.py`):

1. deletes the sink's terminal ledger rows (`cube_appends`) last updated more
   than 7 days ago; `pending` rows are kept;
2. opens the repository, if it exists (maintenance never creates one);
3. **only with a window**:
   - trims by age when the source has stopped: if nothing is pending and
     the tip is the recorded snapshot, it drops the steps the window no
     longer holds in one commit and records it (`last_snapshot_id`);
   - runs `expire_snapshots` and then `garbage_collect`, both with the cutoff
     `now − CUBE_SNAPSHOT_RETENTION_SECONDS` (default 1 h);
4. republishes the recorded tip on the cube collection (Z-5's writer,
   idempotent);
5. lists the prefix and records object counts and bytes per kind.

The result goes into `cube_sinks.last_maintenance` on every run.
`last_maintained_at` is set only on success.

| `last_maintenance` key | Meaning |
|---|---|
| `status` | `ok`, `attention` (see `attention`) or `failed` (see `error`) |
| `attention` | Reasons: `repo_size` (total ≥ `CUBE_REPO_WARN_BYTES`, default 1 GiB) and `gc_delete_failures` (GC could not delete some objects). Each one also logs a WARNING. |
| `started_at`, `finished_at`, `durations_ms` | ISO times; per-phase milliseconds (`trim`, `expire`, `gc`, `list`) |
| `window`, `repository` | Whether the sink has a window, and whether its repository exists |
| `trimmed`, `published` | Steps the age trim dropped, and whether the collection asset was republished |
| `expired_snapshots`, `gc` | Snapshots expired, and Icechunk's `GCSummary` counters (`snapshots_deleted`, `manifests_deleted`, `chunks_deleted`, `transaction_logs_deleted`, `attributes_deleted`, `bytes_deleted`, `objects_failed_to_delete`, the first 5 `delete_errors`). Both are `null` without a window. |
| `sizes`, `total_objects`, `total_bytes`, `warn_bytes` | Per kind `{objects, bytes}` for `transactions`, `overwritten`, `manifests`, `snapshots`, `chunks` and `other` (the `repo` object, config) |
| `ledger_pruned` | Ledger rows deleted |

Expiry and GC keep snapshots, manifests and chunks flat. They never delete
`transactions/` or `overwritten/`, which grow by one object each per commit
(I-143, about 8 MB a day per cube). Those two kinds in `sizes` are the ones to
watch. At the 1 GiB default, a 5-minute cube reaches `attention` after about
four months. A sink without a window is never trimmed or garbage-collected,
so its `snapshots` and `manifests` grow too.
```

`docs/backend.md`: insert after the `| \`STAGING_*\` | … |` row of the "App environment" table:

```markdown
| `CUBE_SNAPSHOT_RETENTION_SECONDS`, `CUBE_REPO_WARN_BYTES` | Pipeline-side cube maintenance (Z-6): snapshots older than the retention (default `3600`, at least `300`) are expired and garbage-collected on sinks with a window; a repository at or above the byte threshold (default 1 GiB) makes the sink's `last_maintenance.status` `attention`. Reference: [`monitoring.md`](monitoring.md) "Cube repositories". |
```

`services/pipeline/README.md`: insert before the `| \`FINALIZE_STALE_SECONDS\` |` row of "Environment contract":

```markdown
| `CUBE_SNAPSHOT_RETENTION_SECONDS` | `3600` | `pipeline.cube_maintain_sink` expires cube snapshots older than this, then garbage-collects with the same cutoff, on sinks with a window only (Z-6, ADR 0022). At least `300`, so a mistyped `0` cannot pull a snapshot from under a reader. |
| `CUBE_REPO_WARN_BYTES` | `1073741824` | A cube repository at or above this many bytes logs a WARNING and its sink's `last_maintenance.status` reads `attention` (I-143: Icechunk never deletes transaction logs or `overwritten/` backups). |
```

and in the **cube sinks** bullet, insert before the line `The pipeline never writes \`cube_sinks.updated_at\` (the app's version, #98).`:

```markdown
  `pipeline.cube_maintain` (`23 * * * *`) enqueues one
  `pipeline.cube_maintain_sink` per enabled sink under the same `lock` (no
  `queueing_lock`; `cube_kick` recovers a stalled one too). It prunes the
  ledger (terminal rows > 7 days), and on a sink with a window it age-trims
  a stopped source, then expires and garbage-collects snapshots
  (`cubes/maintain.py`). It republishes the recorded tip and records per-kind
  sizes in `last_maintenance` (`docs/monitoring.md`).
```

`docs/FEATURES.md`: insert after the `| Z-4 · \`cube_append\` + pipeline deps | … |` row (or after Z-5's row, if it is there):

```markdown
| Z-6 · `cube_maintain` | ✅ | `pipeline.cube_maintain` (`23 * * * *`) enqueues one `pipeline.cube_maintain_sink` per enabled sink under the sink's `lock` (`cube_kick` recovers a stalled one). `cubes/maintain.py`: prune terminal ledger rows older than 7 days; with a `window` only (ADR 0022), age-trim a stopped source in one recorded commit (only when nothing is pending and the tip is the recorded snapshot), then `expire_snapshots` + `garbage_collect` at `now − CUBE_SNAPSHOT_RETENTION_SECONDS` (1 h); republish the recorded tip through Z-5's `after_batch`; list the prefix into per-kind object counts and bytes. `last_maintenance` (shape in `monitoring.md`) has `status` `ok` / `attention` (≥ `CUBE_REPO_WARN_BYTES`, 1 GiB, or GC delete failures) / `failed`, and `last_maintained_at` is set on success only. Maintenance never creates a repository and never trims a provisional one (GC still collects the snapshots its reset orphaned) |
```

- [ ] **Step 2: Run every gate**

```bash
cd services/pipeline && uv run pytest -q -p no:warnings && uv run ruff check . && cd ../..
npm install   # first time in this worktree
npm run verify
```

Expected: pytest all pass; ruff `All checks passed!`; verify green. No app code changed, so verify only confirms nothing regressed.

- [ ] **Step 3: Commit**

```bash
git add docs/monitoring.md docs/backend.md services/pipeline/README.md docs/FEATURES.md
git commit -m "Z-6: docs: cube repository maintenance, last_maintenance shape, env vars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: PR** (`git push -u origin feat/z6-cube-maintain`, then `gh pr create --base main`)

Title `Z-6: cube_maintain: expiry, GC, ledger prune and size readout`. The body starts `Closes #92` and lists:
- the gates run;
- whether the DB-gated tests ran;
- **lead-only steps: none**. Z-9 (#95) checks this live: after 7 h, `last_maintenance` shows deletions and a flat repository size.
- the deviations: Decisions 1–3, 10 (added in review) and 11 above;
- the Z-5 wiring state: wired here, or left to Z-5's PR.

On merge, flip nothing: #93 (Z-7) is blocked on Z-5, not Z-6.

---

## Self-review (done while writing)

- **Spec §10 coverage:**
  - cron + per-sink lock: Task 4;
  - age trim without new data: Task 3, Decision 5;
  - expire + GC with the same cutoff: Task 3;
  - ledger prune after 7 days: Tasks 2–3;
  - `last_maintained_at` / `last_maintenance` with GCSummary counts, durations and per-kind sizes: Task 3;
  - WARNING + attention at `CUBE_REPO_WARN_BYTES`: Task 3, Decision 3;
  - `num_updates_per_repo_info_file = 100` on every open: `open_existing` uses `_config()`;
  - the `docs/monitoring.md` note: Task 5.

  The issue's acceptance tests are all in Tasks 3–4: a windowless sink is never GC'd, the age trim commits once, the summary and per-kind sizes are recorded, the warning fires at the threshold, and the lock is passed.
- **Placeholders:** none. Every code step carries the tested code.
- **Names across tasks:** `maintainable_sinks`, `record_snapshot(…, from_snapshot_id=)`, `prune_ledger(…, older_than_days)`, `record_maintenance(…, summary=, maintained_at=)`, `trim_steps`, `open_existing`, `MaintainDeps`, `run_cube_maintain`, `platform_lister`, `JOB_CUBE_MAINTAIN_SINK`, `enqueue_cube_maintain` and `production_maintain_deps` are spelled the same in every task.
