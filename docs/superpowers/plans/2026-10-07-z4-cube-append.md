# Z-4 · `cube_append`: Virtual Appends With a Rolling Window — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Z-3's `cube_append` stub with the real single-writer job. It turns a sink's pending ledger rows into virtual references appended to the sink's Icechunk repository at `assets/{cube}/_cube/`, trims the rolling window, and commits once per batch.

**Architecture:** The job is split into small modules under `pipeline/cubes/`, each testable on its own:
- `source.py`: an S3 connection → obstore + Icechunk configs, with an explicit endpoint and an egress check.
- `resolve.py`: an item → its NODD object, through the reference-mode association that produced it.
- `steps.py`: a header → one cube step, plus the layout and window rules.
- `icerepo.py`: open or create the repository, read its state, reset it.
- `write.py`: classify a batch, write it, trim it and commit it in one session; a conflict is redone once.
- `append.py`: the async orchestration. It claims rows, stops crash loops, parses 4 at a time off the event loop, records the commit (conditionally the first time, closing #90's race), writes the ledger and re-enqueues.

`connections/sources.py` takes the href → association rule out of process staging so both callers share it. `jobs/cubes.py` wires the production seams in and adds a retry.

Tests run offline. The "fake NODD" is GOES-shaped HDF5 files written with h5py into `tmp_path`; the "fake repository" is Icechunk's in-memory storage. A single integration test hits live NODD and a Silo, and it is skipped unless `CUBE_IT=1`.

**Tech Stack:** Python 3.12, icechunk 2.3.0 (spec floor 2.2.2), virtualizarr 2.7.3 (`[hdf]`), zarr 3.4.0, xarray 2026.7.0, obstore 0.11.1, h5py 3.16.0, obspec-utils 0.9.0, Procrastinate 3.9, psycopg 3, pytest (asyncio auto mode), ruff. No app change, no migration.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §6.1, §6.2, §11 (decision §14.7; §3.2 the ledger vocabulary; §10 for what Z-6 inherits). Spike: `docs/research/2026-10-03-virtual-cube-spike.md` and its `soak.py` (append + trim) and `q7_conn.py` (connection → libraries). ADR 0022. Epic #83, issue #90, and the three comments on #90 (the first-commit window, the `version` pointer, and the Z-3 inheritances).

## Global Constraints

- **Worktree:** `.claude/worktrees/z4-cube-append`, branch `feat/z4-cube-append`, off `origin/main` at ba6f8df. Never work in the main checkout.
- **Gates:** each task ends green on what it touches. The final task runs `npm run verify` (repo root) and, from `services/pipeline/`, `uv run pytest` and `uv run ruff check .`. No e2e, no dev server, no Docker, no load harness. The real-DB check uses a **throwaway** Postgres in the session scratchpad on TCP `localhost:5499`, never the compose DB (it carries the standing GOES demo).
- **No migration and no DDL** (ADR 0001). Migration 032 is on `main`. If a task seems to need DDL, stop and ask.
- **The pipeline never writes `cube_sinks.updated_at`.** It is the app's optimistic-lock version (#98/#99), read as `updated_at::text`. The pipeline writes only `source_prefixes`, `last_snapshot_id`, `last_appended_at` and `last_error` on `cube_sinks`. DB-gated tests pin this.
- **Repository location:** bucket `settings.staging_bucket`, prefix `assets/{cube_collection_id}/_cube` (`CUBE_ITEM_ID` from `cubes/config.py`). Branch `main`. Every create and open uses `RepositoryConfig.num_updates_per_repo_info_file = 100` (I-143).
- **Virtual chunk containers are bucket prefixes** `s3://{bucket}/`, never `s3://` (ADR 0022 invariant).
- **Sources:** read only through the connection of the reference-mode association that produced the item, after a `resolve_pinned` check on its endpoint (ADR 0022). Always an explicit endpoint: the connection's, or `https://s3.{region}.amazonaws.com`, path style (spec §6.1).
- **Ledger vocabulary** (fixture `cube-append-status.json`, unchanged): statuses `pending | appended | skipped | failed`; skip reasons `late`, `duplicate`, `no_source_connection`, `unsupported_layout`, `source_missing`, `no_datetime`. `failed` reasons are free text.
- **Batch:** at most **50** rows per job, in `(item_datetime, id)` order. Headers are parsed **4 at a time**, each in `asyncio.to_thread`. The Icechunk/zarr write phase runs in one `asyncio.to_thread`. If rows remain, the job re-enqueues itself with `enqueue_cube_append` (same `cube:{id}` lock and queueing lock).
- **Never rebase, never use a conflict solver.** On `ConflictError`, redo once from the new tip, then fail the job (retry).
- **Always pass `last_updated_at`** = the source object's LastModified, taken by a HEAD **before** the parse (spike Q5).
- **No byte deletion.** This slice deletes no object anywhere. Resetting a provisional branch to the root snapshot only moves a ref; the orphaned snapshot is Icechunk garbage for Z-6.
- **Logging:** data in `extra={...}`, never interpolated (backend-invariants).
- **Commit trailer:** every commit message ends with
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Decisions this plan takes (flag in the PR)

1. **More modules than #90's file list.** It lists `cubes/source.py` and `cubes/append.py`. This plan adds `cubes/steps.py`, `cubes/icerepo.py`, `cubes/write.py` and `cubes/resolve.py`, so each piece has one job and its own tests. `append.py` stays the entry point.
2. **`obspec-utils` becomes a direct dependency.** VirtualiZarr 2.7.3 deprecates re-exporting `ObjectStoreRegistry` and names `obspec_utils.registry` as the home. It is already a transitive dependency, so the image doesn't grow.
3. **Signed sources are refused in the writer too** (`failed` with reason `SourceConnectionError: signed_source_unsupported`), not decrypted. The sink PUT already refuses them (422) and §13 limits v1 to anonymous prefixes. A connection flipped to signed after the PUT would otherwise give the cube references the cube server can't read.
4. **obstore dials the pinned endpoint, and the Icechunk container keeps the hostname.** A plaintext endpoint is rewritten to its validated IP for obstore, which is what `S3Adapter` and `pinned_endpoint_url` already do. The container config is persisted in the repository and only readers dial it, so it records the hostname. Always path style: the host that `resolve_pinned` vets is then the host obstore dials.
5. **LastModified comes from one HEAD per row** (`obstore.head_async`), because `ingest_files` records only an etag fingerprint. The HEAD runs **before** the parse. A rewrite after the HEAD then makes later reads fail loudly; a HEAD after the parse could pair new bytes with old offsets.
6. **The first-commit race (#90) and crash recovery share one rule: an unrecorded repository is provisional.** While `cube_sinks.last_snapshot_id` is NULL, a job that finds data in the repository resets `main` to the root snapshot (compare-and-swap on the tip) before writing. It replaces the container set with exactly the current sources. Its first `record_commit` applies only `WHERE updated_at = <version read> AND last_snapshot_id IS NULL` (#90 option 1). If that updates 0 rows and the reloaded sink still has no snapshot, an app write won the race. The rows then stay pending and the job re-enqueues, so the next job rebuilds under the new config. Nothing is deleted. The same rule covers a crash between commit and record: those rows are still pending, so resetting loses nothing.
7. **Outcomes.**
   - A `t` already in the cube → `appended` with reason `duplicate` and the current snapshot (spec §6.2: "appended (duplicate, no-op)").
   - `t` ≤ tip → `skipped: late`.
   - A layout mismatch, or a file the step builder rejects → `skipped: unsupported_layout`.
   - The object is missing at HEAD or parse → `skipped: source_missing`.
   - No reference hrefs → `skipped: source_missing`.
   - No association claims the href → `skipped: no_source_connection`.
   - `EgressBlocked`, a refused connection, or any other parse or write error → `failed` with `"{ExceptionType}: {message}"` (≤ 500 chars).
   - An error on one file never fails the batch.
8. **Which source file is an item's header:** exactly one of the item's reference source hrefs whose filename ends `.nc`, `.nc4`, `.h5`, `.hdf5` or `.he5`. No hrefs at all → `source_missing`; zero or several HDF hrefs → `unsupported_layout`.
9. **Crash-loop protection on the ledger (#90 comment).** `take_pending` bumps `attempts` on every row it claims, before anything is parsed. Rows whose `attempts` exceed **6** (`MAX_ROW_ATTEMPTS`) are failed `crash_loop` without being parsed. **Only real crashes count:** any exception that reaches `run_cube_append` gives the attempts back (`CubeRepo.release_rows`), records `last_error` and re-raises. Without that, a platform-store outage of a few minutes would push the oldest 50 rows to `crash_loop` (3 job tries plus 3 from the next kick). A worker that is SIGKILLed, OOM-killed, segfaulted or requeued as stalled never reaches that handler, so its attempts still count. A deterministic bug therefore leaves the rows pending and the sink's `last_error` set, rather than failing them. Retry is `RetrySpec(max_attempts=3, wait_seconds=30)`. `RetrySpec` has no exponential form, and Procrastinate's `attempts` is shared with the stall cap (3).
10. **Z-3's stub rows are revisited.** `cube_kick` first returns every `failed` / `not_implemented` row to `pending` with `attempts = 0` (`CubeRepo.reset_stub_failures`). Their `created_at` is old, so the same tick wakes their sinks. **The lead's call (#90 allows either), flagged in the PR:** this is a whole-table `UPDATE` every 5 minutes, forever, for a one-time leftover. The alternative is to drop it and state that no sink may exist before Z-4 ships.
11. **A step whose write raises is failed and the batch is redone without it** in a fresh session (the half-written session is dropped, never committed).
12. **`window.max_age` on a non-datetime `append_dim` is ignored by the trim** (only `max_steps` applies). **Rows whose `item_datetime` is already older than `now − max_age` are skipped `late` before they are resolved or parsed.** After a long outage, every 50-row job would otherwise parse 50 headers, append them, trim the cube to empty, and commit, again and again. This cutoff uses the item's datetime (GOES: scan start), not `t` (scan midpoint), so a step straddling the cutoff can be skipped; that is one step at the window's edge. A window may trim the cube to **zero** steps, the same thing §10's age trim does to a stopped source. The next append still works (verified on icechunk 2.3.0).
13. **A disabled or deleted sink's job does nothing.** A disabled sink's rows wait, and `cube_kick` wakes them once re-enabled. A missing `CREDENTIALS_MASTER_KEY` makes the job a logged no-op (`load_key_or_skip`), and the rows wait.
14. **`record_commit` writes `source_prefixes`** = the repository config's container prefixes whenever the tip or that set differs from the sink row. It also clears `last_error`.
15. **Double-run residuals (accepted, recorded as I-144).**
    - If a stalled-job requeue runs a second writer during a provisional first append, ledger rows can name a snapshot the other run reset away.
    - After the first commit, `record_commit` is unconditional, so in a double run the slower recorder can move `last_snapshot_id` back to an older snapshot until the next commit.
    - Rows appended and trimmed in the same commit read `appended` with a snapshot that no longer holds them. After crash recovery, such rows read `late`.
    - A sink deleted while its repository is provisional leaves the repository in storage until the cube collection is deleted.
    In every case the cube's data is correct.
16. **The layout check covers the grid, not only the arrays.**
    - Each step's non-time loadable variables (`x`, `y`, the grid mapping) must equal the cube's: decoded values compared exactly, plus the attributes of scalar variables (the grid mapping) in canonical JSON. A mismatch → `skipped: unsupported_layout`. This is proven necessary: on icechunk 2.3.0 / VirtualiZarr 2.7.3, an append **overwrites** the cube's `x`/`y` with the new step's values, so a sector or satellite change would silently re-georeference every earlier step.
    - A step array whose time chunk is not 1 is a layout error (spec §6.2 step 5).
    - `_trim` refuses to shift an array whose time chunk is not 1 (`RuntimeError`), because it shifts by chunks.
17. **The Z-5 hook is `after_batch`, not "after commit".** It is awaited after **every** batch that reached the repository: the ledger is written and the tip is recorded, or was already recorded. That includes a batch that committed nothing (duplicates or lates after a crash), so the collection asset converges after a crash between commit and finish. Z-5's writer must therefore be idempotent. If the hook itself raises, the job retries. Its rows are already finished, so the asset catches up at the next batch (≤ 5 min at the GOES cadence).

## Review Focus

1. **A backlog bigger than the window after downtime** (hundreds of pending rows, `max_steps` 72): repeated 50-row jobs must converge to exactly the newest window, with no duplicate `t` (Task 9, "a backlog larger than the window converges").
2. **One unreadable file in a batch** (a transient NODD error, a corrupt header): that row is `failed` with the error text and the other steps still commit (Task 9, "one bad file fails only its row").
3. **A source that stops past `max_age` and later resumes:** the cube trims to zero steps and the next file appends normally (Task 7, "a cube trimmed to empty still appends").
4. **The sink is deleted or disabled while a job runs:** the job must end quietly without raising or writing ledger errors, and must not leave the repository recorded against a sink that no longer exists (Task 9, "a sink deleted mid-job").
5. **The platform store (Silo) is down for a few minutes:** jobs fail and retry, but no row may end `crash_loop`. When the store returns, the rows append normally (Task 9, "a storage outage hands the attempts back").
6. **A step from another grid** (sector or satellite-position change, same array shapes) must be skipped `unsupported_layout`, and must not rewrite the cube's `x`/`y` (Task 7, "a step on another grid").

---

## File Structure

| File | Responsibility |
|---|---|
| `services/pipeline/pyproject.toml`, `uv.lock` | cube dependencies (Task 1) |
| `src/pipeline/connections/sources.py` (new) | `association_for_href`: href → the reference association, adapter and key (Task 2) |
| `src/pipeline/process/staging.py` | `build_remote_fetcher` uses `association_for_href` (Task 2) |
| `src/pipeline/cubes/source.py` (new) | `SourceLibs`, `libs_from_connection`, `explicit_endpoint` (Task 3) |
| `src/pipeline/cubes/repo.py` | `CubeSink`, `PendingRow`, `RowOutcome`; `load_sink`, `take_pending`, `finish_rows`, `has_pending`, `record_commit`, `record_error`, `reset_stub_failures` (Task 4); `fail_pending` removed (Task 10) |
| `src/pipeline/cubes/steps.py` (new) | `parse_header`, `build_step`, `step_value`, `ArraySpec`, `step_specs`, `check_layout`, `trim_count`, `LayoutError` (Task 5) |
| `src/pipeline/cubes/icerepo.py` (new) | `cube_storage`, `open_repository`, `CubeState`, `read_state`, `reset_to_root` (Task 6) |
| `src/pipeline/cubes/write.py` (new) | `ParsedStep`, `BatchResult`, `classify`, `write_batch`, the `write_step` / `commit_session` seams (Task 7) |
| `src/pipeline/cubes/resolve.py` (new) | `SourceResolver`, `PgSourceResolver`, `ResolvedSource`, `SourceUnavailable`, `pick_hdf_href` (Task 8) |
| `src/pipeline/cubes/append.py` (new) | `AppendDeps`, `AppendReport`, `run_cube_append` (Task 9) |
| `src/pipeline/jobs/cubes.py` | the real handler, `CUBE_APPEND_RETRY`, `production_append_deps`, stub rows reset in `cube_kick` (Task 10) |
| `tests/_cube_fake.py` | fake sink rows, ledger ids, Z-4 methods, one-shot failure hooks (Task 4) |
| `tests/_cube_sources.py` (new) | GOES-shaped HDF5 writer, local `SourceLibs`, registry helper (Task 5) |
| `tests/test_cube_deps.py`, `test_connection_sources.py`, `test_cube_source.py`, `test_cube_steps.py`, `test_cube_icerepo.py`, `test_cube_write.py`, `test_cube_resolve.py`, `test_cube_append.py`, `test_cube_it.py` (new) | unit tests per module; `test_cube_it.py` is skipped unless `CUBE_IT=1` |
| `tests/test_integration_cubes_repo.py` | DB-gated tests of the new SQL (Task 4; the `fail_pending` test goes in Task 10) |
| `tests/test_cube_jobs.py` | the stub test replaced; retry, wiring and stub reset tests (Task 10) |
| `docs/FEATURES.md`, `docs/ISSUES.md` | Z-4 row; the double-run residual (Task 11) |

All `src/` and `tests/` paths are under `services/pipeline/`. Run every `uv` command from `services/pipeline/`.

---

### Task 1: Dependencies

**Files:**
- Modify: `services/pipeline/pyproject.toml`, `services/pipeline/uv.lock`
- Test: `services/pipeline/tests/test_cube_deps.py`

**Interfaces:**
- Produces: importable `icechunk`, `virtualizarr` (with the HDF parser), `zarr`, `xarray`, `obstore`, `h5py`, `obspec_utils`.

- [ ] **Step 1: Write the failing test**

`tests/test_cube_deps.py`:

```python
"""Z-4 dependencies (spec §11): the cube libraries import and round-trip."""

import icechunk as ic
import numpy as np
import zarr


def test_cube_libraries_import():
    import h5py  # noqa: F401
    import obstore  # noqa: F401
    import xarray  # noqa: F401
    from obspec_utils.registry import ObjectStoreRegistry  # noqa: F401
    from virtualizarr import open_virtual_dataset  # noqa: F401
    from virtualizarr.parsers import HDFParser  # noqa: F401

    assert ic.__version__.split(".")[0] == "2"
    assert zarr.__version__.split(".")[0] == "3"


def test_icechunk_round_trips_in_memory():
    repo = ic.Repository.create(ic.in_memory_storage())
    session = repo.writable_session("main")
    array = zarr.group(store=session.store).create_array("a", shape=(2,), dtype="i4")
    array[:] = np.array([1, 2], dtype="i4")
    snapshot = session.commit("one")
    assert repo.lookup_branch("main") == snapshot
    readback = zarr.open_group(repo.readonly_session("main").store, mode="r")
    assert readback["a"][:].tolist() == [1, 2]
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_cube_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'icechunk'`.

- [ ] **Step 3: Add the dependencies**

```bash
uv add 'icechunk>=2.2.2,<3' 'virtualizarr[hdf]>=2.7.3,<3' 'zarr>=3.4,<4' \
  'obstore>=0.11.1' 'h5py>=3.16.0' 'xarray>=2026.7.0' 'obspec-utils>=0.9.0'
```

Then add `"h5py"` and `"icechunk"` to `[tool.uv] no-build-package` next to `"rasterio"` (`no-build-package = ["rasterio", "h5py", "icechunk"]`), so a missing wheel fails at lock time, not in the image build. Re-run `uv lock`, and restore the extras that `uv add` dropped by exact-syncing:

```bash
uv lock && uv sync --extra dev --extra stactools
```

Expected: `uv` resolves (about 150 packages, up from 133) and adds the seven lines to `[project] dependencies`. rasterio 1.5.0 and numpy 2.5.1 are unchanged. A dry run on 2026-10-07 resolved icechunk 2.3.0, virtualizarr 2.7.3, zarr 3.4.0, xarray 2026.7.0, obstore 0.11.1, h5py 3.16.0, numpy 2.5.1. If the resolver reports a conflict with an existing pin (rasterio, the `stactools` extra), stop and report it; don't loosen another pin. The Dockerfile needs no change: every new package ships manylinux wheels, and h5py bundles libhdf5.

- [ ] **Step 4: Run the tests to see them pass, and run the whole suite**

Run: `uv run pytest tests/test_cube_deps.py -v && uv run pytest -q`
Expected: both new tests PASS, and the full suite is still green. The new numpy, xarray and zarr must not break the raster or stactools tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/test_cube_deps.py
git commit -m "Z-4: add the Icechunk/VirtualiZarr cube dependencies (spec §11)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `connections/sources.py` — the shared href → association rule

**Files:**
- Create: `services/pipeline/src/pipeline/connections/sources.py`
- Modify: `services/pipeline/src/pipeline/process/staging.py:76-104`
- Test: `services/pipeline/tests/test_connection_sources.py`

**Interfaces:**
- Produces:
  - `SourceMatch(association: IngestAssociation, adapter: StorageAdapter, key: str)` (frozen dataclass; `key` is percent-decoded).
  - `association_for_href(href: str, associations: Iterable[IngestAssociation], master_key: bytes, allow_hosts: frozenset[str]) -> SourceMatch | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_connection_sources.py`:

```python
"""association_for_href: the ADR 0018 rule shared by staging and the cube writer."""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.repo import ConnectionRow
from pipeline.connections.sources import association_for_href
from pipeline.ingest.repo import IngestAssociation

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
NODD = {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}
HREF = "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-CMIPC/2026/276/17/OR%20x.nc"
NONE = frozenset()


def _assoc(
    assoc_id: str,
    *,
    config: dict | None = None,
    mode: str = "reference",
    protocol: str = "s3",
    sealed: bool = True,
) -> IngestAssociation:
    conn = ConnectionRow(
        id=f"c-{assoc_id}",
        name=assoc_id,
        protocol=protocol,
        config=config or NODD,
        # An anonymous connection stores an EMPTY envelope (G-1), never NULL.
        credentials=seal("{}", KEY) if sealed else None,
        host_key=None,
    )
    return IngestAssociation(
        id=assoc_id, collection_id="src", config={"storage_mode": mode}, connection=conn
    )


def test_matches_the_reference_association_and_decodes_the_key():
    match = association_for_href(HREF, [_assoc("a")], KEY, NONE)
    assert match is not None
    assert match.association.id == "a"
    assert match.key == "ABI-L2-CMIPC/2026/276/17/OR x.nc"


def test_copy_mode_associations_never_match():
    assert association_for_href(HREF, [_assoc("a", mode="copy")], KEY, NONE) is None


def test_an_unbuildable_association_is_passed_over():
    found = association_for_href(HREF, [_assoc("broken", sealed=False), _assoc("ok")], KEY, NONE)
    assert found is not None and found.association.id == "ok"


def test_another_bucket_does_not_match():
    other = _assoc("a", config={"bucket": "noaa-goes18", "region": "us-east-1", "anonymous": True})
    assert association_for_href(HREF, [other], KEY, NONE) is None


def test_a_path_style_custom_endpoint_matches():
    silo = _assoc(
        "a",
        config={
            "bucket": "src",
            "endpoint": "http://minio:9000",
            "force_path_style": True,
            "anonymous": True,
        },
    )
    match = association_for_href("http://minio:9000/src/goes/a.nc", [silo], KEY, NONE)
    assert match is not None and match.key == "goes/a.nc"


async def test_remote_fetcher_reads_through_the_owning_association(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter
    from pipeline.process import staging

    assoc = _assoc("a")

    class _Repo:
        def __init__(self, database_url: str) -> None:
            pass

        async def list_enabled_ingest_associations(self):
            return [assoc]

    got: list[str] = []

    async def fake_get(self, path: str) -> bytes:
        got.append(path)
        return b"bytes"

    monkeypatch.setattr(staging, "PgIngestRepo", _Repo)
    monkeypatch.setattr(S3Adapter, "get", fake_get)
    fetch = staging.build_remote_fetcher(Settings.from_env(env={}), KEY)
    assert await fetch(HREF) == b"bytes"
    assert got == ["ABI-L2-CMIPC/2026/276/17/OR x.nc"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_connection_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.connections.sources'`.

- [ ] **Step 3: Write `connections/sources.py`**

```python
"""Which reference-mode ingest association produced an href (ADR 0018).

A reference-mode item's source href is the source object's stable URL, built
by the producing connection's adapter (``public_object_url``). The enabled
``storage_mode: reference`` association whose connection's object-URL base
prefixes the href owns it, and its credentials, anonymity and egress policy
come with it. Shared by process-run staging (``process/staging.py``) and the
cube writer (``cubes/resolve.py``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.ingest.repo import IngestAssociation


@dataclass(frozen=True)
class SourceMatch:
    association: IngestAssociation
    adapter: StorageAdapter
    #: the object key under the adapter's base URL, percent-decoded
    key: str


def association_for_href(
    href: str,
    associations: Iterable[IngestAssociation],
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> SourceMatch | None:
    """The first reference association whose object-URL base prefixes
    ``href``, or ``None``. An association whose adapter cannot be built is
    passed over, as is any protocol without stable object URLs."""
    for assoc in associations:
        if (assoc.config or {}).get("storage_mode") != "reference":
            continue
        try:
            adapter = build_adapter(assoc.connection, master_key, allow_hosts)
        except AdapterBuildError:
            continue
        try:
            base = adapter.public_object_url("")
        except NotImplementedError:
            # Only S3 publishes stable object URLs (reference mode is
            # s3-only); other protocols cannot have produced the href.
            continue
        if href.startswith(base):
            return SourceMatch(assoc, adapter, unquote(href[len(base) :]))
    return None
```

- [ ] **Step 4: Make staging use it**

In `process/staging.py`, replace the body of `fetch` inside `build_remote_fetcher` with:

```python
    async def fetch(href: str) -> bytes:
        if master_key is not None:
            match = association_for_href(
                href,
                await repo.list_enabled_ingest_associations(),
                master_key,
                settings.egress_allow_hosts,
            )
            if match is not None:
                return await match.adapter.get(match.key)
        return await asyncio.to_thread(fetch_public_url, href, settings.egress_allow_hosts)
```

Add `from pipeline.connections.sources import association_for_href` and remove the imports this leaves unused: `AdapterBuildError`, `build_adapter`, and `unquote` if nothing else in the file uses them (`uv run ruff check src/pipeline/process/staging.py` says which).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_connection_sources.py tests/test_process_staging.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/connections/sources.py src/pipeline/process/staging.py tests/test_connection_sources.py
git commit -m "Z-4: share the href -> reference association rule (connections/sources.py)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `cubes/source.py` — a connection, configured for both libraries

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/source.py`
- Test: `services/pipeline/tests/test_cube_source.py`

**Interfaces:**
- Consumes: `parse_s3_config` (`connections/adapters/s3.py`), `pinned_endpoint_url(endpoint, region, allow_hosts) -> str | None` (`storage/platform.py`), `ConnectionRow`.
- Produces:
  - `SourceLibs(prefix: str, registry_key: str, store, container_store, credentials)` (frozen) with `.url(key) -> str`.
  - `explicit_endpoint(endpoint: str | None, region: str | None) -> str`.
  - `libs_from_connection(connection: ConnectionRow, allow_hosts: frozenset[str]) -> SourceLibs`. It raises `SourceConnectionError` for a non-s3, unparseable or signed connection, and `EgressBlocked` for a refused endpoint.
  - `SourceConnectionError(Exception)`, `REASON_SIGNED_SOURCE = "signed_source_unsupported"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cube_source.py`:

```python
"""libs_from_connection: explicit endpoint, path style, egress-pinned (spec §6.1)."""

from __future__ import annotations

import pytest

from pipeline.connections.egress import EgressBlocked
from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.source import (
    REASON_SIGNED_SOURCE,
    SourceConnectionError,
    explicit_endpoint,
    libs_from_connection,
)
from pipeline.storage import platform

NODD = {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}
NONE: frozenset[str] = frozenset()


def _conn(config: dict | None = None, *, protocol: str = "s3") -> ConnectionRow:
    return ConnectionRow(
        id="c1", name="nodd", protocol=protocol, config=config or NODD,
        credentials=None, host_key=None,
    )


@pytest.fixture
def vetted(monkeypatch) -> list[str]:
    """resolve_pinned without DNS: public hosts pin to a TEST-NET address,
    ``*.internal`` is blocked, allowlisted hosts return the no-pin sentinel."""
    calls: list[str] = []

    def fake(host: str, allow_hosts=()) -> list[str]:
        calls.append(host)
        if host in set(allow_hosts):
            return []
        if host.endswith(".internal"):
            raise EgressBlocked(f"egress to {host} is blocked")
        return ["203.0.113.7"]

    monkeypatch.setattr(platform, "resolve_pinned", fake)
    return calls


def test_explicit_endpoint():
    assert explicit_endpoint(None, "us-west-2") == "https://s3.us-west-2.amazonaws.com"
    assert explicit_endpoint(None, None) == "https://s3.us-east-1.amazonaws.com"
    assert explicit_endpoint("http://minio:9000/", None) == "http://minio:9000"


def test_an_aws_source_gets_an_explicit_regional_path_style_endpoint(vetted):
    libs = libs_from_connection(_conn(), NONE)
    assert vetted == ["s3.us-east-1.amazonaws.com"]  # the host checked is the host dialled
    assert libs.prefix == "s3://noaa-goes19/"
    assert libs.registry_key == "s3://noaa-goes19"
    assert libs.url("/ABI/x.nc") == "s3://noaa-goes19/ABI/x.nc"
    assert libs.store.config["endpoint"] == "https://s3.us-east-1.amazonaws.com"
    assert libs.store.config["virtual_hosted_style_request"] == "false"
    assert libs.store.config["skip_signature"] == "true"
    container = str(libs.container_store)
    assert 'endpoint_url: "https://s3.us-east-1.amazonaws.com"' in container
    assert "force_path_style: True" in container
    assert "anonymous: True" in container


def test_region_defaults_to_us_east_1(vetted):
    libs_from_connection(_conn({"bucket": "b", "anonymous": True}), NONE)
    assert vetted == ["s3.us-east-1.amazonaws.com"]


def test_a_plaintext_endpoint_is_dialled_by_ip_but_persisted_by_name(vetted):
    libs = libs_from_connection(
        _conn({"bucket": "b", "endpoint": "http://silo.example:9000",
               "force_path_style": True, "anonymous": True}),
        NONE,
    )
    assert libs.store.config["endpoint"] == "http://203.0.113.7:9000"
    container = str(libs.container_store)
    assert 'endpoint_url: "http://silo.example:9000"' in container
    assert "allow_http: True" in container


def test_an_allowlisted_compose_endpoint_keeps_its_hostname(vetted):
    libs = libs_from_connection(
        _conn({"bucket": "b", "endpoint": "http://minio:9000", "anonymous": True}),
        frozenset({"minio"}),
    )
    assert libs.store.config["endpoint"] == "http://minio:9000"


def test_a_blocked_endpoint_raises_egress_blocked(vetted):
    with pytest.raises(EgressBlocked):
        libs_from_connection(
            _conn({"bucket": "b", "endpoint": "http://meta.internal", "anonymous": True}), NONE
        )


def test_a_signed_connection_is_refused(vetted):
    with pytest.raises(SourceConnectionError, match=REASON_SIGNED_SOURCE):
        libs_from_connection(_conn({"bucket": "b", "anonymous": False}), NONE)
    assert vetted == []  # refused before any egress check


def test_a_non_s3_connection_is_refused(vetted):
    with pytest.raises(SourceConnectionError, match="s3"):
        libs_from_connection(_conn({"host": "h"}, protocol="sftp"), NONE)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.source'`.

- [ ] **Step 3: Write `cubes/source.py`**

```python
"""A source connection, configured for both cube libraries (virtual cube spec §6.1).

The cube writer reads a NODD header with obstore (VirtualiZarr's parser) and
records a ``VirtualChunkContainer`` for Icechunk. Both come from the S3
connection of the reference-mode association that produced the item.

Always an explicit endpoint, always path style. Without an endpoint obstore
dials the virtual-hosted ``{bucket}.s3.{region}.amazonaws.com`` while the
egress check vets ``s3.{region}.amazonaws.com`` (spike Q7); with path style
the host the check vets is the host obstore dials. obstore gets the PINNED
endpoint: a plaintext endpoint is rewritten to its validated IP, as the S3
adapter does. The Icechunk container keeps the hostname, because it is
persisted in the repository config and only readers dial it.

v1 sources are anonymous (spec §13: the sink PUT refuses signed sources and
the cube server authorizes anonymous prefixes only), so a signed connection is
refused here rather than decrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import icechunk as ic
from obstore.store import S3Store

from pipeline.connections.adapters.s3 import parse_s3_config
from pipeline.connections.repo import ConnectionRow
from pipeline.storage.platform import pinned_endpoint_url

DEFAULT_REGION = "us-east-1"
REASON_SIGNED_SOURCE = "signed_source_unsupported"


class SourceConnectionError(Exception):
    """A connection the cube writer cannot read from (caller-safe message)."""


@dataclass(frozen=True)
class SourceLibs:
    """One source bucket, configured for both libraries."""

    #: the VirtualChunkContainer url_prefix, ``s3://{bucket}/`` (never ``s3://``)
    prefix: str
    #: the ObjectStoreRegistry key VirtualiZarr resolves source URLs against
    registry_key: str
    #: obstore store rooted at the bucket; dials the pinned endpoint
    store: Any
    #: Icechunk ObjectStoreConfig persisted on the container (hostname endpoint)
    container_store: Any
    #: Icechunk credentials that authorize the container when the repo opens
    credentials: Any

    def url(self, key: str) -> str:
        return f"{self.prefix}{key.lstrip('/')}"


def explicit_endpoint(endpoint: str | None, region: str | None) -> str:
    """The connection's endpoint, else AWS's regional endpoint (spec §6.1)."""
    if endpoint:
        return endpoint.rstrip("/")
    return f"https://s3.{region or DEFAULT_REGION}.amazonaws.com"


def libs_from_connection(connection: ConnectionRow, allow_hosts: frozenset[str]) -> SourceLibs:
    """Raises :class:`SourceConnectionError` for a connection the writer cannot
    use, and ``EgressBlocked`` for an endpoint the egress policy refuses."""
    if connection.protocol != "s3":
        raise SourceConnectionError(
            f"cube sources must be s3 connections, not {connection.protocol!r}"
        )
    try:
        cfg = parse_s3_config(connection.config)
    except ValueError as exc:
        raise SourceConnectionError(str(exc)) from exc
    if not cfg.anonymous:
        raise SourceConnectionError(REASON_SIGNED_SOURCE)
    region = cfg.region or DEFAULT_REGION
    endpoint = explicit_endpoint(cfg.endpoint, region)
    dial = pinned_endpoint_url(endpoint, region, allow_hosts) or endpoint
    store = S3Store(
        bucket=cfg.bucket,
        region=region,
        endpoint=dial,
        virtual_hosted_style_request=False,
        skip_signature=True,
        client_options={"allow_http": dial.startswith("http://")},
    )
    container_store = ic.s3_store(
        region=region,
        endpoint_url=endpoint,
        allow_http=endpoint.startswith("http://"),
        anonymous=True,
        force_path_style=True,
    )
    return SourceLibs(
        prefix=f"s3://{cfg.bucket}/",
        registry_key=f"s3://{cfg.bucket}",
        store=store,
        container_store=container_store,
        credentials=ic.s3_credentials(anonymous=True),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cube_source.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cubes/source.py tests/test_cube_source.py
git commit -m "Z-4: build obstore + Icechunk configs from a source connection (spec §6.1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `cubes/repo.py` — the Z-4 SQL seam

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/repo.py`
- Modify: `services/pipeline/tests/_cube_fake.py` (full replacement below)
- Test: `services/pipeline/tests/test_integration_cubes_repo.py` (DB-gated additions), `services/pipeline/tests/test_cube_repo_fake.py` (new)

**Interfaces:**
- Produces (in `pipeline.cubes.repo`):
  - `STUB_REASON_NOT_IMPLEMENTED = "not_implemented"`.
  - `CubeSink(id, source_collection_id, cube_collection_id, enabled: bool, config: dict, source_prefixes: tuple[str, ...], last_snapshot_id: str | None, version: str)`.
  - `PendingRow(id: int, item_id: str, item_datetime: dt.datetime, attempts: int)` (`attempts` is after this take's bump).
  - `RowOutcome(id: int, status: str, reason: str | None = None, snapshot_id: str | None = None)`.
  - `CubeRepo` abstract methods:
    - `load_sink(cube_sink_id) -> CubeSink | None`
    - `take_pending(cube_sink_id, limit) -> list[PendingRow]`
    - `release_rows(cube_sink_id, row_ids) -> int`
    - `finish_rows(cube_sink_id, outcomes) -> int`
    - `has_pending(cube_sink_id) -> bool`
    - `record_commit(cube_sink_id, *, snapshot_id, appended_at, source_prefixes, first_commit_version) -> bool`
    - `record_error(cube_sink_id, message) -> None`
    - `reset_stub_failures() -> int`
- `fail_pending` stays until Task 10 removes it with the stub.

- [ ] **Step 1: Write the failing DB-gated tests**

Append to `tests/test_integration_cubes_repo.py`:

```python
async def test_load_sink_reads_the_app_version_and_the_prefixes(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink_id = await make_sink(_source())
    await conn.execute(
        "UPDATE stac_higher.cube_sinks SET source_prefixes = ARRAY['s3://noaa-goes19/']"
        " WHERE id = %s",
        (sink_id,),
    )
    cur = await conn.execute(
        "SELECT updated_at::text FROM stac_higher.cube_sinks WHERE id = %s", (sink_id,)
    )
    version = (await cur.fetchone())[0]
    repo = PgCubeRepo(DATABASE_URL)
    sink = await repo.load_sink(sink_id)
    assert sink is not None
    assert sink.version == version
    assert sink.source_prefixes == ("s3://noaa-goes19/",)
    assert (sink.config, sink.last_snapshot_id, sink.enabled) == ({}, None, True)
    assert await repo.load_sink(str(uuid.uuid4())) is None


async def test_take_pending_orders_by_item_datetime_and_bumps_attempts(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    _, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [
            LedgerEntry(sink, "late", T0 + dt.timedelta(minutes=10)),
            LedgerEntry(sink, "early", T0),
            LedgerEntry(sink, "mid", T0 + dt.timedelta(minutes=5)),
            LedgerEntry(sink, "done", T0, status="skipped", reason="no_datetime"),
        ]
    )
    taken = await repo.take_pending(sink, 2)
    assert [(r.item_id, r.attempts) for r in taken] == [("early", 1), ("mid", 1)]
    again = await repo.take_pending(sink, 50)
    assert [(r.item_id, r.attempts) for r in again] == [("early", 2), ("mid", 2), ("late", 1)]


async def test_release_rows_undoes_the_take_on_pending_rows_only(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    ids = {r.item_id: r.id for r in await repo.take_pending(sink, 50)}
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    assert await repo.release_rows(sink, list(ids.values())) == 1
    assert [(r[0], r[2], r[4]) for r in await _rows(conn, sink)] == [
        ("a", "pending", 0),
        ("b", "appended", 1),
    ]


async def test_finish_rows_changes_pending_rows_only(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo, RowOutcome

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends([LedgerEntry(sink, "a", T0), LedgerEntry(sink, "b", T0)])
    ids = {r.item_id: r.id for r in await repo.take_pending(sink, 50)}
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'appended'"
        " WHERE cube_sink_id = %s AND item_id = 'b'",
        (sink,),
    )
    changed = await repo.finish_rows(
        sink,
        [
            RowOutcome(ids["a"], "appended", "duplicate", "SNAP"),
            RowOutcome(ids["b"], "skipped", "late"),  # a second writer: no effect
        ],
    )
    assert changed == 1
    cur = await conn.execute(
        "SELECT item_id, status, reason, snapshot_id FROM stac_higher.cube_appends"
        " WHERE cube_sink_id = %s ORDER BY item_id",
        (sink,),
    )
    assert await cur.fetchall() == [
        ("a", "appended", "duplicate", "SNAP"),
        ("b", "appended", None, None),
    ]
    assert not await repo.has_pending(sink)


async def test_record_commit_first_commit_is_conditional_and_never_writes_updated_at(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    loaded = await repo.load_sink(sink)
    kw = {"appended_at": T0, "source_prefixes": ["s3://b/"]}
    await repo.record_error(sink, "boom")
    # An app write moved the version: the first commit loses (#90).
    assert not await repo.record_commit(
        sink, snapshot_id="S0", first_commit_version="2000-01-01 00:00:00+00", **kw
    )
    assert await repo.record_commit(
        sink, snapshot_id="S1", first_commit_version=loaded.version, **kw
    )
    # Only one first commit: a second run that also read NULL loses.
    assert not await repo.record_commit(
        sink, snapshot_id="S2", first_commit_version=loaded.version, **kw
    )
    assert await repo.record_commit(sink, snapshot_id="S3", first_commit_version=None, **kw)
    after = await repo.load_sink(sink)
    assert (after.last_snapshot_id, after.source_prefixes) == ("S3", ("s3://b/",))
    assert after.version == loaded.version  # the pipeline never writes updated_at
    cur = await conn.execute(
        "SELECT last_error, last_appended_at FROM stac_higher.cube_sinks WHERE id = %s",
        (sink,),
    )
    assert await cur.fetchone() == (None, T0)


async def test_record_error_keeps_the_app_version(db):
    from pipeline.cubes.repo import PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    before = (await repo.load_sink(sink)).version
    await repo.record_error(sink, "invalid config: unsupported parser 'grib'")
    cur = await conn.execute(
        "SELECT last_error FROM stac_higher.cube_sinks WHERE id = %s", (sink,)
    )
    assert (await cur.fetchone())[0] == "invalid config: unsupported parser 'grib'"
    assert (await repo.load_sink(sink)).version == before


async def test_reset_stub_failures_returns_z3_stub_rows_to_pending(db):
    from pipeline.cubes.repo import LedgerEntry, PgCubeRepo

    conn, make_sink = db
    sink = await make_sink(_source())
    repo = PgCubeRepo(DATABASE_URL)
    await repo.record_appends(
        [LedgerEntry(sink, "stub", T0), LedgerEntry(sink, "real", T0), LedgerEntry(sink, "p", T0)]
    )
    await conn.execute(
        "UPDATE stac_higher.cube_appends SET status = 'failed', attempts = 1,"
        " reason = CASE item_id WHEN 'stub' THEN 'not_implemented' ELSE 'EgressBlocked: x' END"
        " WHERE cube_sink_id = %s AND item_id IN ('stub', 'real')",
        (sink,),
    )
    assert await repo.reset_stub_failures() >= 1  # whole-table: other sinks may have some
    assert await _rows(conn, sink) == [
        ("p", T0, "pending", None, 0),
        ("real", T0, "failed", "EgressBlocked: x", 1),
        ("stub", T0, "pending", None, 0),
    ]
```

- [ ] **Step 2: Write the failing fake tests**

`tests/test_cube_repo_fake.py`. These pin that the fake behaves like the SQL, because Tasks 9 and 10 lean on it:

```python
"""FakeCubeRepo mirrors PgCubeRepo's Z-4 semantics (pinned by the DB-gated tests)."""

import datetime as dt

from _cube_fake import FakeCubeRepo, FakeSink
from pipeline.cubes.repo import LedgerEntry, RowOutcome

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


async def test_take_finish_and_record_commit():
    repo = FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube", version="v1")])
    await repo.record_appends(
        [LedgerEntry("s1", "b", T0 + dt.timedelta(minutes=5)), LedgerEntry("s1", "a", T0)]
    )
    taken = await repo.take_pending("s1", 50)
    assert [(r.item_id, r.attempts) for r in taken] == [("a", 1), ("b", 1)]
    assert await repo.release_rows("s1", [taken[1].id]) == 1
    assert repo.rows("s1")[1].attempts == 0
    assert await repo.finish_rows("s1", [RowOutcome(taken[0].id, "appended", None, "S1")]) == 1
    assert await repo.has_pending("s1")
    kw = {"appended_at": T0, "source_prefixes": ["s3://b/"]}
    assert not await repo.record_commit("s1", snapshot_id="S1", first_commit_version="v0", **kw)
    assert await repo.record_commit("s1", snapshot_id="S1", first_commit_version="v1", **kw)
    assert not await repo.record_commit("s1", snapshot_id="S2", first_commit_version="v1", **kw)
    sink = await repo.load_sink("s1")
    assert (sink.last_snapshot_id, sink.source_prefixes, sink.version) == ("S1", ("s3://b/",), "v1")


async def test_reset_stub_failures():
    repo = FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube")])
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    row = repo.rows("s1")[0]
    row.status, row.reason, row.attempts = "failed", "not_implemented", 1
    assert await repo.reset_stub_failures() == 1
    assert (row.status, row.reason, row.attempts) == ("pending", None, 0)
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_cube_repo_fake.py -v`
Expected: FAIL with `ImportError: cannot import name 'RowOutcome'`. The DB-gated tests skip without `DATABASE_URL`; they run in Step 7.

- [ ] **Step 4: Add the types and abstract methods to `cubes/repo.py`**

Replace the module docstring's ownership paragraph with:

```
Ownership (ADR 0001): the app owns the DDL (migration 032). The pipeline
reads ``cube_sinks`` and writes ``cube_appends`` rows, plus the
pipeline-owned ``cube_sinks`` columns ``source_prefixes``,
``last_snapshot_id``, ``last_appended_at`` and ``last_error`` (Z-4). It
never writes ``cube_sinks.updated_at``: that column is the app's
optimistic-lock version (#98).
```

Add `from typing import Any` to the imports. Add after `REASON_NO_DATETIME`:

```python
#: Z-3's stub reason. ``reset_stub_failures`` hands those rows back to the
#: real append (#90 comment); nothing writes it any more.
STUB_REASON_NOT_IMPLEMENTED = "not_implemented"
```

After `LedgerEntry`:

```python
@dataclass(frozen=True)
class CubeSink:
    """One ``cube_sinks`` row, as the append reads it."""

    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool
    #: raw jsonb; ``cubes.config.parse_cube_sink_config`` reads it
    config: dict[str, Any]
    source_prefixes: tuple[str, ...]
    last_snapshot_id: str | None
    #: ``updated_at::text``: the app's optimistic-lock version (#98)
    version: str


@dataclass(frozen=True)
class PendingRow:
    id: int
    item_id: str
    item_datetime: dt.datetime
    #: attempts AFTER this take's bump (the crash-loop count, #90)
    attempts: int


@dataclass(frozen=True)
class RowOutcome:
    """A terminal ledger write: ``appended`` / ``skipped`` / ``failed``."""

    id: int
    status: str
    reason: str | None = None
    snapshot_id: str | None = None
```

Abstract methods on `CubeRepo` (after `fail_pending`):

```python
    @abc.abstractmethod
    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:
        """The sink row, or ``None`` when it is gone."""

    @abc.abstractmethod
    async def take_pending(self, cube_sink_id: str, limit: int) -> list[PendingRow]:
        """Up to ``limit`` pending rows in ``(item_datetime, id)`` order, each
        with ``attempts`` bumped by one BEFORE the job parses anything, so a
        row that keeps killing its worker is counted (#90 comment)."""

    @abc.abstractmethod
    async def release_rows(self, cube_sink_id: str, row_ids: Sequence[int]) -> int:
        """Undo ``take_pending``'s bump on rows still ``pending``: the job
        failed with an exception (an outage or a bug), which is not a crash
        loop. Returns the rows changed."""

    @abc.abstractmethod
    async def finish_rows(self, cube_sink_id: str, outcomes: Sequence[RowOutcome]) -> int:
        """Apply terminal outcomes to rows still ``pending``; a double run's
        second writer changes nothing. Returns the rows changed."""

    @abc.abstractmethod
    async def has_pending(self, cube_sink_id: str) -> bool:
        """Whether the sink still holds a ``pending`` row."""

    @abc.abstractmethod
    async def record_commit(
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        """Record the repository tip, its container prefixes and the time, and
        clear ``last_error``. With ``first_commit_version`` set, apply only
        while the row still has that app version and no snapshot: either this
        or a concurrent PUT loses, never both (#90). Never writes
        ``updated_at``. Returns whether the row changed."""

    @abc.abstractmethod
    async def record_error(self, cube_sink_id: str, message: str) -> None:
        """Set ``last_error`` (never ``updated_at``)."""

    @abc.abstractmethod
    async def reset_stub_failures(self) -> int:
        """Return Z-3 stub rows (``failed`` / ``not_implemented``) to
        ``pending`` with ``attempts = 0``. Returns the rows changed."""
```

- [ ] **Step 5: Implement them on `PgCubeRepo`**

```python
    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, source_collection_id, cube_collection_id, enabled,"
                "       config, source_prefixes, last_snapshot_id, updated_at::text"
                "  FROM stac_higher.cube_sinks WHERE id = %s",
                (cube_sink_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return CubeSink(
            id=row[0],
            source_collection_id=row[1],
            cube_collection_id=row[2],
            enabled=bool(row[3]),
            config=dict(row[4] or {}),
            source_prefixes=tuple(row[5] or ()),
            last_snapshot_id=row[6],
            version=row[7],
        )

    async def take_pending(  # pragma: no cover
        self, cube_sink_id: str, limit: int
    ) -> list[PendingRow]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH picked AS ("
                "  SELECT id FROM stac_higher.cube_appends"
                "   WHERE cube_sink_id = %s AND status = 'pending'"
                "   ORDER BY item_datetime, id LIMIT %s FOR UPDATE"
                ")"
                " UPDATE stac_higher.cube_appends a"
                "    SET attempts = a.attempts + 1, updated_at = now()"
                "   FROM picked WHERE a.id = picked.id"
                " RETURNING a.id, a.item_id, a.item_datetime, a.attempts",
                (cube_sink_id, limit),
            )
            rows = await cur.fetchall()
            await conn.commit()
        taken = [PendingRow(id=r[0], item_id=r[1], item_datetime=r[2], attempts=r[3]) for r in rows]
        return sorted(taken, key=lambda r: (r.item_datetime, r.id))

    async def release_rows(  # pragma: no cover
        self, cube_sink_id: str, row_ids: Sequence[int]
    ) -> int:
        if not row_ids:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends"
                "   SET attempts = GREATEST(attempts - 1, 0), updated_at = now()"
                " WHERE cube_sink_id = %s AND id = ANY(%s::bigint[]) AND status = 'pending'",
                (cube_sink_id, list(row_ids)),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed

    async def finish_rows(  # pragma: no cover
        self, cube_sink_id: str, outcomes: Sequence[RowOutcome]
    ) -> int:
        if not outcomes:
            return 0
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends a"
                "   SET status = u.status, reason = u.reason,"
                "       snapshot_id = u.snapshot, updated_at = now()"
                "  FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::text[])"
                "       AS u(id, status, reason, snapshot)"
                " WHERE a.id = u.id AND a.cube_sink_id = %s AND a.status = 'pending'",
                (
                    [o.id for o in outcomes],
                    [o.status for o in outcomes],
                    [o.reason for o in outcomes],
                    [o.snapshot_id for o in outcomes],
                    cube_sink_id,
                ),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed

    async def has_pending(self, cube_sink_id: str) -> bool:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM stac_higher.cube_appends"
                "  WHERE cube_sink_id = %s AND status = 'pending')",
                (cube_sink_id,),
            )
            row = await cur.fetchone()
        return bool(row[0])

    async def record_commit(  # pragma: no cover
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_sinks"
                "   SET last_snapshot_id = %s, last_appended_at = %s,"
                "       source_prefixes = %s::text[], last_error = NULL"
                " WHERE id = %s"
                "   AND (%s::text IS NULL"
                "        OR (updated_at = %s::timestamptz AND last_snapshot_id IS NULL))",
                (
                    snapshot_id,
                    appended_at,
                    list(source_prefixes),
                    cube_sink_id,
                    first_commit_version,
                    first_commit_version,
                ),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed > 0

    async def record_error(self, cube_sink_id: str, message: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.cube_sinks SET last_error = %s WHERE id = %s",
                (message, cube_sink_id),
            )
            await conn.commit()

    async def reset_stub_failures(self) -> int:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.cube_appends"
                "   SET status = 'pending', reason = NULL, attempts = 0, updated_at = now()"
                " WHERE status = 'failed' AND reason = %s",
                (STUB_REASON_NOT_IMPLEMENTED,),
            )
            changed = cur.rowcount
            await conn.commit()
        return changed
```

- [ ] **Step 6: Replace `tests/_cube_fake.py`**

```python
"""In-memory CubeRepo for dispatcher, cube-job and append unit tests.

Mirrors PgCubeRepo (the DB-gated tests pin the SQL; test_cube_repo_fake.py
pins this). One-shot hooks simulate what the SQL cannot show in a unit test:
an app write landing just before ``record_commit``, and a crash inside
``record_commit`` or ``finish_rows``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.cubes.repo import (
    STUB_REASON_NOT_IMPLEMENTED,
    CubeRepo,
    CubeSink,
    CubeSinkRef,
    LedgerEntry,
    PendingRow,
    RowOutcome,
)


@dataclass
class FakeSink:
    id: str
    source_collection_id: str
    cube_collection_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    source_prefixes: tuple[str, ...] = ()
    last_snapshot_id: str | None = None
    #: the app's updated_at::text; tests bump it to simulate a PUT/PATCH
    version: str = "v1"
    last_error: str | None = None
    last_appended_at: dt.datetime | None = None


@dataclass
class FakeLedgerRow:
    cube_sink_id: str
    item_id: str
    item_datetime: dt.datetime
    status: str
    reason: str | None
    attempts: int = 0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    id: int = 0
    snapshot_id: str | None = None


@dataclass
class FakeCubeRepo(CubeRepo):
    sinks: list[FakeSink] = field(default_factory=list)
    #: (cube_sink_id, item_id) -> row, mirroring UNIQUE (cube_sink_id, item_id)
    ledger: dict[tuple[str, str], FakeLedgerRow] = field(default_factory=dict)
    #: enabled_sinks_for_source calls (asserts the per-batch cache)
    sink_calls: int = 0
    #: raise from enabled_sinks_for_source / record_appends when set
    lookup_error: Exception | None = None
    record_error_exc: Exception | None = None
    #: one-shot: runs just before record_commit applies (an app write racing it)
    before_record: Callable[[FakeCubeRepo], None] | None = None
    #: one-shot: raised by record_commit / finish_rows (a worker crash there)
    record_commit_error: Exception | None = None
    finish_error: Exception | None = None
    _next_id: int = 1

    def _sink(self, cube_sink_id: str) -> FakeSink | None:
        return next((s for s in self.sinks if s.id == cube_sink_id), None)

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
        if self.record_error_exc is not None:
            raise self.record_error_exc
        live = {s.id for s in self.sinks if s.enabled}
        inserted = 0
        for e in entries:
            key = (e.cube_sink_id, e.item_id)
            if e.cube_sink_id not in live or key in self.ledger:
                continue
            self.ledger[key] = FakeLedgerRow(
                e.cube_sink_id, e.item_id, e.item_datetime, e.status, e.reason, id=self._next_id
            )
            self._next_id += 1
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

    async def load_sink(self, cube_sink_id: str) -> CubeSink | None:
        s = self._sink(cube_sink_id)
        if s is None:
            return None
        return CubeSink(
            id=s.id,
            source_collection_id=s.source_collection_id,
            cube_collection_id=s.cube_collection_id,
            enabled=s.enabled,
            config=dict(s.config),
            source_prefixes=tuple(s.source_prefixes),
            last_snapshot_id=s.last_snapshot_id,
            version=s.version,
        )

    async def take_pending(self, cube_sink_id: str, limit: int) -> list[PendingRow]:
        rows = sorted(
            (r for r in self.ledger.values()
             if r.cube_sink_id == cube_sink_id and r.status == "pending"),
            key=lambda r: (r.item_datetime, r.id),
        )[:limit]
        for r in rows:
            r.attempts += 1
        return [PendingRow(r.id, r.item_id, r.item_datetime, r.attempts) for r in rows]

    async def release_rows(self, cube_sink_id: str, row_ids: Sequence[int]) -> int:
        wanted = set(row_ids)
        changed = 0
        for r in self.ledger.values():
            if r.cube_sink_id == cube_sink_id and r.id in wanted and r.status == "pending":
                r.attempts = max(r.attempts - 1, 0)
                changed += 1
        return changed

    async def finish_rows(self, cube_sink_id: str, outcomes: Sequence[RowOutcome]) -> int:
        if self.finish_error is not None:
            exc, self.finish_error = self.finish_error, None
            raise exc
        by_id = {r.id: r for r in self.ledger.values() if r.cube_sink_id == cube_sink_id}
        changed = 0
        for o in outcomes:
            row = by_id.get(o.id)
            if row is None or row.status != "pending":
                continue
            row.status, row.reason, row.snapshot_id = o.status, o.reason, o.snapshot_id
            changed += 1
        return changed

    async def has_pending(self, cube_sink_id: str) -> bool:
        return any(
            r.cube_sink_id == cube_sink_id and r.status == "pending" for r in self.ledger.values()
        )

    async def record_commit(
        self,
        cube_sink_id: str,
        *,
        snapshot_id: str,
        appended_at: dt.datetime,
        source_prefixes: Sequence[str],
        first_commit_version: str | None,
    ) -> bool:
        if self.before_record is not None:
            hook, self.before_record = self.before_record, None
            hook(self)
        if self.record_commit_error is not None:
            exc, self.record_commit_error = self.record_commit_error, None
            raise exc
        s = self._sink(cube_sink_id)
        if s is None:
            return False
        if first_commit_version is not None and (
            s.version != first_commit_version or s.last_snapshot_id is not None
        ):
            return False
        s.last_snapshot_id = snapshot_id
        s.last_appended_at = appended_at
        s.source_prefixes = tuple(source_prefixes)
        s.last_error = None
        return True

    async def record_error(self, cube_sink_id: str, message: str) -> None:
        s = self._sink(cube_sink_id)
        if s is not None:
            s.last_error = message

    async def reset_stub_failures(self) -> int:
        changed = 0
        for r in self.ledger.values():
            if r.status == "failed" and r.reason == STUB_REASON_NOT_IMPLEMENTED:
                r.status, r.reason, r.attempts = "pending", None, 0
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

The old fake's `record_error` attribute (an exception for `record_appends`) is renamed `record_error_exc`, because `record_error` is now a `CubeRepo` method. Update any test that sets `repo.record_error = ...`: run `grep -rn "record_error =" tests/` and change each hit to `record_error_exc`.

- [ ] **Step 7: Run the tests, including the DB-gated ones against a throwaway Postgres**

Run: `uv run pytest tests/test_cube_repo_fake.py tests/test_cube_jobs.py tests/test_dispatch_cubes.py -v && uv run ruff check src tests`
Expected: all PASS.

Then the real SQL. Throwaway cluster on TCP 5499; the scratchpad unix-socket path is too long. Run the migrations from the worktree root so `pg` resolves under `app/`:

```bash
SP=<session scratchpad>
initdb -D "$SP/pg" -U postgres --auth=trust >/dev/null
pg_ctl -D "$SP/pg" -o "-p 5499 -c listen_addresses=localhost -k ''" -l "$SP/pg.log" start
createdb -h localhost -p 5499 -U postgres z4
(cd ../.. && DATABASE_URL=postgresql://postgres@localhost:5499/z4 \
  npx tsx -e "import('./app/src/lib/db/migrate.ts').then(m => m.runMigrations()).then(() => process.exit(0))")
DATABASE_URL=postgresql://postgres@localhost:5499/z4 uv run pytest tests/test_integration_cubes_repo.py -v
```

Expected: every test PASSES (none skipped). Leave the cluster running for Task 10, and stop it in Task 11.

- [ ] **Step 8: Commit**

```bash
git add src/pipeline/cubes/repo.py tests/_cube_fake.py tests/test_cube_repo_fake.py tests/test_integration_cubes_repo.py
git commit -m "Z-4: cube repo seam for claiming, finishing and recording appends

take_pending bumps attempts before parsing (crash-loop count); record_commit's
first commit is conditional on the app version read (#90); the pipeline never
writes cube_sinks.updated_at.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

If `grep` found `record_error =` hits in Step 6, add those test files to this commit.

---

### Task 5: `cubes/steps.py` — a header becomes a step, and the layout and window rules

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/steps.py`
- Create: `services/pipeline/tests/_cube_sources.py`
- Test: `services/pipeline/tests/test_cube_steps.py`

**Interfaces:**
- Consumes: `CubeSinkConfig`, `CubeWindow` (`cubes/config.py`), `SourceLibs` (Task 3).
- Produces (in `pipeline.cubes.steps`):
  - `LayoutError(Exception)`.
  - `ArraySpec(shape: tuple[int, ...], chunks: tuple[int, ...], dtype: str)` (frozen; excludes the time axis).
  - `build_step(vds: xr.Dataset, config) -> xr.Dataset`.
  - `parse_header(url: str, registry: ObjectStoreRegistry, config) -> xr.Dataset` (blocking).
  - `step_value(step, append_dim) -> np.generic`.
  - `step_specs(step, config) -> dict[str, ArraySpec]`.
  - `check_layout(step_specs, cube_specs) -> None` (raises `LayoutError`).
  - `StaticSpec(values: np.ndarray, attrs: str | None)` with `.same_as(other) -> bool`.
  - `canonical_attrs(attrs) -> str`, `static_spec(var: xr.DataArray) -> StaticSpec`.
  - `step_statics(step, config) -> dict[str, StaticSpec]`.
  - `check_statics(step_statics, cube_statics) -> None` (raises `LayoutError`).
  - `trim_count(values: np.ndarray, window: CubeWindow | None, now: dt.datetime) -> int`.
- Produces (in `tests/_cube_sources.py`):
  - constants `T0`, `SOURCE_MTIME`, `SOURCE_LAST_MODIFIED`, `GOES_CONFIG`
  - `scan(n) -> dt.datetime`
  - `as_ns(when) -> np.datetime64`
  - `write_goes_file(path, *, when, value=0.0, shape=(4, 6), chunks=(2, 3), variables=("CMI", "DQF"), mtime=SOURCE_MTIME, x0=0.0, perspective_point_height=35786023.0) -> Path`
  - `local_libs(root) -> SourceLibs`
  - `registry_for(*libs) -> ObjectStoreRegistry`

- [ ] **Step 1: Write the test helper `tests/_cube_sources.py`**

```python
"""GOES-shaped HDF5 files on local disk: the fake NODD source for cube tests.

``write_goes_file`` writes what VirtualiZarr's HDFParser reads from a real ABI
L2 CMIP file: chunked ``CMI``/``DQF`` (y, x) arrays with ``x``/``y``
dimension scales, a scalar ``t`` in seconds since J2000 and a scalar
``goes_imager_projection``. Every file gets a whole-second mtime, like S3's
LastModified, because Icechunk compares ``last_updated_at`` to it. A
sub-second mtime fails the read.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import h5py
import icechunk as ic
import numpy as np
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import LocalStore

from pipeline.cubes.source import SourceLibs

J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)
T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)
SOURCE_MTIME = 1_790_000_000
SOURCE_LAST_MODIFIED = dt.datetime.fromtimestamp(SOURCE_MTIME, dt.UTC)
GOES_CONFIG = {
    "parser": "hdf5",
    "append_dim": "t",
    "variables": ["CMI", "DQF"],
    "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
}


def scan(n: int) -> dt.datetime:
    """The n-th 5-minute scan after T0."""
    return T0 + dt.timedelta(minutes=5 * n)


def as_ns(when: dt.datetime) -> np.datetime64:
    """How the cube holds ``t``: naive UTC datetime64[ns]."""
    return np.datetime64(when.astimezone(dt.UTC).replace(tzinfo=None), "ns")


def write_goes_file(
    path,
    *,
    when: dt.datetime,
    value: float = 0.0,
    shape: tuple[int, int] = (4, 6),
    chunks: tuple[int, int] = (2, 3),
    variables: tuple[str, ...] = ("CMI", "DQF"),
    mtime: int = SOURCE_MTIME,
    x0: float = 0.0,
    perspective_point_height: float = 35786023.0,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        y = f.create_dataset("y", data=np.arange(shape[0], dtype="f4") * -1e-4)
        y.make_scale("y")
        x = f.create_dataset("x", data=np.arange(shape[1], dtype="f4") * 1e-4 + x0)
        x.make_scale("x")
        for name in variables:
            dtype = "i1" if name == "DQF" else "f4"
            data = f.create_dataset(name, data=np.full(shape, value, dtype), chunks=chunks)
            data.dims[0].attach_scale(y)
            data.dims[1].attach_scale(x)
        if "CMI" in variables:
            f["CMI"].attrs["grid_mapping"] = "goes_imager_projection"
        t = f.create_dataset("t", data=np.float64((when - J2000).total_seconds()))
        t.attrs["units"] = "seconds since 2000-01-01 12:00:00"
        proj = f.create_dataset("goes_imager_projection", data=np.int32(-2147483647))
        proj.attrs["grid_mapping_name"] = "geostationary"
        proj.attrs["perspective_point_height"] = perspective_point_height
    os.utime(path, (mtime, mtime))
    return path


def local_libs(root) -> SourceLibs:
    """A local directory as a cube source: the same shape as an S3 bucket."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return SourceLibs(
        prefix=f"file://{root}/",
        registry_key=f"file://{root}",
        store=LocalStore(prefix=str(root)),
        container_store=ic.local_filesystem_store(str(root)),
        credentials=ic.credentials.LocalFileSystemAccess,
    )


def registry_for(*libs: SourceLibs) -> ObjectStoreRegistry:
    return ObjectStoreRegistry({lib.registry_key: lib.store for lib in libs})
```

- [ ] **Step 2: Write the failing tests**

`tests/test_cube_steps.py`:

```python
"""Header → step, layout and window rules (spec §6.2 steps 4-6)."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from _cube_sources import GOES_CONFIG, as_ns, local_libs, registry_for, scan, write_goes_file
from pipeline.cubes.config import CubeWindow, parse_cube_sink_config
from pipeline.cubes.steps import (
    ArraySpec,
    LayoutError,
    build_step,
    canonical_attrs,
    check_layout,
    check_statics,
    parse_header,
    step_specs,
    step_statics,
    step_value,
    trim_count,
)

CONFIG = parse_cube_sink_config(GOES_CONFIG)


def _parse(tmp_path, name: str = "a.nc", **kw) -> xr.Dataset:
    write_goes_file(tmp_path / name, when=kw.pop("when", scan(0)), **kw)
    libs = local_libs(tmp_path)
    return parse_header(libs.url(name), registry_for(libs), CONFIG)


def test_a_goes_file_becomes_one_step(tmp_path):
    step = _parse(tmp_path, value=7.0)
    assert dict(step.sizes) == {"t": 1, "y": 4, "x": 6}
    assert step["CMI"].dims == ("t", "y", "x")
    assert step["DQF"].dims == ("t", "y", "x")
    assert step["goes_imager_projection"].dims == ()  # the grid mapping stays a scalar
    assert step_value(step, "t") == as_ns(scan(0))


def test_a_missing_variable_is_a_layout_error(tmp_path):
    with pytest.raises(LayoutError, match="DQF"):
        _parse(tmp_path, variables=("CMI",))


def _plain(t, cmi_dims=("y", "x"), cmi_shape=(2, 3)) -> xr.Dataset:
    return xr.Dataset(
        {
            "CMI": (cmi_dims, np.zeros(cmi_shape, "f4")),
            "DQF": (cmi_dims, np.zeros(cmi_shape, "i1")),
            "goes_imager_projection": ((), np.int32(0)),
        },
        coords={"t": t, "x": np.arange(3.0), "y": np.arange(2.0)},
    )


def test_t_as_a_scalar_coordinate_also_works():
    step = build_step(_plain(as_ns(scan(1))), CONFIG)
    assert step["CMI"].dims == ("t", "y", "x")
    assert step_value(step, "t") == as_ns(scan(1))


def test_a_file_holding_several_times_is_a_layout_error():
    many = _plain([as_ns(scan(0)), as_ns(scan(1))], ("t", "y", "x"), (2, 2, 3))
    with pytest.raises(LayoutError, match="not a scalar"):
        build_step(many, CONFIG)


def test_step_specs_and_the_layout_check(tmp_path):
    specs = step_specs(_parse(tmp_path), CONFIG)
    assert specs == {
        "CMI": ArraySpec((4, 6), (2, 3), "float32"),
        "DQF": ArraySpec((4, 6), (2, 3), "int8"),
    }
    check_layout(specs, specs)
    with pytest.raises(LayoutError, match="CMI"):
        check_layout(specs, {**specs, "CMI": ArraySpec((4, 6), (4, 6), "float32")})
    with pytest.raises(LayoutError, match="DQF"):
        check_layout(specs, {"CMI": specs["CMI"]})


def test_a_step_with_more_than_one_time_per_chunk_is_a_layout_error():
    step = build_step(_plain(as_ns(scan(0))), CONFIG)
    doubled = xr.concat([step, step], dim="t")  # numpy-backed: one chunk of 2 along t
    with pytest.raises(LayoutError, match="time chunk 2"):
        step_specs(doubled, CONFIG)


def test_statics_compare_the_grid_values_and_the_grid_mapping_attrs(tmp_path):
    base = step_statics(_parse(tmp_path, "a.nc"), CONFIG)
    assert set(base) == {"x", "y", "goes_imager_projection"}
    check_statics(base, base)
    shifted = step_statics(_parse(tmp_path, "b.nc", x0=0.5), CONFIG)
    with pytest.raises(LayoutError, match="x differs"):
        check_statics(shifted, base)
    moved = step_statics(_parse(tmp_path, "c.nc", perspective_point_height=1.0), CONFIG)
    with pytest.raises(LayoutError, match="goes_imager_projection differs"):
        check_statics(moved, base)


def test_canonical_attrs_ignores_the_hdf5_array_wrapping():
    hdf5 = {"h": np.array([35786023.0]), "name": np.bytes_(b"geostationary")}
    zarr_json = {"name": "geostationary", "h": 35786023.0}
    assert canonical_attrs(hdf5) == canonical_attrs(zarr_json)


VALUES = np.array([as_ns(scan(i)) for i in range(5)])


def test_trim_by_max_steps():
    assert trim_count(VALUES, CubeWindow(max_steps=3, max_age_seconds=None), scan(4)) == 2


def test_trim_by_max_age_drops_steps_strictly_older_than_the_cutoff():
    # now = T0+20 min, max_age 10 min: the cutoff is T0+10 min (scan 2), kept.
    assert trim_count(VALUES, CubeWindow(max_steps=None, max_age_seconds=600), scan(4)) == 2


def test_trim_takes_the_larger_of_both_rules():
    assert trim_count(VALUES, CubeWindow(max_steps=4, max_age_seconds=600), scan(4)) == 2


def test_no_window_or_no_steps_trims_nothing():
    assert trim_count(VALUES, None, scan(4)) == 0
    empty = np.array([], dtype="datetime64[ns]")
    assert trim_count(empty, CubeWindow(1, 60), scan(4)) == 0


def test_max_age_ignores_an_append_dim_that_is_not_time():
    assert trim_count(np.array([1.0, 2.0]), CubeWindow(None, 60), scan(4)) == 0


def test_a_window_can_trim_every_step():
    assert trim_count(VALUES, CubeWindow(None, 60), scan(10)) == 5
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_cube_steps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.steps'`.

- [ ] **Step 4: Write `cubes/steps.py`**

```python
"""One NODD file → one cube step, and the layout and window rules (spec §6.2 steps 4-6).

``parse_header`` reads only the file's metadata and its loadable variables:
the ``variables`` become virtual references (VirtualiZarr ManifestArrays),
never bytes. It is blocking, so the job runs it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from virtualizarr import open_virtual_dataset  # also registers the .vz accessor
from virtualizarr.parsers import HDFParser

from pipeline.cubes.config import CubeSinkConfig, CubeWindow


class LayoutError(Exception):
    """The file cannot become a step of this cube (``skipped: unsupported_layout``)."""


@dataclass(frozen=True)
class ArraySpec:
    """A time-dimensioned array's shape, chunks and dtype without the time axis."""

    shape: tuple[int, ...]
    chunks: tuple[int, ...]
    dtype: str


def build_step(vds: xr.Dataset, config: CubeSinkConfig) -> xr.Dataset:
    """Select ``variables`` + ``loadable_variables`` and add the append axis.

    A GOES file carries ``t`` as a scalar. Whether the parser yields it as a
    data variable or a coordinate, it becomes the length-1 index of
    ``append_dim``. Every ``variables`` entry gains that axis (time chunk 1).
    The other loadable variables (``x``, ``y``, the scalar grid mapping) are
    kept as they are.
    """
    ds = vds.reset_coords()
    dim = config.append_dim
    wanted = (*config.variables, *config.loadable_variables)
    missing = sorted({n for n in wanted if n not in ds.variables})
    if missing:
        raise LayoutError(f"missing variables: {', '.join(missing)}")
    if ds[dim].ndim != 0:
        raise LayoutError(f"{dim} is not a scalar in this file")
    names = list(dict.fromkeys([*config.variables, dim]))
    step = ds[names].set_coords(dim).expand_dims(dim)
    for name in config.loadable_variables:
        if name != dim and name not in step.variables:
            step[name] = ds[name]
    return step


def parse_header(url: str, registry: ObjectStoreRegistry, config: CubeSinkConfig) -> xr.Dataset:
    """Blocking: run it through ``asyncio.to_thread``."""
    vds = open_virtual_dataset(
        url,
        registry=registry,
        parser=HDFParser(),
        loadable_variables=list(config.loadable_variables),
    )
    return build_step(vds, config)


def step_value(step: xr.Dataset, append_dim: str) -> np.generic:
    """The step's single ``append_dim`` value."""
    return step[append_dim].values[0]


def step_specs(step: xr.Dataset, config: CubeSinkConfig) -> dict[str, ArraySpec]:
    """Raises :class:`LayoutError` for an array whose time chunk is not 1: the
    window shifts by chunks (spec §6.2 step 5)."""
    specs: dict[str, ArraySpec] = {}
    for name in config.variables:
        data = step[name].data
        metadata = getattr(data, "metadata", None)
        chunks = tuple(getattr(metadata, "chunks", None) or data.shape)
        if chunks[0] != 1:
            raise LayoutError(f"{name} has time chunk {chunks[0]}; a cube step needs 1")
        specs[name] = ArraySpec(tuple(data.shape[1:]), chunks[1:], str(step[name].dtype))
    return specs


@dataclass(frozen=True, eq=False)
class StaticSpec:
    """A variable without the append axis (``x``, ``y``, the grid mapping): its
    decoded values and, for a scalar, its attributes in canonical JSON."""

    values: np.ndarray
    attrs: str | None

    def same_as(self, other: StaticSpec) -> bool:
        if self.values.shape != other.values.shape or self.attrs != other.attrs:
            return False
        try:
            return bool(np.array_equal(self.values, other.values, equal_nan=True))
        except TypeError:  # a dtype without NaN (strings, objects)
            return bool(np.array_equal(self.values, other.values))


def canonical_attrs(attrs: Mapping[str, Any]) -> str:
    """Attributes as JSON, so a file's HDF5 attributes compare equal to the
    same attributes after a round trip through the Zarr metadata (numpy to
    plain, bytes to str, a one-element array to its element)."""

    def plain(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            value = value.tolist()
        elif isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, bytes):
            value = value.decode()
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        return value

    return json.dumps({k: plain(v) for k, v in attrs.items()}, sort_keys=True, default=str)


def static_spec(var: xr.DataArray) -> StaticSpec:
    return StaticSpec(np.asarray(var.values), canonical_attrs(var.attrs) if var.ndim == 0 else None)


def step_statics(step: xr.Dataset, config: CubeSinkConfig) -> dict[str, StaticSpec]:
    return {
        name: static_spec(step[name])
        for name in config.loadable_variables
        if name != config.append_dim
    }


def check_statics(step: Mapping[str, StaticSpec], cube: Mapping[str, StaticSpec]) -> None:
    """The step's grid must be the cube's. An append rewrites the cube's
    non-time variables with the step's, so a step from another grid would
    silently re-georeference every earlier step."""
    for name, spec in step.items():
        have = cube.get(name)
        if have is None:
            raise LayoutError(f"{name} is not a variable of this cube")
        if not spec.same_as(have):
            raise LayoutError(f"{name} differs from the cube's (grid or projection changed)")


def check_layout(step: Mapping[str, ArraySpec], cube: Mapping[str, ArraySpec]) -> None:
    """Every step array must match the cube's array of that name: a virtual
    append needs the same chunk grid, shape and dtype."""
    for name, spec in step.items():
        have = cube.get(name)
        if have is None:
            raise LayoutError(f"{name} is not an array of this cube")
        if have != spec:
            raise LayoutError(f"{name} is {spec}; the cube has {have}")


def trim_count(values: np.ndarray, window: CubeWindow | None, now: dt.datetime) -> int:
    """How many of the oldest steps the window drops (spec §6.2 step 6):
    ``max(len - max_steps, steps older than now - max_age)``. ``values`` is
    ascending. ``max_age`` applies only to a datetime ``append_dim``. The
    result may equal ``len(values)``: a cube may be trimmed to zero steps."""
    if window is None or len(values) == 0:
        return 0
    k = 0
    if window.max_steps is not None:
        k = max(k, len(values) - window.max_steps)
    if window.max_age_seconds is not None and np.issubdtype(values.dtype, np.datetime64):
        cutoff = now.astimezone(dt.UTC).replace(tzinfo=None) - dt.timedelta(
            seconds=window.max_age_seconds
        )
        k = max(k, int(np.searchsorted(values, np.datetime64(cutoff, "ns"), side="left")))
    return k
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cube_steps.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/cubes/steps.py tests/_cube_sources.py tests/test_cube_steps.py
git commit -m "Z-4: header -> cube step, layout check and window trim count

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `cubes/icerepo.py` — the repository: storage, open, state, reset

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/icerepo.py`
- Test: `services/pipeline/tests/test_cube_icerepo.py`

**Interfaces:**
- Consumes: `SourceLibs` (Task 3), `ArraySpec` (Task 5), `platform_s3_access(settings)` (`storage/platform.py`), `CUBE_ITEM_ID` (`cubes/config.py`).
- Produces (in `pipeline.cubes.icerepo`):
  - `BRANCH = "main"`, `REPO_INFO_UPDATES = 100`.
  - `cube_prefix(cube_collection_id) -> str` → `"assets/{c}/_cube"`.
  - `cube_storage(settings, cube_collection_id) -> ic.Storage`.
  - `open_repository(storage, libs: Sequence[SourceLibs], *, replace_containers: bool) -> ic.Repository`.
  - `CubeState(values: np.ndarray, specs: dict[str, ArraySpec], time_arrays: tuple[str, ...], initialised: bool, statics: dict[str, StaticSpec])`.
  - `read_state(session, append_dim) -> CubeState`.
  - `reset_to_root(repo, *, from_snapshot_id: str) -> None` (raises `ic.ConflictError` if the branch moved).

- [ ] **Step 1: Write the failing tests**

`tests/test_cube_icerepo.py`:

```python
"""The cube's Icechunk repository (spec §6.2 steps 2-3, I-143)."""

from __future__ import annotations

import icechunk as ic
import pytest

from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.config import Settings
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import (
    BRANCH,
    REPO_INFO_UPDATES,
    cube_prefix,
    cube_storage,
    open_repository,
    read_state,
    reset_to_root,
)
from pipeline.cubes.steps import ArraySpec, check_statics, parse_header, step_statics

CONFIG = parse_cube_sink_config(GOES_CONFIG)


def _append(repo, libs, root, name, n, *, first: bool) -> str:
    write_goes_file(root / name, when=scan(n))
    step = parse_header(libs.url(name), registry_for(libs), CONFIG)
    session = repo.writable_session(BRANCH)
    step.vz.to_icechunk(
        session.store, append_dim=None if first else "t", last_updated_at=SOURCE_LAST_MODIFIED
    )
    return session.commit(f"append {name}")


def test_create_sets_the_history_setting_and_the_source_container(tmp_path):
    storage, libs = ic.in_memory_storage(), local_libs(tmp_path)
    open_repository(storage, [libs], replace_containers=False)
    cfg = ic.Repository.fetch_config(storage)
    assert cfg.num_updates_per_repo_info_file == REPO_INFO_UPDATES == 100
    assert list(cfg.virtual_chunk_containers) == [libs.prefix]


def test_open_adds_a_new_source_container_and_keeps_the_old(tmp_path):
    storage = ic.in_memory_storage()
    a, b = local_libs(tmp_path / "a"), local_libs(tmp_path / "b")
    open_repository(storage, [a], replace_containers=False)
    open_repository(storage, [b], replace_containers=False)
    assert set(ic.Repository.fetch_config(storage).virtual_chunk_containers) == {a.prefix, b.prefix}


def test_replace_containers_keeps_exactly_the_given_sources(tmp_path):
    storage = ic.in_memory_storage()
    a, b = local_libs(tmp_path / "a"), local_libs(tmp_path / "b")
    open_repository(storage, [a], replace_containers=False)
    open_repository(storage, [b], replace_containers=True)
    assert list(ic.Repository.fetch_config(storage).virtual_chunk_containers) == [b.prefix]


def test_a_new_repository_is_uninitialised(tmp_path):
    repo = open_repository(ic.in_memory_storage(), [local_libs(tmp_path)], replace_containers=False)
    state = read_state(repo.readonly_session(BRANCH), "t")
    assert (state.initialised, len(state.values), state.specs, state.time_arrays) == (
        False, 0, {}, ()
    )


def test_state_after_a_write(tmp_path):
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    _append(repo, libs, tmp_path, "a.nc", 0, first=True)
    _append(repo, libs, tmp_path, "b.nc", 1, first=False)
    state = read_state(repo.readonly_session(BRANCH), "t")
    assert state.initialised
    assert list(state.values) == [as_ns(scan(0)), as_ns(scan(1))]
    assert state.time_arrays == ("CMI", "DQF", "t")
    assert state.specs["CMI"] == ArraySpec((4, 6), (2, 3), "float32")
    # The grid read back from the cube equals the grid of the file it came from.
    assert set(state.statics) == {"x", "y", "goes_imager_projection"}
    fresh = parse_header(libs.url("a.nc"), registry_for(libs), CONFIG)
    check_statics(step_statics(fresh, CONFIG), state.statics)


def test_reset_to_root_empties_the_branch_and_is_compare_and_swap(tmp_path):
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    snapshot = _append(repo, libs, tmp_path, "a.nc", 0, first=True)
    root = list(repo.ancestry(branch=BRANCH))[-1].id
    with pytest.raises(ic.ConflictError):
        reset_to_root(repo, from_snapshot_id=root)  # the branch is not at root
    reset_to_root(repo, from_snapshot_id=snapshot)
    assert repo.lookup_branch(BRANCH) == root
    assert not read_state(repo.readonly_session(BRANCH), "t").initialised


def test_cube_storage_points_at_the_reserved_prefix():
    settings = Settings.from_env(
        env={
            "STAGING_BUCKET": "stac-higher",
            "STAGING_S3_ENDPOINT": "http://minio:9000",
            "EGRESS_ALLOW_HOSTS": "minio",
        }
    )
    assert cube_prefix("goes19-c13-cube") == "assets/goes19-c13-cube/_cube"
    text = repr(cube_storage(settings, "goes19-c13-cube"))
    assert "bucket: stac-higher" in text
    assert "prefix: assets/goes19-c13-cube/_cube" in text
    assert "endpoint_url: http://minio:9000" in text
    assert "force_path_style: True" in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_icerepo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.icerepo'`.

- [ ] **Step 3: Write `cubes/icerepo.py`**

```python
"""A cube's Icechunk repository: storage, open/create, state, reset (spec §6.2 steps 2-3).

The repository lives at ``assets/{cube_collection}/_cube/`` in the platform
bucket (ADR 0022). Every create and open sets
``num_updates_per_repo_info_file = 100`` (I-143). Each source bucket is one
``VirtualChunkContainer`` (``s3://{bucket}/``, never ``s3://``), authorized at
open with that source's credentials. Everything here is blocking, so the job
runs it through ``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import icechunk as ic
import numpy as np
import xarray as xr
import zarr
from zarr.errors import GroupNotFoundError

from pipeline.config import Settings
from pipeline.cubes.config import CUBE_ITEM_ID
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import ArraySpec, StaticSpec, static_spec
from pipeline.storage.platform import platform_s3_access

BRANCH = "main"
#: I-143: keeps the ``repo`` object near 10 KB (Icechunk's default is 1,000)
REPO_INFO_UPDATES = 100


def cube_prefix(cube_collection_id: str) -> str:
    return f"assets/{cube_collection_id}/{CUBE_ITEM_ID}"


def cube_storage(settings: Settings, cube_collection_id: str) -> ic.Storage:
    """The platform bucket, egress-pinned like every other platform client."""
    access = platform_s3_access(settings)
    endpoint = access.endpoint_url
    return ic.s3_storage(
        bucket=settings.staging_bucket,
        prefix=cube_prefix(cube_collection_id),
        region=access.region,
        endpoint_url=endpoint,
        allow_http=bool(endpoint and endpoint.startswith("http://")),
        force_path_style=access.force_path_style,
        access_key_id=access.access_key,
        secret_access_key=access.secret_key,
    )


def _config(libs: Sequence[SourceLibs] = ()) -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.num_updates_per_repo_info_file = REPO_INFO_UPDATES
    for lib in libs:
        cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(lib.prefix, lib.container_store))
    return cfg


def open_repository(
    storage: ic.Storage, libs: Sequence[SourceLibs], *, replace_containers: bool
) -> ic.Repository:
    """Open the repository, creating it with ``libs`` as its containers if absent.

    An open repository gains any container it lacks. With
    ``replace_containers`` its containers become exactly ``libs``: the job
    uses that while the repository is provisional, when the app may still
    have changed the source (#98). Either change is saved to the repository
    config."""
    authorize = ic.containers_credentials({lib.prefix: lib.credentials for lib in libs})
    if not ic.Repository.exists(storage):
        return ic.Repository.create(
            storage, _config(libs), authorize_virtual_chunk_access=authorize
        )
    repo = ic.Repository.open(storage, config=_config(), authorize_virtual_chunk_access=authorize)
    known = set(repo.config.virtual_chunk_containers or {})
    wanted = {lib.prefix for lib in libs}
    if (replace_containers and known != wanted) or not wanted <= known:
        cfg = repo.config
        if replace_containers:
            cfg.clear_virtual_chunk_containers()
        for lib in libs:
            cfg.set_virtual_chunk_container(
                ic.VirtualChunkContainer(lib.prefix, lib.container_store)
            )
        repo = repo.reopen(config=cfg, authorize_virtual_chunk_access=authorize)
        repo.save_config()
    return repo


@dataclass(frozen=True)
class CubeState:
    #: ``append_dim`` values, ascending (datetime64[ns] for a time axis)
    values: np.ndarray = field(default_factory=lambda: np.array([], dtype="datetime64[ns]"))
    #: every time-dimensioned array except ``append_dim`` itself
    specs: dict[str, ArraySpec] = field(default_factory=dict)
    #: every array whose first dimension is ``append_dim``, ``append_dim`` included
    time_arrays: tuple[str, ...] = ()
    #: the cube has its ``append_dim`` array (it may hold zero steps)
    initialised: bool = False
    #: every variable without the append axis (``x``, ``y``, the grid mapping)
    statics: dict[str, StaticSpec] = field(default_factory=dict)


def read_state(session: ic.Session, append_dim: str) -> CubeState:
    """The cube as ``session`` sees it: its time values and array layout."""
    try:
        group = zarr.open_group(session.store, mode="r")
    except GroupNotFoundError:
        return CubeState()
    names = sorted(group.array_keys())
    if append_dim not in names:
        return CubeState()
    time_arrays: list[str] = []
    specs: dict[str, ArraySpec] = {}
    for name in names:
        array = group[name]
        dims = array.metadata.dimension_names or ()
        if dims and dims[0] == append_dim:
            time_arrays.append(name)
            if name != append_dim:
                specs[name] = ArraySpec(
                    tuple(array.shape[1:]), tuple(array.chunks[1:]), str(array.dtype)
                )
    # xarray decodes the CF time units; only the small native t array is read.
    ds = xr.open_zarr(session.store, consolidated=False, zarr_format=3, chunks=None)
    return CubeState(
        values=ds[append_dim].values,
        specs=specs,
        time_arrays=tuple(time_arrays),
        initialised=True,
        statics={
            str(name): static_spec(ds[name]) for name in ds.variables if name not in time_arrays
        },
    )


def reset_to_root(repo: ic.Repository, *, from_snapshot_id: str) -> None:
    """Point ``main`` back at the repository's root snapshot, only if it is
    still at ``from_snapshot_id`` (else ``ic.ConflictError``). Moves a ref and
    deletes nothing: the dropped snapshots are garbage for Z-6's GC."""
    root = list(repo.ancestry(branch=BRANCH))[-1].id
    repo.reset_branch(BRANCH, root, from_snapshot_id=from_snapshot_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cube_icerepo.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cubes/icerepo.py tests/test_cube_icerepo.py
git commit -m "Z-4: cube repository storage, open/create, state and root reset

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `cubes/write.py` — one batch, one commit

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/write.py`
- Test: `services/pipeline/tests/test_cube_write.py`

**Interfaces:**
- Consumes: `read_state`, `CubeState`, `BRANCH` (Task 6); `check_layout`, `step_specs`, `trim_count`, `LayoutError` (Task 5).
- Produces (in `pipeline.cubes.write`):
  - `ParsedStep(row_id: int, item_id: str, step: xr.Dataset, value: np.generic, last_modified: dt.datetime)` (frozen).
  - `BatchResult(outcomes: dict[int, tuple[str, str | None]], snapshot_id: str, committed: bool, values: np.ndarray, trimmed: int, initialised: bool)`.
  - `write_batch(repo, parsed: Sequence[ParsedStep], config, now) -> BatchResult` (blocking; raises `ic.ConflictError` after the one redo).
  - `classify(parsed, state, config, exclude=frozenset()) -> tuple[dict[int, Outcome], list[ParsedStep]]`. It checks array layout (`check_layout`) and the grid (`check_statics`) against the cube, or against the batch's first step for a new cube.
  - `error_text(exc) -> str`.
  - Seams patched by tests: `write_step(session, parsed, append_dim)` and `commit_session(session, message) -> str`.
  - Constants: `REASON_DUPLICATE`, `REASON_LATE`, `REASON_UNSUPPORTED_LAYOUT`, `MAX_ERROR_CHARS = 500`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cube_write.py`:

```python
"""write_batch: classify, write, trim and commit once (spec §6.2 steps 5-7)."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import icechunk as ic
import numpy as np
import pytest
import xarray as xr

import pipeline.cubes.write as write_mod
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    SOURCE_MTIME,
    as_ns,
    local_libs,
    registry_for,
    scan,
    write_goes_file,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import ParsedStep, write_batch

PLAIN = parse_cube_sink_config(GOES_CONFIG)
NOW = scan(100)


def _config(**window) -> object:
    return parse_cube_sink_config({**GOES_CONFIG, "window": window})


@dataclass
class Cube:
    root: Path
    libs: SourceLibs
    repo: ic.Repository

    def parsed(self, row_id: int, n: int, *, value: float | None = None, **kw) -> ParsedStep:
        name = f"f{n}-{row_id}.nc"
        pixel = float(n if value is None else value)
        write_goes_file(self.root / name, when=scan(n), value=pixel, **kw)
        step = parse_header(self.libs.url(name), registry_for(self.libs), PLAIN)
        t = step_value(step, "t")
        return ParsedStep(row_id, f"item-{row_id}", step, t, SOURCE_LAST_MODIFIED)

    def write(self, *steps: ParsedStep, config=PLAIN, now: dt.datetime = NOW):
        return write_batch(self.repo, list(steps), config, now)

    def read(self) -> xr.Dataset:
        return xr.open_zarr(
            self.repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3
        )

    def times(self) -> list:
        return list(self.read()["t"].values)

    def pixels(self) -> list[float]:
        return [float(v) for v in self.read()["CMI"].isel(x=0, y=0).values]

    def commits(self) -> int:
        return len(list(self.repo.ancestry(branch=BRANCH))) - 1  # minus the root


@pytest.fixture
def cube(tmp_path) -> Cube:
    libs = local_libs(tmp_path)
    repo = open_repository(ic.in_memory_storage(), [libs], replace_containers=False)
    return Cube(tmp_path, libs, repo)


def test_a_batch_appends_in_time_order_in_one_commit(cube):
    result = cube.write(cube.parsed(2, 2), cube.parsed(1, 0), cube.parsed(3, 1))
    assert result.committed and result.initialised
    assert result.outcomes == {1: ("appended", None), 2: ("appended", None), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(1)), as_ns(scan(2))]
    assert cube.pixels() == [0.0, 1.0, 2.0]
    assert cube.commits() == 1
    assert next(cube.repo.ancestry(branch=BRANCH)).message == "append 3 items: item-1…item-2"
    assert result.snapshot_id == cube.repo.lookup_branch(BRANCH)


def test_a_step_already_present_is_appended_as_a_duplicate(cube):
    first = cube.write(cube.parsed(1, 0))
    # A reprocessed NODD file: a new item, the same scan t, other bytes.
    again = cube.write(cube.parsed(2, 0, value=9.0))
    assert again.outcomes == {2: ("appended", "duplicate")}
    assert (again.committed, again.snapshot_id) == (False, first.snapshot_id)
    assert cube.pixels() == [0.0]


def test_a_step_at_or_before_the_tip_is_skipped_late(cube):
    cube.write(cube.parsed(1, 0), cube.parsed(2, 2))
    result = cube.write(cube.parsed(3, 1))
    assert result.outcomes == {3: ("skipped", "late")}
    assert not result.committed


def test_a_mismatched_layout_is_skipped_and_the_rest_append(cube):
    cube.write(cube.parsed(1, 0))
    result = cube.write(cube.parsed(2, 1, chunks=(4, 6)), cube.parsed(3, 2))
    assert result.outcomes == {2: ("skipped", "unsupported_layout"), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(2))]


def test_a_step_on_another_grid_is_skipped_and_the_cube_grid_kept(cube):
    cube.write(cube.parsed(1, 0))
    before = cube.read()["x"].values.copy()
    result = cube.write(cube.parsed(2, 1, x0=0.5), cube.parsed(3, 2))
    assert result.outcomes == {2: ("skipped", "unsupported_layout"), 3: ("appended", None)}
    # An append rewrites the non-time variables, so this is what the check protects.
    assert np.array_equal(cube.read()["x"].values, before)


def test_a_step_with_another_grid_mapping_is_skipped(cube):
    cube.write(cube.parsed(1, 0))
    result = cube.write(cube.parsed(2, 1, perspective_point_height=35786000.0))
    assert result.outcomes == {2: ("skipped", "unsupported_layout")}


def test_the_first_step_of_a_new_cube_sets_the_layout(cube):
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1, chunks=(4, 6)))
    assert result.outcomes == {1: ("appended", None), 2: ("skipped", "unsupported_layout")}


def test_max_steps_trims_in_the_same_commit(cube):
    result = cube.write(*[cube.parsed(i + 1, i) for i in range(5)], config=_config(max_steps=3))
    assert result.trimmed == 2
    assert cube.times() == [as_ns(scan(2)), as_ns(scan(3)), as_ns(scan(4))]
    assert cube.pixels() == [2.0, 3.0, 4.0]  # the virtual refs moved with t
    assert cube.commits() == 1


def test_max_age_trims_by_now(cube):
    steps = [cube.parsed(i + 1, i) for i in range(5)]
    result = cube.write(*steps, config=_config(max_age="10m"), now=scan(4))
    assert result.trimmed == 2
    assert cube.times() == [as_ns(scan(2)), as_ns(scan(3)), as_ns(scan(4))]


def test_a_cube_trimmed_to_empty_still_appends(cube):
    config = _config(max_age="10m")
    emptied = write_batch(cube.repo, [cube.parsed(1, 0)], config, scan(10))
    assert (emptied.trimmed, emptied.initialised, cube.times()) == (1, True, [])
    resumed = write_batch(cube.repo, [cube.parsed(2, 11)], config, scan(11))
    assert resumed.outcomes == {2: ("appended", None)}
    assert cube.times() == [as_ns(scan(11))]


def test_last_updated_at_is_the_source_last_modified(cube):
    cube.write(cube.parsed(1, 0))
    assert cube.pixels() == [0.0]  # LastModified matches: readable
    source = cube.root / "f0-1.nc"
    os.utime(source, (SOURCE_MTIME + 60, SOURCE_MTIME + 60))  # rewritten after the append
    with pytest.raises(Exception, match="checksum"):
        cube.pixels()


def test_a_conflicting_commit_is_redone_once_from_the_new_tip(cube, monkeypatch):
    cube.write(cube.parsed(1, 0))
    real = write_mod.commit_session
    calls: list[str] = []

    def racing(session, message):
        calls.append(message)
        if len(calls) == 1:  # another writer commits scan 1 first
            rival = cube.parsed(9, 1)
            other = cube.repo.writable_session(BRANCH)
            rival.step.vz.to_icechunk(
                other.store, append_dim="t", last_updated_at=SOURCE_LAST_MODIFIED
            )
            other.commit("rival")
        return real(session, message)

    monkeypatch.setattr(write_mod, "commit_session", racing)
    result = cube.write(cube.parsed(2, 1), cube.parsed(3, 2))
    assert len(calls) == 2
    assert result.outcomes == {2: ("appended", "duplicate"), 3: ("appended", None)}
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(1)), as_ns(scan(2))]


def test_a_second_conflict_propagates(cube, monkeypatch):
    cube.write(cube.parsed(1, 0))
    real = write_mod.commit_session
    calls: list[str] = []

    def always_racing(session, message):
        calls.append(message)
        rival = cube.parsed(100 + len(calls), 10 + len(calls))
        other = cube.repo.writable_session(BRANCH)
        rival.step.vz.to_icechunk(
            other.store, append_dim="t", last_updated_at=SOURCE_LAST_MODIFIED
        )
        other.commit("rival")
        return real(session, message)

    monkeypatch.setattr(write_mod, "commit_session", always_racing)
    # The step is newer than every rival (scans 11, 12), so the redo still
    # has something to commit and meets the second conflict.
    with pytest.raises(ic.ConflictError):
        cube.write(cube.parsed(2, 50))
    assert len(calls) == 2  # one redo, never a rebase


def test_a_step_whose_write_fails_is_failed_and_the_rest_commit(cube, monkeypatch):
    real = write_mod.write_step

    def flaky(session, parsed, append_dim):
        if parsed.row_id == 2:
            raise RuntimeError("disk on fire")
        real(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", flaky)
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1), cube.parsed(3, 2))
    assert result.outcomes == {
        1: ("appended", None),
        2: ("failed", "RuntimeError: disk on fire"),
        3: ("appended", None),
    }
    assert cube.times() == [as_ns(scan(0)), as_ns(scan(2))]
    assert cube.commits() == 1


def test_a_failing_first_step_lets_the_next_one_create_the_cube(cube, monkeypatch):
    real = write_mod.write_step

    def flaky(session, parsed, append_dim):
        if parsed.row_id == 1:
            raise OSError("connection reset")
        real(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", flaky)
    result = cube.write(cube.parsed(1, 0), cube.parsed(2, 1))
    assert result.outcomes == {1: ("failed", "OSError: connection reset"), 2: ("appended", None)}
    assert cube.times() == [as_ns(scan(1))]


def test_nothing_to_write_commits_nothing(cube):
    first = cube.write(cube.parsed(1, 1))
    result = cube.write(cube.parsed(2, 0))
    assert (result.committed, result.snapshot_id) == (False, first.snapshot_id)
    assert cube.commits() == 1


def test_error_text_is_bounded():
    text = write_mod.error_text(ValueError("x" * 2000))
    assert text.startswith("ValueError: x") and len(text) == write_mod.MAX_ERROR_CHARS


def test_the_unused_now_does_not_matter_without_a_window(cube):
    result = cube.write(cube.parsed(1, 0), now=dt.datetime(1970, 1, 1, tzinfo=dt.UTC))
    assert result.trimmed == 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_write.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.write'`.

- [ ] **Step 3: Write `cubes/write.py`**

```python
"""Write one batch of parsed steps into a cube in ONE commit (spec §6.2 steps 5-7).

Blocking (Icechunk and zarr), so the job runs it through ``asyncio.to_thread``.

Each attempt works in a fresh session on ``main``:
1. Read the tip.
2. Classify every step: duplicate, late, layout mismatch, or accepted.
3. Write the accepted steps with ``last_updated_at`` = the source's LastModified.
4. Trim the window in the same session.
5. Commit.

A step whose write raises is failed, and the attempt is redone without it in a
new session; the half-written one is dropped. A ``ConflictError`` means someone
committed since the session opened (a double run). It redoes the attempt once
from the new tip, where steps already written read as duplicates. A second
conflict propagates, and the job retries. Never a rebase or a conflict solver
(ADR 0022).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import icechunk as ic
import numpy as np
import xarray as xr
import zarr

from pipeline.cubes.config import CubeSinkConfig
from pipeline.cubes.icerepo import BRANCH, CubeState, read_state
from pipeline.cubes.steps import (
    LayoutError,
    check_layout,
    check_statics,
    step_specs,
    step_statics,
    trim_count,
)

logger = logging.getLogger(__name__)

REASON_DUPLICATE = "duplicate"
REASON_LATE = "late"
REASON_UNSUPPORTED_LAYOUT = "unsupported_layout"
#: a failed row's reason is free text; keep it short enough to show in the UI
MAX_ERROR_CHARS = 500

Outcome = tuple[str, str | None]


@dataclass(frozen=True)
class ParsedStep:
    row_id: int
    item_id: str
    step: xr.Dataset
    #: the step's ``append_dim`` value
    value: np.generic
    #: the source object's LastModified, taken before the parse
    last_modified: dt.datetime


@dataclass(frozen=True)
class BatchResult:
    #: row id -> (status, reason); appended rows take ``snapshot_id``
    outcomes: dict[int, Outcome]
    #: the branch tip after the batch: the new commit, or the unchanged tip
    snapshot_id: str
    committed: bool
    #: ``append_dim`` values after the batch
    values: np.ndarray
    trimmed: int
    #: the cube has its ``append_dim`` array (False only if nothing was ever written)
    initialised: bool


def error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]


def write_step(session: ic.Session, parsed: ParsedStep, append_dim: str | None) -> None:
    """One step's virtual references (and its native arrays) into the session.
    ``append_dim=None`` writes a new cube's first step."""
    parsed.step.vz.to_icechunk(
        session.store, append_dim=append_dim, last_updated_at=parsed.last_modified
    )


def commit_session(session: ic.Session, message: str) -> str:
    return session.commit(message)


class _StepWriteFailed(Exception):
    def __init__(self, row_id: int, message: str) -> None:
        super().__init__(message)
        self.row_id = row_id
        self.message = message


def write_batch(
    repo: ic.Repository,
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
) -> BatchResult:
    try:
        return _attempt(repo, parsed, config, now)
    except ic.ConflictError:
        logger.warning(
            "cube commit conflicted; redoing the batch once from the new tip",
            extra={"steps": len(parsed)},
        )
        return _attempt(repo, parsed, config, now)


def classify(
    parsed: Sequence[ParsedStep],
    state: CubeState,
    config: CubeSinkConfig,
    exclude: frozenset[int] = frozenset(),
) -> tuple[dict[int, Outcome], list[ParsedStep]]:
    """Each step's outcome against the cube, and the accepted steps in time order."""
    outcomes: dict[int, Outcome] = {}
    accepted: list[ParsedStep] = []
    present = set(state.values)
    tip = state.values[-1] if len(state.values) else None
    specs = dict(state.specs) if state.initialised else None
    statics = dict(state.statics) if state.initialised else None
    for p in sorted(parsed, key=lambda p: (p.value, p.row_id)):
        if p.row_id in exclude:
            continue
        if p.value in present:
            outcomes[p.row_id] = ("appended", REASON_DUPLICATE)
            continue
        if tip is not None and p.value <= tip:
            outcomes[p.row_id] = ("skipped", REASON_LATE)
            continue
        try:
            mine, grid = step_specs(p.step, config), step_statics(p.step, config)
            if specs is None or statics is None:
                specs, statics = mine, grid  # the first step of a new cube sets its layout
            else:
                check_layout(mine, specs)
                check_statics(grid, statics)
        except LayoutError as exc:
            logger.warning(
                "cube step skipped: unsupported layout",
                extra={"item_id": p.item_id, "detail": str(exc)},
            )
            outcomes[p.row_id] = ("skipped", REASON_UNSUPPORTED_LAYOUT)
            continue
        accepted.append(p)
        present.add(p.value)
        tip = p.value
        outcomes[p.row_id] = ("appended", None)
    return outcomes, accepted


def _attempt(
    repo: ic.Repository,
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
) -> BatchResult:
    dim = config.append_dim
    failed: dict[int, str] = {}
    while True:
        session = repo.writable_session(BRANCH)
        state = read_state(session, dim)
        outcomes, accepted = classify(parsed, state, config, frozenset(failed))
        try:
            for i, step in enumerate(accepted):
                try:
                    write_step(session, step, dim if state.initialised or i > 0 else None)
                except Exception as exc:
                    raise _StepWriteFailed(step.row_id, error_text(exc)) from exc
        except _StepWriteFailed as err:
            logger.warning(
                "cube step write failed; redoing the batch without it",
                extra={"row_id": err.row_id, "error": err.message},
            )
            failed[err.row_id] = err.message
            continue
        break
    for row_id, message in failed.items():
        outcomes[row_id] = ("failed", message)
    after = read_state(session, dim)
    trimmed = trim_count(after.values, config.window, now)
    if trimmed:
        _trim(session, after, trimmed)
    if session.has_uncommitted_changes:
        snapshot_id = commit_session(session, _message(accepted, trimmed))
        committed = True
    else:
        snapshot_id = repo.lookup_branch(BRANCH)
        committed = False
    return BatchResult(
        outcomes=outcomes,
        snapshot_id=snapshot_id,
        committed=committed,
        values=after.values[trimmed:],
        trimmed=trimmed,
        initialised=after.initialised,
    )


def _trim(session: ic.Session, state: CubeState, k: int) -> None:
    """Drop the ``k`` oldest steps of every time-dimensioned array (spike
    soak.py): shift the chunk grid down, then shrink. Every time array has
    time chunk 1, so a shift of ``k`` chunks is ``k`` steps."""
    group = zarr.open_group(session.store, mode="r+")
    for name in state.time_arrays:
        if group[name].chunks[0] != 1:
            raise RuntimeError(
                f"{name} has time chunk {group[name].chunks[0]}; the window shift needs 1"
            )
    for name in state.time_arrays:
        array = group[name]
        session.shift_array(f"/{name}", (-k,) + (0,) * (array.ndim - 1))
    for name in state.time_arrays:
        array = group[name]
        array.resize((array.shape[0] - k, *array.shape[1:]))


def _message(accepted: Sequence[ParsedStep], trimmed: int) -> str:
    if accepted:
        return f"append {len(accepted)} items: {accepted[0].item_id}…{accepted[-1].item_id}"
    return f"trim {trimmed} steps"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cube_write.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cubes/write.py tests/test_cube_write.py
git commit -m "Z-4: write a batch of virtual steps, trim the window, commit once

Duplicate -> appended/duplicate, late -> skipped, layout -> skipped; a failing
write fails only its row; ConflictError redoes once from the new tip, never
a rebase.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `cubes/resolve.py` — an item → its NODD object

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/resolve.py`
- Test: `services/pipeline/tests/test_cube_resolve.py`

**Interfaces:**
- Consumes: `association_for_href` (Task 2), `libs_from_connection`, `SourceLibs`, `SourceConnectionError` (Task 3), `PgProcessRepo.reference_source_hrefs`, `PgIngestRepo.list_enabled_ingest_associations`, `EgressBlocked`.
- Produces (in `pipeline.cubes.resolve`):
  - `SourceUnavailable(status: str, reason: str)` (an `Exception` with `.status`, `.reason`).
  - `ResolvedSource(libs: SourceLibs, key: str)` with `.url`.
  - `SourceResolver` ABC:
    - `resolve(source_collection_id, item_id) -> ResolvedSource` (raises `SourceUnavailable`)
    - `last_modified(source) -> dt.datetime` (raises `FileNotFoundError`)
  - `PgSourceResolver(settings, master_key, hrefs, associations)` and `PgSourceResolver.from_settings(settings, master_key)`.
  - `pick_hdf_href(hrefs: Mapping[str, str]) -> str`, `HDF_SUFFIXES`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cube_resolve.py`:

```python
"""Item → source object through its reference association (spec §6.1)."""

from __future__ import annotations

import pytest

from _cube_sources import SOURCE_LAST_MODIFIED, local_libs, scan, write_goes_file
from pipeline.config import Settings
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.resolve import (
    PgSourceResolver,
    ResolvedSource,
    SourceUnavailable,
    pick_hdf_href,
)
from pipeline.ingest.repo import IngestAssociation
from pipeline.storage import platform

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
BASE = "https://noaa-goes19.s3.us-east-1.amazonaws.com/"
NC = "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s1.nc"


def _assoc(config: dict | None = None) -> IngestAssociation:
    conn = ConnectionRow(
        id="nodd", name="nodd", protocol="s3",
        config=config or {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True},
        credentials=seal("{}", KEY), host_key=None,
    )
    return IngestAssociation(
        id="a1", collection_id="src", config={"storage_mode": "reference"}, connection=conn
    )


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    def fake(host, allow_hosts=()):
        if host.endswith(".internal"):
            from pipeline.connections.egress import EgressBlocked

            raise EgressBlocked(f"egress to {host} is blocked")
        return ["203.0.113.7"]

    monkeypatch.setattr(platform, "resolve_pinned", fake)


def _resolver(hrefs: dict[str, str], associations: list[IngestAssociation]):
    calls = {"assoc": 0}

    async def get_hrefs(collection_id: str, item_id: str) -> dict[str, str]:
        assert collection_id == "src"
        return hrefs

    async def get_assocs() -> list[IngestAssociation]:
        calls["assoc"] += 1
        return associations

    return PgSourceResolver(Settings.from_env(env={}), KEY, get_hrefs, get_assocs), calls


def test_pick_hdf_href():
    assert pick_hdf_href({"a.nc": "u1", "a.json": "u2"}) == "u1"
    with pytest.raises(SourceUnavailable) as none:
        pick_hdf_href({})
    assert (none.value.status, none.value.reason) == ("skipped", "source_missing")
    for hrefs in ({"a.json": "u"}, {"a.nc": "u1", "b.h5": "u2"}):
        with pytest.raises(SourceUnavailable) as bad:
            pick_hdf_href(hrefs)
        assert (bad.value.status, bad.value.reason) == ("skipped", "unsupported_layout")


async def test_resolves_through_the_reference_association():
    resolver, calls = _resolver({"x.nc": BASE + NC}, [_assoc()])
    first = await resolver.resolve("src", "item-1")
    second = await resolver.resolve("src", "item-2")
    assert first.url == f"s3://noaa-goes19/{NC}"
    assert first.libs is second.libs  # built once per connection per job
    assert calls["assoc"] == 1  # associations listed once per job


async def test_an_href_no_association_claims_is_no_source_connection():
    resolver, _ = _resolver({"x.nc": "https://elsewhere.example/x.nc"}, [_assoc()])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert (exc.value.status, exc.value.reason) == ("skipped", "no_source_connection")


async def test_an_item_without_reference_hrefs_is_source_missing():
    resolver, _ = _resolver({}, [_assoc()])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert (exc.value.status, exc.value.reason) == ("skipped", "source_missing")


async def test_egress_blocked_fails_the_row_with_the_message():
    blocked = _assoc({"bucket": "b", "endpoint": "http://meta.internal", "force_path_style": True,
                      "anonymous": True})
    resolver, _ = _resolver({"x.nc": "http://meta.internal/b/x.nc"}, [blocked])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert exc.value.status == "failed"
    assert "meta.internal" in exc.value.reason


async def test_last_modified_is_the_object_head(tmp_path):
    write_goes_file(tmp_path / "a.nc", when=scan(0))
    resolver, _ = _resolver({}, [])
    libs = local_libs(tmp_path)
    assert await resolver.last_modified(ResolvedSource(libs, "a.nc")) == SOURCE_LAST_MODIFIED
    with pytest.raises(FileNotFoundError):
        await resolver.last_modified(ResolvedSource(libs, "missing.nc"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.resolve'`.

- [ ] **Step 3: Write `cubes/resolve.py`**

```python
"""Item → its NODD source object, through the association that produced it (spec §6.1).

The item's reference-mode source hrefs come from the ingest ledger
(``PgProcessRepo.reference_source_hrefs``: the catalog stores canonical hrefs,
not source ones). Its header is the one HDF file among them. The
reference-mode association whose connection prefixes that href owns it
(``connections/sources.py``), and that connection, egress-checked, is the
only way the cube writer reads the source (ADR 0022).
"""

from __future__ import annotations

import abc
import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import obstore

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.connections.sources import association_for_href
from pipeline.cubes.source import SourceConnectionError, SourceLibs, libs_from_connection
from pipeline.ingest.repo import IngestAssociation, PgIngestRepo
from pipeline.process.repo import PgProcessRepo

HDF_SUFFIXES = (".nc", ".nc4", ".h5", ".hdf5", ".he5")


class SourceUnavailable(Exception):
    """The row cannot be appended: its ledger ``status`` and ``reason``."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class ResolvedSource:
    libs: SourceLibs
    key: str

    @property
    def url(self) -> str:
        return self.libs.url(self.key)


class SourceResolver(abc.ABC):
    @abc.abstractmethod
    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        """Raises :class:`SourceUnavailable`."""

    @abc.abstractmethod
    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        """The object's LastModified (one HEAD). Raises ``FileNotFoundError``."""


def pick_hdf_href(hrefs: Mapping[str, str]) -> str:
    """filename -> href: the item's one HDF source file."""
    if not hrefs:
        raise SourceUnavailable("skipped", "source_missing")
    hdf = [href for name, href in sorted(hrefs.items()) if name.lower().endswith(HDF_SUFFIXES)]
    if len(hdf) != 1:
        raise SourceUnavailable("skipped", "unsupported_layout")
    return hdf[0]


@dataclass
class PgSourceResolver(SourceResolver):
    """One per job: associations are listed once, and each connection's
    libraries are built once."""

    settings: Settings
    master_key: bytes
    hrefs: Callable[[str, str], Awaitable[dict[str, str]]]
    associations: Callable[[], Awaitable[list[IngestAssociation]]]
    _assocs: list[IngestAssociation] | None = None
    _libs: dict[str, SourceLibs] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings, master_key: bytes) -> PgSourceResolver:
        return cls(
            settings,
            master_key,
            PgProcessRepo(settings.database_url).reference_source_hrefs,
            PgIngestRepo(settings.database_url).list_enabled_ingest_associations,
        )

    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        href = pick_hdf_href(await self.hrefs(source_collection_id, item_id))
        if self._assocs is None:
            self._assocs = await self.associations()
        allow = self.settings.egress_allow_hosts
        match = association_for_href(href, self._assocs, self.master_key, allow)
        if match is None:
            raise SourceUnavailable("skipped", "no_source_connection")
        connection = match.association.connection
        libs = self._libs.get(connection.id)
        if libs is None:
            try:
                # resolve_pinned does DNS: keep it off the event loop.
                libs = await asyncio.to_thread(libs_from_connection, connection, allow)
            except (EgressBlocked, SourceConnectionError) as exc:
                raise SourceUnavailable("failed", f"{type(exc).__name__}: {exc}") from exc
            self._libs[connection.id] = libs
        return ResolvedSource(libs, match.key)

    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        meta = await obstore.head_async(source.libs.store, source.key)
        return meta["last_modified"]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cube_resolve.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cubes/resolve.py tests/test_cube_resolve.py
git commit -m "Z-4: resolve an item to its NODD object through its reference association

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `cubes/append.py` — the job's orchestration

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/append.py`
- Test: `services/pipeline/tests/test_cube_append.py`

**Interfaces:**
- Consumes: Tasks 4–8 (`CubeRepo` and its types, `parse_header`, `step_value`, `LayoutError`, `open_repository`, `read_state`, `reset_to_root`, `BRANCH`, `write_batch`, `ParsedStep`, `BatchResult`, `error_text`, `SourceResolver`, `ResolvedSource`, `SourceUnavailable`), `parse_cube_sink_config`.
- Produces (in `pipeline.cubes.append`):
  - `BATCH_LIMIT = 50`, `PARSE_CONCURRENCY = 4`, `MAX_ROW_ATTEMPTS = 6`, `REASON_CRASH_LOOP = "crash_loop"`.
  - `AppendDeps(repo, resolver, storage_for, enqueue_next, after_batch=None, now=<utcnow>, batch_limit=BATCH_LIMIT, parse=parse_header)`.
  - Exceptions: any exception after `take_pending` gives the attempts back (`release_rows`), sets `last_error` to `error_text(exc)`, and re-raises.
  - `AppendReport(taken, appended, skipped, failed, snapshot_id, committed, recorded, requeued)`.
  - `async run_cube_append(cube_sink_id: str, deps: AppendDeps) -> AppendReport`.
  - The Z-5 hook: `after_batch(sink: CubeSink, config: CubeSinkConfig, result: BatchResult)` is awaited after every batch that reached the repository, once the tip is recorded and the ledger written. `result.snapshot_id` is the recorded tip. It must be idempotent.

- [ ] **Step 1: Write the failing tests**

`tests/test_cube_append.py`:

```python
"""run_cube_append: claim, parse off the loop, commit, record, ledger, re-enqueue."""

from __future__ import annotations

import asyncio
import datetime as dt
import threading
from dataclasses import dataclass, field
from pathlib import Path

import icechunk as ic
import obstore
import pytest
import xarray as xr

import pipeline.cubes.write as write_mod
from _cube_fake import FakeCubeRepo, FakeSink
from _cube_sources import (
    GOES_CONFIG,
    SOURCE_LAST_MODIFIED,
    as_ns,
    local_libs,
    scan,
    write_goes_file,
)
from pipeline.cubes.append import (
    BATCH_LIMIT,
    MAX_ROW_ATTEMPTS,
    PARSE_CONCURRENCY,
    AppendDeps,
    run_cube_append,
)
from pipeline.cubes.icerepo import BRANCH
from pipeline.cubes.repo import LedgerEntry
from pipeline.cubes.resolve import ResolvedSource, SourceResolver, SourceUnavailable
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import parse_header

SINK = "s1"


@dataclass
class FakeResolver(SourceResolver):
    libs: SourceLibs
    #: item_id -> object key, or the SourceUnavailable to raise
    items: dict[str, str | SourceUnavailable] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        found = self.items.get(item_id)
        if found is None:
            raise SourceUnavailable("skipped", "source_missing")
        if isinstance(found, SourceUnavailable):
            raise found
        return ResolvedSource(self.libs, found)

    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        self.calls.append(("head", source.key))
        return (await obstore.head_async(source.libs.store, source.key))["last_modified"]


@dataclass
class Harness:
    root: Path
    repo: FakeCubeRepo
    resolver: FakeResolver
    storage: object
    enqueued: list[str] = field(default_factory=list)
    hooked: list = field(default_factory=list)
    now: dt.datetime = field(default_factory=lambda: scan(1000))

    def deps(self, **over) -> AppendDeps:
        async def enqueue_next(cube_sink_id: str) -> None:
            self.enqueued.append(cube_sink_id)

        base = {
            "repo": self.repo,
            "resolver": self.resolver,
            "storage_for": lambda sink: self.storage,
            "enqueue_next": enqueue_next,
            "now": lambda: self.now,
        }
        return AppendDeps(**{**base, **over})

    async def pend(self, *ns: int, **file_kw) -> None:
        """A source file and a pending ledger row per scan n (item ``i{n}``)."""
        for n in ns:
            write_goes_file(self.root / f"{n}.nc", when=scan(n), value=float(n), **file_kw)
            self.resolver.items[f"i{n}"] = f"{n}.nc"
        await self.repo.record_appends([LedgerEntry(SINK, f"i{n}", scan(n)) for n in ns])

    def ledger(self) -> dict[str, tuple]:
        return {r.item_id: (r.status, r.reason) for r in self.repo.rows(SINK)}

    def sink(self) -> FakeSink:
        return self.repo.sinks[0]

    def times(self) -> list:
        repo = ic.Repository.open(
            self.storage,
            authorize_virtual_chunk_access=ic.containers_credentials(
                {self.resolver.libs.prefix: self.resolver.libs.credentials}
            ),
        )
        ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
        return list(ds["t"].values)


@pytest.fixture
def h(tmp_path) -> Harness:
    libs = local_libs(tmp_path)
    repo = FakeCubeRepo(sinks=[FakeSink(SINK, "src", "cube", config=dict(GOES_CONFIG))])
    return Harness(tmp_path, repo, FakeResolver(libs), ic.in_memory_storage())


def ns(*scans: int) -> list:
    return [as_ns(scan(n)) for n in scans]


async def test_pending_rows_become_one_recorded_commit(h):
    await h.pend(0, 1, 2)
    report = await run_cube_append(SINK, h.deps())
    assert (report.taken, report.appended, report.committed, report.recorded) == (3, 3, True, True)
    assert h.times() == ns(0, 1, 2)
    assert h.ledger() == {f"i{n}": ("appended", None) for n in (0, 1, 2)}
    assert {r.snapshot_id for r in h.repo.rows(SINK)} == {h.sink().last_snapshot_id}
    assert h.sink().source_prefixes == (h.resolver.libs.prefix,)
    assert h.enqueued == []


async def test_the_constants_are_the_specs():
    assert (BATCH_LIMIT, PARSE_CONCURRENCY, MAX_ROW_ATTEMPTS) == (50, 4, 6)


async def test_a_first_commit_that_loses_to_an_app_write_stays_provisional(h):
    await h.pend(0, 1)

    def app_patch(repo: FakeCubeRepo) -> None:  # a PUT/PATCH between commit and record
        repo.sinks[0].version = "v2"

    h.repo.before_record = app_patch
    lost = await run_cube_append(SINK, h.deps())
    assert (lost.committed, lost.recorded, lost.requeued) == (True, False, True)
    assert h.sink().last_snapshot_id is None
    assert set(h.ledger().values()) == {("pending", None)}
    assert h.enqueued == [SINK]

    won = await run_cube_append(SINK, h.deps())  # the next job rebuilds under v2
    assert won.recorded
    # Rewritten after the reset, so not duplicates:
    assert h.ledger() == {"i0": ("appended", None), "i1": ("appended", None)}
    assert h.times() == ns(0, 1)


async def test_unrecorded_data_from_a_crash_is_reset_before_writing(h):
    await h.pend(0)
    h.repo.record_commit_error = RuntimeError("worker killed")
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("pending", None)}
    await h.pend(1)
    await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("appended", None), "i1": ("appended", None)}
    assert h.times() == ns(0, 1)


async def test_redone_steps_of_a_recorded_cube_are_duplicates(h):
    await h.pend(0, 1)
    h.repo.finish_error = RuntimeError("worker killed")  # crash after the record
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps())
    recorded = h.sink().last_snapshot_id
    report = await run_cube_append(SINK, h.deps())
    assert not report.committed
    assert h.ledger() == {"i0": ("appended", "duplicate"), "i1": ("appended", "duplicate")}
    assert {r.snapshot_id for r in h.repo.rows(SINK)} == {recorded}
    assert h.times() == ns(0, 1)


async def test_source_outcomes_land_on_the_ledger(h):
    await h.pend(0)
    h.resolver.items["nc"] = SourceUnavailable("skipped", "no_source_connection")
    h.resolver.items["eg"] = SourceUnavailable("failed", "EgressBlocked: egress to x is blocked")
    h.resolver.items["gone"] = "deleted.nc"  # resolves, but the object is missing
    await h.repo.record_appends(
        [LedgerEntry(SINK, i, scan(5)) for i in ("nc", "eg", "gone", "nohref")]
    )
    report = await run_cube_append(SINK, h.deps())
    assert h.ledger() == {
        "i0": ("appended", None),
        "nc": ("skipped", "no_source_connection"),
        "eg": ("failed", "EgressBlocked: egress to x is blocked"),
        "gone": ("skipped", "source_missing"),
        "nohref": ("skipped", "source_missing"),
    }
    assert (report.appended, report.skipped, report.failed) == (1, 3, 1)


async def test_unsupported_layout_and_late(h):
    await h.pend(5)
    await run_cube_append(SINK, h.deps())
    await h.pend(3)  # before the tip: late
    write_goes_file(h.root / "6.nc", when=scan(6), variables=("CMI",))  # DQF missing
    h.resolver.items["i6"] = "6.nc"
    await h.repo.record_appends([LedgerEntry(SINK, "i6", scan(6))])
    await run_cube_append(SINK, h.deps())
    assert h.ledger()["i3"] == ("skipped", "late")
    assert h.ledger()["i6"] == ("skipped", "unsupported_layout")
    assert h.times() == ns(5)


async def test_one_bad_file_fails_only_its_row(h):
    await h.pend(0, 1, 2)
    real = parse_header

    def flaky(url, registry, config):
        if url.endswith("/1.nc"):
            raise OSError("connection reset by peer")
        return real(url, registry, config)

    await run_cube_append(SINK, h.deps(parse=flaky))
    assert h.ledger() == {
        "i0": ("appended", None),
        "i1": ("failed", "OSError: connection reset by peer"),
        "i2": ("appended", None),
    }
    assert h.times() == ns(0, 2)


async def test_a_row_past_the_attempt_cap_fails_crash_loop(h):
    await h.pend(0, 1)
    h.repo.ledger[(SINK, "i0")].attempts = MAX_ROW_ATTEMPTS  # this take makes it 7
    await run_cube_append(SINK, h.deps())
    assert h.ledger() == {"i0": ("failed", "crash_loop"), "i1": ("appended", None)}


async def test_the_batch_limit_re_enqueues_the_rest(h):
    await h.pend(0, 1, 2)
    first = await run_cube_append(SINK, h.deps(batch_limit=2))
    assert (first.taken, first.requeued, h.enqueued) == (2, True, [SINK])
    second = await run_cube_append(SINK, h.deps(batch_limit=2))
    assert (second.taken, second.requeued, h.enqueued) == (1, False, [SINK])
    assert h.times() == ns(0, 1, 2)


async def test_a_backlog_larger_than_the_window_converges(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_steps": 2}}
    await h.pend(0, 1, 2, 3, 4)
    runs = 0
    while True:
        runs += 1
        report = await run_cube_append(SINK, h.deps(batch_limit=2))
        if not report.requeued:
            break
    assert runs == 3
    assert h.times() == ns(3, 4)
    assert all(status == "appended" for status, _ in h.ledger().values())


async def test_the_window_holds_across_runs(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_steps": 2}}
    await h.pend(0, 1, 2)
    await run_cube_append(SINK, h.deps())
    await h.pend(3)
    await run_cube_append(SINK, h.deps())
    assert h.times() == ns(2, 3)


async def test_parsing_runs_off_the_loop_at_most_four_at_a_time(h):
    await h.pend(*range(8))
    loop_thread = threading.get_ident()
    seen: list[int] = []
    live, peak = 0, 0
    guard = threading.Lock()

    def tracked(url, registry, config):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
            seen.append(threading.get_ident())
        try:
            return parse_header(url, registry, config)
        finally:
            with guard:
                live -= 1

    await run_cube_append(SINK, h.deps(parse=tracked))
    assert len(seen) == 8 and loop_thread not in seen
    assert peak <= PARSE_CONCURRENCY


async def test_the_head_comes_before_the_parse_and_its_time_is_passed(h, monkeypatch):
    await h.pend(0)
    order: list[str] = []
    passed: list[dt.datetime] = []
    real_write = write_mod.write_step

    def parse(url, registry, config):
        order.append("parse")
        return parse_header(url, registry, config)

    def capture(session, parsed, append_dim):
        passed.append(parsed.last_modified)
        real_write(session, parsed, append_dim)

    monkeypatch.setattr(write_mod, "write_step", capture)
    original_head = h.resolver.last_modified

    async def head(source):
        order.append("head")
        return await original_head(source)

    h.resolver.last_modified = head  # type: ignore[method-assign]
    await run_cube_append(SINK, h.deps(parse=parse))
    assert order == ["head", "parse"]
    assert passed == [SOURCE_LAST_MODIFIED]


async def test_a_disabled_or_missing_sink_does_nothing(h):
    await h.pend(0)
    h.sink().enabled = False
    assert (await run_cube_append(SINK, h.deps())).taken == 0
    assert (await run_cube_append("gone", h.deps())).taken == 0
    assert h.ledger() == {"i0": ("pending", None)}


async def test_a_sink_deleted_mid_job_ends_quietly(h):
    await h.pend(0)
    h.repo.before_record = lambda repo: repo.sinks.clear()
    report = await run_cube_append(SINK, h.deps())
    assert (report.recorded, report.requeued) == (False, True)  # the next job sees no sink


async def test_an_invalid_config_records_an_error_and_leaves_rows(h):
    h.sink().config = {**GOES_CONFIG, "parser": "grib"}
    await h.pend(0)
    report = await run_cube_append(SINK, h.deps())
    assert report.taken == 0
    assert h.sink().last_error.startswith("invalid config:")
    assert h.ledger() == {"i0": ("pending", None)}


async def test_after_batch_runs_after_every_batch_that_reached_the_cube(h):
    calls = []

    async def hook(sink, config, result):
        calls.append((sink.id, config.asset_key, result.snapshot_id))

    await h.pend(0)
    await run_cube_append(SINK, h.deps(after_batch=hook))
    first = h.sink().last_snapshot_id
    assert calls == [(SINK, "cube", first)]
    await h.pend(-1)  # late: nothing committed, and the hook re-confirms the tip
    await run_cube_append(SINK, h.deps(after_batch=hook))
    assert calls == [(SINK, "cube", first), (SINK, "cube", first)]


async def test_the_asset_hook_catches_up_after_a_crash(h):
    tips: list[str] = []

    async def hook(sink, config, result):
        tips.append(result.snapshot_id)

    await h.pend(0)
    await run_cube_append(SINK, h.deps(after_batch=hook))
    await h.pend(1)
    h.repo.finish_error = RuntimeError("worker killed")  # recorded, hook not reached
    with pytest.raises(RuntimeError):
        await run_cube_append(SINK, h.deps(after_batch=hook))
    tip = h.sink().last_snapshot_id
    assert tips[-1] != tip
    await run_cube_append(SINK, h.deps(after_batch=hook))  # the redo: duplicates only
    assert tips[-1] == tip


async def test_a_storage_outage_hands_the_attempts_back(h):
    await h.pend(0)

    def down(sink):
        raise OSError("platform store unreachable")

    for _ in range(MAX_ROW_ATTEMPTS + 2):
        with pytest.raises(OSError):
            await run_cube_append(SINK, h.deps(storage_for=down))
    row = h.repo.rows(SINK)[0]
    assert (row.status, row.attempts) == ("pending", 0)  # never crash_loop
    assert h.sink().last_error == "OSError: platform store unreachable"
    await run_cube_append(SINK, h.deps())  # the store is back
    assert h.ledger() == {"i0": ("appended", None)}
    assert h.sink().last_error is None


async def test_a_failed_release_keeps_the_original_exception(h, monkeypatch):
    await h.pend(0)

    async def db_blip(cube_sink_id, row_ids):
        raise ConnectionError("database unreachable")

    def down(sink):
        raise OSError("platform store unreachable")

    monkeypatch.setattr(h.repo, "release_rows", db_blip)
    with pytest.raises(OSError, match="platform store"):
        await run_cube_append(SINK, h.deps(storage_for=down))
    assert h.repo.rows(SINK)[0].attempts == 1  # kept: the conservative direction


async def test_rows_older_than_max_age_are_skipped_without_parsing(h):
    h.sink().config = {**GOES_CONFIG, "window": {"max_age": "10m"}}
    h.now = scan(10)
    await h.pend(0, 9)
    parsed: list[str] = []

    def tracked(url, registry, config):
        parsed.append(url.rsplit("/", 1)[1])
        return parse_header(url, registry, config)

    await run_cube_append(SINK, h.deps(parse=tracked))
    assert h.ledger() == {"i0": ("skipped", "late"), "i9": ("appended", None)}
    assert parsed == ["9.nc"]
    assert h.times() == ns(9)


async def test_parse_does_not_block_the_event_loop(h):
    await h.pend(0)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        await run_cube_append(SINK, h.deps())
    finally:
        task.cancel()
    assert ticks > 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_append.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes.append'`.

- [ ] **Step 3: Write `cubes/append.py`**

```python
"""``pipeline.cube_append``: pending ledger rows → one virtual commit (spec §6.1-6.2; ADR 0022).

One writer per sink: the job runs under the Procrastinate lock ``cube:{id}``.
It stays correct if it runs twice anyway (a stalled-job requeue while the
first run is still alive, #90):
- rows are claimed by bumping ``attempts``;
- the Icechunk commit is optimistic (``write_batch`` redoes a conflict once);
- steps already in the cube read as duplicates;
- ``finish_rows`` touches only rows still pending.

Until the sink records a snapshot (``last_snapshot_id``), its repository is
PROVISIONAL, because the app may still change the source and layout (#98):
- A job that finds unrecorded data resets ``main`` to the root snapshot
  before writing. Those rows are still pending, so nothing is lost.
- The first commit is recorded only while the sink still has the app
  version the job read. If the race is lost, the rows stay pending and the
  next job rebuilds under the new config.
Nothing is deleted: the orphaned snapshots are Icechunk garbage for Z-6.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from obspec_utils.registry import ObjectStoreRegistry

from pipeline.cubes.config import CubeSinkConfig, CubeSinkConfigError, parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository, read_state, reset_to_root
from pipeline.cubes.repo import CubeRepo, CubeSink, PendingRow, RowOutcome
from pipeline.cubes.resolve import ResolvedSource, SourceResolver, SourceUnavailable
from pipeline.cubes.source import SourceLibs
from pipeline.cubes.steps import LayoutError, parse_header, step_value
from pipeline.cubes.write import BatchResult, ParsedStep, error_text, write_batch

logger = logging.getLogger(__name__)

#: rows per job; a backlog re-enqueues instead of holding the lock (§14.7)
BATCH_LIMIT = 50
#: headers parsed at once (spike: ~1.45 s per header, ~0.04 s per commit)
PARSE_CONCURRENCY = 4
#: a row claimed more often than this is failed instead of parsed (#90)
MAX_ROW_ATTEMPTS = 6
REASON_CRASH_LOOP = "crash_loop"
REASON_LATE = "late"
REASON_SOURCE_MISSING = "source_missing"
REASON_UNSUPPORTED_LAYOUT = "unsupported_layout"

AfterBatch = Callable[[CubeSink, CubeSinkConfig, BatchResult], Awaitable[None]]
Parse = Callable[[str, ObjectStoreRegistry, CubeSinkConfig], Any]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class AppendDeps:
    repo: CubeRepo
    resolver: SourceResolver
    #: the Icechunk Storage of a sink's repository (``icerepo.cube_storage``)
    storage_for: Callable[[CubeSink], Any]
    #: wake the sink's next job (``jobs.cubes.enqueue_cube_append``: same locks)
    enqueue_next: Callable[[str], Awaitable[object]]
    #: Z-5's collection asset writer: awaited after every batch that reached
    #: the repository (recorded tip, ledger written); must be idempotent
    after_batch: AfterBatch | None = None
    now: Callable[[], dt.datetime] = _utcnow
    batch_limit: int = BATCH_LIMIT
    parse: Parse = parse_header


@dataclass
class AppendReport:
    taken: int = 0
    appended: int = 0
    skipped: int = 0
    failed: int = 0
    snapshot_id: str | None = None
    committed: bool = False
    recorded: bool = False
    requeued: bool = False


async def run_cube_append(cube_sink_id: str, deps: AppendDeps) -> AppendReport:
    report = AppendReport()
    sink = await deps.repo.load_sink(cube_sink_id)
    if sink is None or not sink.enabled:
        return report  # a disabled sink's rows wait; cube_kick wakes it once re-enabled
    try:
        config = parse_cube_sink_config(sink.config)
    except CubeSinkConfigError as exc:
        await deps.repo.record_error(sink.id, f"invalid config: {exc}")
        logger.error(
            "cube_append: invalid sink config",
            extra={"cube_sink_id": sink.id, "error": str(exc)},
        )
        return report

    rows = await deps.repo.take_pending(sink.id, deps.batch_limit)
    report.taken = len(rows)
    if not rows:
        return report
    try:
        await _append_rows(sink, config, rows, deps, report)
    except Exception as exc:
        # An exception is an outage or a bug, not a crash loop: give the
        # attempts back, so a platform-store outage cannot fail rows
        # `crash_loop`. A worker that dies mid-job never gets here, so real
        # crashes still count (#90). A cleanup failure (likely the same DB
        # blip) is logged and must not replace the original exception: the
        # rows then keep their bump, which is the conservative direction.
        try:
            await deps.repo.release_rows(sink.id, [r.id for r in rows])
            await deps.repo.record_error(sink.id, error_text(exc))
        except Exception:
            logger.exception(
                "cube_append: could not release rows", extra={"cube_sink_id": sink.id}
            )
        raise
    return report


async def _append_rows(
    sink: CubeSink,
    config: CubeSinkConfig,
    rows: Sequence[PendingRow],
    deps: AppendDeps,
    report: AppendReport,
) -> None:
    looping = [
        RowOutcome(r.id, "failed", REASON_CRASH_LOOP) for r in rows if r.attempts > MAX_ROW_ATTEMPTS
    ]
    if looping:
        await deps.repo.finish_rows(sink.id, looping)
        logger.error(
            "cube_append: rows failed after too many attempts",
            extra={"cube_sink_id": sink.id, "rows": len(looping)},
        )
    live = [r for r in rows if r.attempts <= MAX_ROW_ATTEMPTS]

    outcomes: dict[int, RowOutcome] = {}
    cutoff = _age_cutoff(config, deps.now())
    if cutoff is not None:
        # Already outside the window: skip before any header is read.
        for row in [r for r in live if r.item_datetime < cutoff]:
            outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_LATE)
        live = [r for r in live if r.item_datetime >= cutoff]
    sources: list[tuple[PendingRow, ResolvedSource]] = []
    for row in live:
        try:
            resolved = await deps.resolver.resolve(sink.source_collection_id, row.item_id)
        except SourceUnavailable as exc:
            outcomes[row.id] = RowOutcome(row.id, exc.status, exc.reason)
            continue
        sources.append((row, resolved))
    parsed = await _parse_all(sources, config, deps, outcomes)

    result: BatchResult | None = None
    if parsed:
        libs = list({source.libs.prefix: source.libs for _, source in sources}.values())
        result, prefixes = await asyncio.to_thread(
            _write,
            deps.storage_for(sink),
            libs,
            parsed,
            config,
            deps.now(),
            sink.last_snapshot_id is None,
        )
        report.snapshot_id, report.committed = result.snapshot_id, result.committed
        needs_record = result.initialised and (
            result.snapshot_id != sink.last_snapshot_id
            or set(prefixes) != set(sink.source_prefixes)
        )
        if needs_record and not await _record(deps.repo, sink, result, prefixes, deps.now()):
            await deps.enqueue_next(sink.id)
            report.requeued = True
            return
        report.recorded = True
        for row_id, (status, reason) in result.outcomes.items():
            snapshot = result.snapshot_id if status == "appended" else None
            outcomes[row_id] = RowOutcome(row_id, status, reason, snapshot)

    await deps.repo.finish_rows(sink.id, list(outcomes.values()))
    for outcome in [*looping, *outcomes.values()]:
        if outcome.status == "appended":
            report.appended += 1
        elif outcome.status == "skipped":
            report.skipped += 1
        else:
            report.failed += 1
    if result is not None and result.initialised and deps.after_batch is not None:
        await deps.after_batch(sink, config, result)
    if await deps.repo.has_pending(sink.id):
        await deps.enqueue_next(sink.id)
        report.requeued = True


def _age_cutoff(config: CubeSinkConfig, now: dt.datetime) -> dt.datetime | None:
    window = config.window
    if window is None or window.max_age_seconds is None:
        return None
    return now - dt.timedelta(seconds=window.max_age_seconds)


async def _parse_all(
    sources: Sequence[tuple[PendingRow, ResolvedSource]],
    config: CubeSinkConfig,
    deps: AppendDeps,
    outcomes: dict[int, RowOutcome],
) -> list[ParsedStep]:
    if not sources:
        return []
    registry = ObjectStoreRegistry({s.libs.registry_key: s.libs.store for _, s in sources})
    gate = asyncio.Semaphore(PARSE_CONCURRENCY)

    async def one(row: PendingRow, source: ResolvedSource) -> ParsedStep | None:
        async with gate:
            try:
                # HEAD first: a rewrite after it makes reads fail loudly, while
                # a HEAD after the parse could pair new bytes with old offsets.
                last_modified = await deps.resolver.last_modified(source)
                step = await asyncio.to_thread(deps.parse, source.url, registry, config)
            except FileNotFoundError:
                outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_SOURCE_MISSING)
                return None
            except LayoutError as exc:
                logger.warning(
                    "cube_append: unsupported layout",
                    extra={"item_id": row.item_id, "detail": str(exc)},
                )
                outcomes[row.id] = RowOutcome(row.id, "skipped", REASON_UNSUPPORTED_LAYOUT)
                return None
            except Exception as exc:
                logger.warning(
                    "cube_append: header parse failed",
                    exc_info=True,
                    extra={"item_id": row.item_id},
                )
                outcomes[row.id] = RowOutcome(row.id, "failed", error_text(exc))
                return None
        value = step_value(step, config.append_dim)
        return ParsedStep(row.id, row.item_id, step, value, last_modified)

    results = await asyncio.gather(*(one(row, source) for row, source in sources))
    return [p for p in results if p is not None]


def _write(
    storage: Any,
    libs: Sequence[SourceLibs],
    parsed: Sequence[ParsedStep],
    config: CubeSinkConfig,
    now: dt.datetime,
    provisional: bool,
) -> tuple[BatchResult, list[str]]:
    """Blocking: open, reset unrecorded data, write the batch."""
    repo = open_repository(storage, libs, replace_containers=provisional)
    if provisional:
        tip = repo.lookup_branch(BRANCH)
        if read_state(repo.readonly_session(BRANCH), config.append_dim).initialised:
            logger.warning(
                "cube repository holds unrecorded data; resetting it before writing",
                extra={"snapshot_id": tip},
            )
            reset_to_root(repo, from_snapshot_id=tip)
    result = write_batch(repo, parsed, config, now)
    return result, sorted(repo.config.virtual_chunk_containers or {})


async def _record(
    repo: CubeRepo,
    sink: CubeSink,
    result: BatchResult,
    prefixes: Sequence[str],
    now: dt.datetime,
) -> bool:
    """Record the tip. The first commit is conditional on the version read;
    a loss leaves the repository provisional (False)."""
    first = sink.version if sink.last_snapshot_id is None else None
    kw = {"snapshot_id": result.snapshot_id, "appended_at": now, "source_prefixes": prefixes}
    if await repo.record_commit(sink.id, first_commit_version=first, **kw):
        return True
    fresh = await repo.load_sink(sink.id)
    if fresh is None or fresh.last_snapshot_id is None:
        logger.warning(
            "cube_append: the sink changed or went away during its first append;"
            " the repository stays provisional",
            extra={"cube_sink_id": sink.id, "snapshot_id": result.snapshot_id},
        )
        return False
    # Another run recorded first (a double run); record over it with this tip.
    return await repo.record_commit(sink.id, first_commit_version=None, **kw)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cube_append.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean. If `test_a_sink_deleted_mid_job_ends_quietly` raises from `finish_rows`, check that the requeue path returns before `finish_rows`: rows of a deleted sink cascade away.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cubes/append.py tests/test_cube_append.py
git commit -m "Z-4: cube_append orchestration: claim, parse off-loop, commit, record

The first commit is recorded only while the sink keeps the app version read
(#90); an unrecorded repository is provisional and reset before writing.
Crash loops fail rows past 6 attempts; 50-row batches re-enqueue.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `jobs/cubes.py` — the real handler, retry, stub rows revisited

**Files:**
- Modify: `services/pipeline/src/pipeline/jobs/cubes.py`
- Modify: `services/pipeline/src/pipeline/cubes/repo.py` (remove `fail_pending`)
- Modify: `services/pipeline/tests/_cube_fake.py` (remove `fail_pending`)
- Modify: `services/pipeline/tests/test_cube_jobs.py`, `services/pipeline/tests/test_integration_cubes_repo.py`

**Interfaces:**
- Consumes: `run_cube_append`, `AppendDeps`, `AppendReport` (Task 9); `PgSourceResolver` (Task 8); `cube_storage` (Task 6); `load_key_or_skip` (`jobs/_common.py`); `CubeRepo.reset_stub_failures` (Task 4).
- Produces (in `pipeline.jobs.cubes`):
  - `CUBE_APPEND_RETRY = RetrySpec(max_attempts=3, wait_seconds=30)`.
  - `production_append_deps(settings, queue, repo) -> AppendDeps | None`.
  - `register(queue, settings, *, repo=None, deps_factory=None)`.
  - `REASON_NOT_IMPLEMENTED` is removed (now `STUB_REASON_NOT_IMPLEMENTED` in `cubes/repo.py`).

- [ ] **Step 1: Update the tests to the real handler**

In `tests/test_cube_jobs.py`:

1. Replace the imports block with:

```python
"""Cube jobs: the per-sink lock, the cube_kick backstop, the cube_append handler."""

import datetime as dt

import pytest

import pipeline.jobs.cubes as cubes_jobs
from _cube_fake import FakeCubeRepo, FakeLedgerRow, FakeSink
from pipeline.config import Settings
from pipeline.cubes.append import AppendDeps, AppendReport
from pipeline.cubes.repo import STUB_REASON_NOT_IMPLEMENTED, LedgerEntry
from pipeline.cubes.resolve import PgSourceResolver
from pipeline.jobs.cubes import (
    CUBE_APPEND_RETRY,
    JOB_CUBE_APPEND,
    JOB_CUBE_KICK,
    KICK_CRON,
    KICK_STALE_SECONDS,
    cube_append_enqueuer,
    cube_lock,
    enqueue_cube_append,
    production_append_deps,
    register,
)
from pipeline.queue.interface import QUEUE_DEFAULT, Enqueued, RetrySpec
from pipeline.queue.memory import InMemoryQueue

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)
DEPS = object()  # what deps_factory hands the (patched) run_cube_append
KEY_B64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
```

2. Replace the `queue` fixture with these two fixtures (`repo` stays as it is):

```python
@pytest.fixture
def ran(monkeypatch) -> list[str]:
    """Sink ids the handler ran run_cube_append for (the append is Task 9's)."""
    calls: list[str] = []

    async def fake_run(cube_sink_id: str, deps) -> AppendReport:
        assert deps is DEPS
        calls.append(cube_sink_id)
        return AppendReport()

    monkeypatch.setattr(cubes_jobs, "run_cube_append", fake_run)
    return calls


@pytest.fixture
def queue(repo: FakeCubeRepo, ran: list[str]) -> InMemoryQueue:
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo, deps_factory=lambda: DEPS)
    return q
```

3. In `test_registration`, add:

```python
    assert queue.retry_specs[JOB_CUBE_APPEND] == CUBE_APPEND_RETRY
    assert RetrySpec(max_attempts=3, wait_seconds=30) == CUBE_APPEND_RETRY
```

4. In `test_kick_recovers_a_stranded_append_a_waiting_job_covers`, add `ran: list[str]` to the parameters. Replace `assert repo.rows("s1")[0].status == "pending"  # wedged` with `assert ran == []  # wedged`, and the final `assert repo.rows("s1")[0].status == "failed"  # the stub ran: unwedged` with `assert ran == ["s1"]  # unwedged`.

5. Delete `test_stub_marks_pending_rows_failed_not_implemented` and add:

```python
async def test_cube_append_runs_the_append_for_its_sink(queue: InMemoryQueue, ran: list[str]):
    await enqueue_cube_append(queue, "s1")
    await queue.run_pending()
    assert ran == ["s1"]
    assert queue.jobs[0].status == "done"


async def test_cube_append_without_a_master_key_leaves_rows_waiting(repo: FakeCubeRepo):
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)  # production deps, no key
    await repo.record_appends([LedgerEntry("s1", "a", T0)])
    await enqueue_cube_append(q, "s1")
    await q.run_pending()
    assert q.jobs[0].status == "done"
    assert repo.rows("s1")[0].status == "pending"


async def test_production_deps_wire_the_real_seams(repo: FakeCubeRepo):
    q = InMemoryQueue()
    register(q, Settings.from_env(env={}), repo=repo)
    settings = Settings.from_env(
        env={"CREDENTIALS_MASTER_KEY": KEY_B64, "EGRESS_ALLOW_HOSTS": "minio"}
    )
    deps = production_append_deps(settings, q, repo)
    assert isinstance(deps, AppendDeps)
    assert isinstance(deps.resolver, PgSourceResolver)
    sink = await repo.load_sink("s1")
    assert "prefix: assets/cube1/_cube" in repr(deps.storage_for(sink))
    await deps.enqueue_next("s1")
    assert q.jobs[0].lock == q.jobs[0].queueing_lock == "cube:s1"


async def test_kick_returns_z3_stub_failures_to_pending(
    queue: InMemoryQueue, repo: FakeCubeRepo
):
    repo.ledger[("s1", "stub")] = FakeLedgerRow(
        "s1", "stub", T0, "failed", STUB_REASON_NOT_IMPLEMENTED, attempts=1, id=99
    )
    repo.backdate("s1", "stub", KICK_STALE_SECONDS + 1)
    await queue.run_periodic(JOB_CUBE_KICK, timestamp=1_700_000_000)
    row = repo.rows("s1")[0]
    assert (row.status, row.reason, row.attempts) == ("pending", None, 0)
    assert [j.payload for j in queue.jobs] == [{"cube_sink_id": "s1"}]  # woken the same tick
```

In `tests/test_integration_cubes_repo.py`, delete `test_fail_pending_touches_pending_rows_only_and_never_the_sink`. `test_reset_stub_failures_returns_z3_stub_rows_to_pending` and `test_record_commit_first_commit_is_conditional_and_never_writes_updated_at` (Task 4) now carry its two assertions: pending-only, and never `updated_at`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cube_jobs.py -v`
Expected: FAIL with `ImportError: cannot import name 'CUBE_APPEND_RETRY'`.

- [ ] **Step 3: Rewrite `jobs/cubes.py`**

Replace the module docstring's last bullet (the stub) with:

```
- ``pipeline.cube_append`` runs ``cubes.append.run_cube_append`` (Z-4): it
  claims up to 50 pending rows, parses their headers 4 at a time off the
  event loop, appends them virtually in one Icechunk commit, trims the
  window, records the commit and the ledger, and re-enqueues itself while
  rows remain. Retry: ``CUBE_APPEND_RETRY`` (shares Procrastinate's attempt
  budget with ``retry_stalled``'s cap of 3).
```

and the `cube_kick` bullet's first sentence with: "``pipeline.cube_kick`` (every 5 minutes) first returns Z-3's stub rows (``failed: not_implemented``) to ``pending``, then hands any ``cube_append`` that a dead worker left running back to the queue (``QueueBackend.retry_stalled``), since otherwise it would hold its sink's lock forever."

Then make these code changes:

```python
import dataclasses
import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.cubes.append import AppendDeps, run_cube_append
from pipeline.cubes.icerepo import cube_storage
from pipeline.cubes.repo import CubeRepo, PgCubeRepo
from pipeline.cubes.resolve import PgSourceResolver
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import Enqueued, QueueBackend, RetrySpec
```

Delete `REASON_NOT_IMPLEMENTED` and add:

```python
#: §6: three attempts. RetrySpec waits a fixed time (no exponential form), and
#: Procrastinate counts retry_stalled requeues in the same attempts budget.
CUBE_APPEND_RETRY = RetrySpec(max_attempts=3, wait_seconds=30)
```

In `kick_stale_sinks`, before the `retry_stalled` block:

```python
    try:
        reset = await repo.reset_stub_failures()
    except Exception:
        logger.exception("cube_kick: resetting Z-3 stub rows failed")
    else:
        if reset:
            logger.info("cube_kick returned Z-3 stub rows to pending", extra={"rows": reset})
```

Add `production_append_deps` above `register`, and replace `register`:

```python
def production_append_deps(
    settings: Settings, queue: QueueBackend, repo: CubeRepo
) -> AppendDeps | None:
    """The real seams, or ``None`` without a credential master key (logged by
    ``load_key_or_skip``; the rows wait for the next kick)."""
    master_key = load_key_or_skip(settings, JOB_CUBE_APPEND)
    if master_key is None:
        return None

    async def enqueue_next(cube_sink_id: str) -> Enqueued:
        return await enqueue_cube_append(queue, cube_sink_id)

    return AppendDeps(
        repo=repo,
        resolver=PgSourceResolver.from_settings(settings, master_key),
        storage_for=lambda sink: cube_storage(settings, sink.cube_collection_id),
        enqueue_next=enqueue_next,
    )


def register(
    queue: QueueBackend,
    settings: Settings,
    *,
    repo: CubeRepo | None = None,
    deps_factory: Callable[[], AppendDeps | None] | None = None,
) -> None:
    def _repo() -> CubeRepo:
        return repo if repo is not None else PgCubeRepo(settings.database_url)

    async def cube_append(cube_sink_id: str) -> None:
        deps = (
            deps_factory()
            if deps_factory is not None
            else production_append_deps(settings, queue, _repo())
        )
        if deps is None:
            return
        report = await run_cube_append(cube_sink_id, deps)
        logger.info(
            "cube_append finished",
            extra={"cube_sink_id": cube_sink_id, **dataclasses.asdict(report)},
        )

    async def cube_kick(timestamp: int) -> None:
        kicked = await kick_stale_sinks(_repo(), queue)
        if kicked:
            logger.info(
                "cube_kick re-enqueued sinks with stale pending rows",
                extra={"sinks": kicked, "scheduled_timestamp": timestamp},
            )

    # Default queue: the append reads headers, not bytes (spec §6).
    queue.register_task(cube_append, name=JOB_CUBE_APPEND, retry=CUBE_APPEND_RETRY)
    queue.register_periodic(cube_kick, name=JOB_CUBE_KICK, cron=KICK_CRON)
```

Each job builds a fresh `AppendDeps`, so `PgSourceResolver`'s per-job caches (associations, libraries) never outlive the job.

- [ ] **Step 4: Remove `fail_pending`**

Delete the `fail_pending` abstract method and its `PgCubeRepo` implementation from `cubes/repo.py`, and `fail_pending` from `tests/_cube_fake.py`. Confirm nothing else calls it: `grep -rn fail_pending src tests` returns nothing.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cube_jobs.py tests/test_dispatch_cubes.py tests/test_cube_repo_fake.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

Then run the DB-gated file again against the throwaway cluster from Task 4:

`DATABASE_URL=postgresql://postgres@localhost:5499/z4 uv run pytest tests/test_integration_cubes_repo.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/jobs/cubes.py src/pipeline/cubes/repo.py tests/_cube_fake.py tests/test_cube_jobs.py tests/test_integration_cubes_repo.py
git commit -m "Z-4: wire the real cube_append; cube_kick revisits Z-3 stub rows

Retry 3 x 30 s; a missing master key leaves rows waiting; fail_pending and
the stub are gone.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Live integration test, docs, gates, PR

**Files:**
- Create: `services/pipeline/tests/test_cube_it.py`
- Modify: `docs/FEATURES.md`, `docs/ISSUES.md`

**Interfaces:**
- Consumes: `libs_from_connection` (Task 3), `parse_header`, `step_value` (Task 5), `open_repository`, `BRANCH` (Task 6), `write_batch`, `ParsedStep` (Task 7).

- [ ] **Step 1: Write the integration test (skipped unless `CUBE_IT=1`)**

`tests/test_cube_it.py`:

```python
"""Z-4 live check: real NODD GOES-19 C13 files appended virtually to a cube on S3.

Skipped unless CUBE_IT=1. Needs network to NODD and a throwaway S3-compatible
store for the cube (a z4-prefixed Silo, never the compose stack's):

    CUBE_IT=1 CUBE_IT_ENDPOINT=http://localhost:19000 CUBE_IT_BUCKET=probe \\
    CUBE_IT_KEY=minioadmin CUBE_IT_SECRET=minioadmin \\
    uv run pytest tests/test_cube_it.py -v
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import os
import uuid

import h5py
import icechunk as ic
import numpy as np
import obstore
import pytest
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import S3Store

from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.icerepo import BRANCH, open_repository
from pipeline.cubes.source import libs_from_connection
from pipeline.cubes.steps import parse_header, step_value
from pipeline.cubes.write import ParsedStep, write_batch

pytestmark = pytest.mark.skipif(os.environ.get("CUBE_IT") != "1", reason="set CUBE_IT=1")

NODD = ConnectionRow(
    id="nodd", name="nodd", protocol="s3",
    config={"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True},
    credentials=None, host_key=None,
)
CONFIG = parse_cube_sink_config(
    {
        "parser": "hdf5",
        "append_dim": "t",
        "variables": ["CMI", "DQF"],
        "loadable_variables": ["t", "x", "y", "goes_imager_projection"],
        "window": {"max_steps": 2},
    }
)


def _attr(value):
    """An HDF5 attribute as CF decoding wants it: a scalar, bytes as str."""
    if not isinstance(value, bytes | str):
        value = np.ravel(value)[0]
    return value.decode() if isinstance(value, bytes) else value


def _newest_c13_keys(store, n: int) -> list[str]:
    now = dt.datetime.now(dt.UTC)
    keys: list[str] = []
    for hours in range(3):
        t = now - dt.timedelta(hours=hours)
        for batch in obstore.list(store, prefix=f"ABI-L2-CMIPC/{t:%Y/%j/%H}/", chunk_size=1000):
            keys += [o["path"] for o in batch if "M6C13" in o["path"]]
    return sorted(keys)[-n:]


async def test_nodd_files_append_virtually_and_read_back():
    libs = libs_from_connection(NODD, frozenset())
    keys = _newest_c13_keys(libs.store, 3)
    assert len(keys) == 3
    registry = ObjectStoreRegistry({libs.registry_key: libs.store})
    parsed = []
    for i, key in enumerate(keys):
        meta = await obstore.head_async(libs.store, key)
        step = await asyncio.to_thread(parse_header, libs.url(key), registry, CONFIG)
        name, t = key.rsplit("/", 1)[1], step_value(step, "t")
        parsed.append(ParsedStep(i, name, step, t, meta["last_modified"]))

    endpoint = os.environ["CUBE_IT_ENDPOINT"]
    bucket, prefix = os.environ["CUBE_IT_BUCKET"], f"z4-it-{uuid.uuid4().hex[:8]}/_cube"
    creds = {
        "access_key_id": os.environ["CUBE_IT_KEY"],
        "secret_access_key": os.environ["CUBE_IT_SECRET"],
    }
    storage = ic.s3_storage(
        bucket=bucket, prefix=prefix, endpoint_url=endpoint, region="us-east-1",
        allow_http=endpoint.startswith("http://"), force_path_style=True, **creds,
    )
    repo = open_repository(storage, [libs], replace_containers=True)
    result = write_batch(repo, parsed, CONFIG, dt.datetime.now(dt.UTC))
    # Real files from one sector share the grid check: none is skipped.
    assert set(result.outcomes.values()) == {("appended", None)}
    assert result.committed and result.trimmed == 1
    assert list(ic.Repository.fetch_config(storage).virtual_chunk_containers) == ["s3://noaa-goes19/"]

    ds = xr.open_zarr(repo.readonly_session(BRANCH).store, consolidated=False, zarr_format=3)
    assert ds.sizes["t"] == 2
    virtual = float(ds["CMI"].isel(t=-1, y=750, x=1250).values)
    raw = obstore.get(libs.store, keys[-1]).bytes()
    with h5py.File(io.BytesIO(bytes(raw)), "r") as f:
        cmi = f["CMI"]
        attrs = {
            k: _attr(cmi.attrs[k])
            for k in ("scale_factor", "add_offset", "_FillValue", "_Unsigned")
            if k in cmi.attrs
        }
        # The same CF decoding xarray applies to the virtual read.
        pixel = xr.Variable((), cmi[750, 1250], attrs=attrs)
        direct = float(xr.conventions.decode_cf_variable("CMI", pixel).values)
    assert virtual == pytest.approx(direct, rel=1e-6, nan_ok=True)  # the same pixel, read virtually

    cube_store = S3Store(
        bucket=bucket, endpoint=endpoint, region="us-east-1", virtual_hosted_style_request=False,
        client_options={"allow_http": endpoint.startswith("http://")}, **creds,
    )
    repo_bytes = sum(o["size"] for batch in obstore.list(cube_store, prefix=prefix) for o in batch)
    nodd_bytes = (await obstore.head_async(libs.store, keys[-1]))["size"]
    assert repo_bytes < nodd_bytes  # no source bytes copied
```

Run: `uv run pytest tests/test_cube_it.py -v`
Expected: `1 skipped` (no `CUBE_IT`). Running it for real is lead-only (#90 "Lead-only steps").

- [ ] **Step 2: Docs**

In `docs/FEATURES.md`, under "Virtual Icechunk cube sink (Z queue)", add after the Z-3 row:

```markdown
| Z-4 · `cube_append` + pipeline deps | ✅ | `pipeline.cube_append` (default queue, retry 3 × 30 s) claims ≤ 50 pending rows (`attempts` bumped first; > 6 → `failed: crash_loop`), resolves each item's one HDF source href through its reference association (`connections/sources.py::association_for_href`, shared with process staging), HEADs then parses 4 headers at a time off the event loop, and appends them virtually to `assets/{cube}/_cube/` (Icechunk, `num_updates_per_repo_info_file = 100`, one container per source bucket, explicit path-style endpoint after `resolve_pinned`). Duplicate `t` → `appended/duplicate`, late → `skipped`, layout mismatch → `skipped: unsupported_layout`; window trimmed in the same commit; `ConflictError` redone once. The first commit is recorded only while the sink keeps the app version read (#90); an unrecorded repository is provisional and reset to its root snapshot before writing. `cube_kick` returns Z-3 stub rows to pending. Deps: icechunk, virtualizarr[hdf], zarr, xarray, obstore, h5py, obspec-utils. Z-5's asset writer plugs into `AppendDeps.after_batch` (after every batch that reached the cube; must be idempotent). A grid or projection change is `skipped: unsupported_layout` (an append would rewrite `x`/`y`). An exception gives the claimed attempts back, so outages never become `crash_loop` |
```

Also edit the Z-3 row's last sentence, "`cube_append` is a stub (`failed: not_implemented`) until Z-4", to "`cube_append` was a stub until Z-4".

In `docs/ISSUES.md`, add the next free I-number (I-144 if still free; check the last entry first):

```markdown
### I-144 · Cube ledger rows can name a snapshot that no longer holds them 🟢

Tracked in: —

While a sink has no recorded snapshot, its repository is provisional (Z-4): a
job that finds unrecorded data resets `main` to the root snapshot before
writing. If a stalled-job requeue starts a second `cube_append` while the
first is still committing (a job blocking its event loop > 300 s, #90), the
first run's rows can be finished `appended` with a snapshot id the second run
then reset away. The cube's data and `cube_sinks.last_snapshot_id` are
correct: the second run rewrites the steps and records its own tip. Only
those rows' `snapshot_id` names an orphaned snapshot.

Related, same severity:
- After the first commit, `record_commit` is unconditional. In a double run,
  the slower recorder can move `cube_sinks.last_snapshot_id` back to an older
  snapshot until the next commit records the tip.
- Rows appended and trimmed in the same commit read `appended` with a
  snapshot that no longer holds them. After crash recovery, such rows read
  `late`.
- A sink deleted while its repository is provisional (no recorded snapshot)
  leaves the repository in storage until the cube collection is deleted
  (`asset_gc`).

Accepted for v1; revisit if the ledger's `snapshot_id` ever drives a reader.
```

- [ ] **Step 3: The gates**

```bash
cd services/pipeline && uv run pytest -q && uv run ruff check .
cd ../.. && npm run verify
```

Expected: pytest green (the `CUBE_IT` test and the DB-gated tests skip without their env), ruff clean, verify green. Run the DB-gated files once more against the throwaway cluster, then stop and remove it:

```bash
DATABASE_URL=postgresql://postgres@localhost:5499/z4 uv run pytest tests/test_integration_cubes_repo.py -v
pg_ctl -D "$SP/pg" stop
```

- [ ] **Step 4: Commit**

```bash
git add services/pipeline/tests/test_cube_it.py docs/FEATURES.md docs/ISSUES.md
git commit -m "Z-4: live NODD/Silo integration test (CUBE_IT=1), FEATURES row, I-144

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: PR (lead pushes; teammates stop here)**

`git push -u origin feat/z4-cube-append`, then `gh pr create --base main --title "Z-4: cube_append: virtual appends with a rolling window"` with a body that:
- starts `Closes #90`;
- lists the gates run (pytest, ruff, verify, and the DB-gated cubes repo tests against a throwaway Postgres);
- lists every item under "Decisions this plan takes" as the deviations to review, and asks the lead to decide decision 10 (keep or drop the stub-row reset);
- lists the lead-only steps left:
  1. `CUBE_IT=1` against a throwaway, `z4`-prefixed Silo (#90).
  2. The pipeline image size delta: `docker image ls` before/after on the CI-built image, or `docker compose build pipeline` locally. Record it in the PR body (spec §11).
  3. The image passes the C-queue scan in CI (`containers.yml`, Trivy HIGH/CRITICAL, informational).
  4. An import and parse smoke test **in the built Linux image**. h5py and rasterio each bundle their own libhdf5, and one worker process loads both: `docker compose run --rm pipeline python -c "import rasterio, h5py, virtualizarr, icechunk; print('ok')"`, then parse one live GOES header with `pipeline.cubes.steps.parse_header`. The unit suite ran on macOS only.
- ends with the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

On merge: flip #91 (Z-5) and #92 (Z-6) from `blocked` to `ready` if Z-4 was their last blocker, and remove the worktree.

---

## Self-review notes (for the reviewer of this plan)

- **Spec coverage:**

  | Spec item | Task |
  |---|---|
  | §6.1 sources, association, `no_source_connection`, explicit endpoint, `resolve_pinned`, `EgressBlocked` ⇒ failed | 2, 3, 8 |
  | §6.2-1 lock | Z-3, unchanged |
  | §6.2-2 open/create, history 100, per-bucket containers, `source_prefixes` | 6, 9 |
  | §6.2-3 tip | 6 |
  | §6.2-4 ≤ 50 rows, 4 concurrent parses | 9 |
  | §6.2-5 duplicate / late / layout / `to_icechunk` with `last_updated_at` | 7 |
  | §6.2-6 window | 5, 7 |
  | §6.2-7 commit, conflict redo once, never rebase | 7 |
  | §6.2-8 ledger + `last_*` | 4, 9 |
  | §6.2-9 asset writer hook | 9 |
  | self re-enqueue | 9 |
  | retry 3 | 10 |
  | §11 dependencies | 1 |
  | #90 comments: first-commit window | 4, 9 |
  | #90 comments: stub rows | 10 |
  | #90 comments: crash loop | 4, 9 |
  | #90 comments: off-loop parsing | 9 |
  | #90 comments: double-run safety | 7, 9 |
  | `CUBE_IT` integration test | 11 |

- **Not in this slice:** the collection asset writer (Z-5; only the `after_batch` hook), `cube_maintain` / GC / expiry (Z-6), the cube server (Z-7), the UI (Z-8).
